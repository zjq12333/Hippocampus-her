"""
DAG-based Context Management for Hermes
========================================
Implements hierarchical summarization (D0→D1→D2) inspired by Lossless Claw.

Core idea:
- D0: Raw messages (always preserved, never deleted)
- D1: Session fragment summaries (compressed from D0)
- D2: Global topic summaries (compressed from D1)
- DAG allows multi-parent nodes (one summary can reference multiple fragments)
"""

import json
import os
import time
import uuid
from dataclasses import dataclass, asdict, field
from datetime import datetime
from pathlib import Path
from typing import Optional

DAG_DIR = Path.home() / ".hermes" / "dag"
NODES_DIR = DAG_DIR / "nodes"
INDEX_FILE = DAG_DIR / "index.json"
TAIL_LOCK_FILE = DAG_DIR / "tail.lock"

# Thresholds
TAIL_LOCK_TOKENS = 2000  # Last ~2000 tokens are always D0 (fresh tail protection)
D1_THRESHOLD_TOKENS = 5000  # Compress D0 → D1 when D0 exceeds this
D2_THRESHOLD_TOKENS = 3000  # Compress D1 → D2 when D1 exceeds this

# Token estimation (rough: 1 token ≈ 4 chars for Chinese+English mix)
def estimate_tokens(text: str) -> int:
    return len(text) // 4


@dataclass
class ContextNode:
    """A node in the DAG — can be a raw message (D0) or a summary (D1/D2)."""
    node_id: str
    depth: int  # 0=D0, 1=D1, 2=D2
    parent_ids: list[str] = field(default_factory=list)
    content: str = ""
    created_at: str = ""  # ISO format
    token_count: int = 0
    role: str = "user"  # user / assistant / system

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now().isoformat()
        if not self.token_count:
            self.token_count = estimate_tokens(self.content)
        if not self.node_id:
            self.node_id = str(uuid.uuid4())[:8]

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "ContextNode":
        return cls(**d)

    def save(self):
        """Save node to disk."""
        NODES_DIR.mkdir(parents=True, exist_ok=True)
        path = NODES_DIR / f"{self.node_id}.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)

    @classmethod
    def load(cls, node_id: str) -> "ContextNode":
        path = NODES_DIR / f"{node_id}.json"
        if not path.exists():
            raise FileNotFoundError(f"Node {node_id} not found")
        with open(path, "r", encoding="utf-8") as f:
            return cls.from_dict(json.load(f))

    def delete(self):
        """Remove node from disk."""
        path = NODES_DIR / f"{self.node_id}.json"
        if path.exists():
            path.unlink()


@dataclass
class DAGIndex:
    """The index tracks the current state of the DAG."""
    nodes: dict[str, dict] = field(default_factory=dict)  # node_id → node summary
    tail_ids: list[str] = field(default_factory=list)  # D0 nodes in fresh tail
    d0_ids: list[str] = field(default_factory=list)  # all D0 node IDs (oldest first)
    d1_ids: list[str] = field(default_factory=list)  # all D1 node IDs
    d2_ids: list[str] = field(default_factory=list)  # all D2 node IDs
    total_tokens: int = 0

    def save(self):
        """Save index to disk."""
        DAG_DIR.mkdir(parents=True, exist_ok=True)
        with open(INDEX_FILE, "w", encoding="utf-8") as f:
            json.dump(asdict(self), f, ensure_ascii=False, indent=2)

    @classmethod
    def load(cls) -> "DAGIndex":
        if not INDEX_FILE.exists():
            return cls()
        with open(INDEX_FILE, "r", encoding="utf-8") as f:
            return cls(**json.load(f))

    def add_node(self, node: ContextNode):
        """Register a node in the index."""
        self.nodes[node.node_id] = {
            "depth": node.depth,
            "token_count": node.token_count,
            "created_at": node.created_at,
            "role": node.role,
        }
        if node.depth == 0:
            self.d0_ids.append(node.node_id)
            self.tail_ids.append(node.node_id)
        elif node.depth == 1:
            self.d1_ids.append(node.node_id)
        elif node.depth == 2:
            self.d2_ids.append(node.node_id)
        self.total_tokens += node.token_count

    def remove_node(self, node_id: str):
        """Unregister a node from the index."""
        if node_id in self.nodes:
            info = self.nodes[node_id]
            depth = info["depth"]
            self.total_tokens -= info["token_count"]
            del self.nodes[node_id]
            if depth == 0:
                self.d0_ids = [n for n in self.d0_ids if n != node_id]
                self.tail_ids = [n for n in self.tail_ids if n != node_id]
            elif depth == 1:
                self.d1_ids = [n for n in self.d1_ids if n != node_id]
            elif depth == 2:
                self.d2_ids = [n for n in self.d2_ids if n != node_id]


class DAGContextManager:
    """
    Manages the DAG of conversation context.

    Usage:
        dag = DAGContextManager()
        dag.append_message("Hello", role="user")      # D0 node
        dag.compress_if_needed()                       # May trigger D0→D1
        dag.build_context_window(max_tokens=4000)      # Returns context for LLM
    """

    def __init__(self):
        self.index = DAGIndex.load()

    def append_message(self, content: str, role: str = "user") -> ContextNode:
        """Append a raw message (D0 node)."""
        node = ContextNode(
            node_id=str(uuid.uuid4())[:8],
            depth=0,
            content=content,
            role=role,
            token_count=estimate_tokens(content),
        )
        node.save()
        self.index.add_node(node)
        self.index.save()
        return node

    def compress_if_needed(self) -> Optional[tuple[str, list[ContextNode]]]:
        """
        Check if compression is needed and perform it.
        Returns (source_content, source_node_ids) if compression is needed, None otherwise.
        The caller should call LLM to generate summary from source_content.
        """
        # Calculate tokens in tail (all D0 nodes currently)
        tail_token_count = sum(
            self.index.nodes[nid]["token_count"]
            for nid in self.index.d0_ids  # All D0 nodes contribute to tail
        )

        # If D0 exceeds threshold, compress oldest D0 → D1
        if tail_token_count > D1_THRESHOLD_TOKENS:
            return self._compress_d0_to_d1()

        return None

    def _compress_d0_to_d1(self) -> Optional[tuple[str, list[str]]]:
        """Compress oldest D0 nodes into a D1 summary node."""
        # Find D0 nodes — always compress from oldest (first in d0_ids)
        available_d0 = self.index.d0_ids[:]  # oldest first
        if not available_d0:
            return None

        # Group oldest D0s for compression (up to ~4000 tokens)
        nodes_to_compress = []
        total = 0
        for nid in available_d0:
            tc = self.index.nodes[nid]["token_count"]
            if total + tc <= D1_THRESHOLD_TOKENS:
                nodes_to_compress.append(ContextNode.load(nid))
                total += tc
            else:
                break

        if len(nodes_to_compress) < 2:
            return None  # Need at least 2 nodes to compress

        # Build source content for summarization
        source_content = "\n".join(
            f"[{n.role}] {n.content}" for n in nodes_to_compress
        )

        # Mark source nodes as no longer in tail
        for nid in [n.node_id for n in nodes_to_compress]:
            if nid in self.index.tail_ids:
                self.index.tail_ids.remove(nid)

        self.index.save()

        return (source_content, [n.node_id for n in nodes_to_compress])

    def create_summary_node(
        self,
        source_content: str,
        summary_text: str,
        source_node_ids: list[str],
        depth: int,
    ) -> ContextNode:
        """
        Create a summary node at the given depth.
        Caller should call LLM to generate summary_text from source_content.
        """
        node = ContextNode(
            node_id=str(uuid.uuid4())[:8],
            depth=depth,
            parent_ids=source_node_ids,
            content=summary_text,
            role="summary",
            token_count=estimate_tokens(summary_text),
        )
        node.save()
        self.index.add_node(node)
        self.index.save()
        return node

    def build_context_window(self, max_tokens: int = 4000) -> list[ContextNode]:
        """
        Build a context window for LLM injection.
        Strategy: Start from D2/D1, add D0 tail, respect max_tokens.
        """
        result: list[ContextNode] = []
        total = 0

        # Add D2 nodes first (highest level summaries)
        for nid in reversed(self.index.d2_ids):
            node = ContextNode.load(nid)
            if total + node.token_count <= max_tokens:
                result.insert(0, node)
                total += node.token_count

        # Add D1 nodes
        for nid in reversed(self.index.d1_ids):
            node = ContextNode.load(nid)
            if total + node.token_count <= max_tokens:
                result.insert(0, node)
                total += node.token_count

        # Add D0 tail (fresh messages, never compressed)
        for nid in reversed(self.index.tail_ids):
            node = ContextNode.load(nid)
            if total + node.token_count <= max_tokens:
                result.insert(0, node)
                total += node.token_count

        return result

    def expand_to_d0(self, node_id: str) -> list[ContextNode]:
        """
        Expand a summary node down to its D0 source nodes.
        Recursively expands until D0 is reached.
        """
        node = ContextNode.load(node_id)

        if node.depth == 0:
            return [node]

        results = []
        for parent_id in node.parent_ids:
            results.extend(self.expand_to_d0(parent_id))
        return results

    def search_all_nodes(self, query: str) -> list[tuple[str, str]]:
        """
        Search across all nodes in the DAG (D0 + D1 + D2).
        Returns list of (node_id, matching_content).
        Simple text search for now.
        """
        results = []
        for nid in list(self.index.nodes.keys()):
            try:
                node = ContextNode.load(nid)
                if query.lower() in node.content.lower():
                    results.append((nid, node.content[:200]))
            except Exception:
                continue
        return results

    def get_stats(self) -> dict:
        """Return DAG statistics."""
        return {
            "total_nodes": len(self.index.nodes),
            "d0_count": len(self.index.d0_ids),
            "d1_count": len(self.index.d1_ids),
            "d2_count": len(self.index.d2_ids),
            "tail_count": len(self.index.tail_ids),
            "total_tokens": self.index.total_tokens,
        }


# CLI interface for testing
if __name__ == "__main__":
    import sys

    dag = DAGContextManager()
    stats = dag.get_stats()
    print(f"DAG Stats: {stats}")
