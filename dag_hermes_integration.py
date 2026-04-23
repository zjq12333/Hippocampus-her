"""
DAG-Hermes Integration Layer
==============================
Integrates DAG context manager into Hermes Agent's flush_memories and
compress_context flows.

Phase 2: LLM-powered summarization (D0 → D1)
"""

import json
import time
import uuid
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Add dag_context to path
import sys
sys.path.insert(0, str(Path.home() / ".hermes"))
from dag_context import DAGContextManager, ContextNode, estimate_tokens

# Compression summarization prompt
SUMMARIZE_PROMPT = """你是一个会话记忆提炼助手。请将以下对话片段提炼成一个简洁的摘要，保留关键信息、用户偏好、决策结论和待办事项。

要求：
- 保留具体的用户名、项目名、文件路径等细节
- 保留明确的决策结论
- 保留未完成的待办事项
- 删除重复的寒暄和无关细节
- 中文输出，200字以内

对话片段：
{content}

摘要："""

# D1 → D2 summarization prompt (for deeper compression)
SUMMARIZE_D2_PROMPT = """你是一个全局会话概览提炼助手。请将以下多个会话片段摘要合并成一个全局概览。

要求：
- 提取跨会话的主题和模式
- 保留长期目标和项目状态
- 保留用户的重要偏好和习惯
- 中文输出，150字以内

会话摘要片段：
{content}

全局概览："""


class DAGHermesIntegration:
    """
    Integrates DAG context manager with Hermes Agent.

    Usage:
        dag_hermes = DAGHermesIntegration()
        dag_hermes.on_message(role="user", content="Hello")  # Add D0 node
        summary_node = dag_hermes.compress_if_needed_llm(llm_call_fn)  # D0→D1 if needed
    """

    def __init__(self):
        self.dag = DAGContextManager()
        self._enabled = True
        self._session_start_d0_count = 0  # Track D0 count at session start

    def on_message(self, role: str, content: str) -> ContextNode:
        """Call this for each new conversation message to create a D0 node."""
        if not self._enabled:
            return None
        if role not in ("user", "assistant", "system"):
            role = "system"
        node = self.dag.append_message(content, role=role)
        return node

    def compress_if_needed_llm(
        self,
        llm_call_fn,
        min_turns: int = 2,
    ) -> Optional[ContextNode]:
        """
        Check if DAG compression is needed and generate D1 summary using LLM.

        Args:
            llm_call_fn: A callable that takes (prompt: str) and returns str (summary)
            min_turns: Minimum conversation turns before compression (default 2)

        Returns:
            The created D1 ContextNode if compression was performed, None otherwise.
        """
        if not self._enabled:
            return None

        # Check minimum turns (each turn = user + assistant = 2 messages)
        d0_count = len(self.dag.index.d0_ids) - self._session_start_d0_count
        if d0_count < min_turns * 2:
            return None

        # Check if compression is needed
        result = self.dag.compress_if_needed()
        if not result:
            return None

        source_content, source_node_ids = result

        # Generate D1 summary using LLM
        prompt = SUMMARIZE_PROMPT.format(content=source_content)
        try:
            summary_text = llm_call_fn(prompt)
            if not summary_text or len(summary_text.strip()) < 10:
                logger.warning("LLM returned empty summary, skipping D1 creation")
                return None
        except Exception as e:
            logger.warning("LLM summarization failed: %s", e)
            return None

        # Create D1 summary node
        d1_node = self.dag.create_summary_node(
            source_content=source_content,
            summary_text=summary_text.strip(),
            source_node_ids=source_node_ids,
            depth=1,
        )

        logger.info(
            "DAG D1 compression: %d D0 nodes → 1 D1 node (%d tokens → %d tokens)",
            len(source_node_ids),
            sum(self.dag.index.nodes[n]["token_count"] for n in source_node_ids),
            d1_node.token_count,
        )
        return d1_node

    def build_context_for_llm(self, max_tokens: int = 4000) -> list[dict]:
        """
        Build a message list suitable for LLM injection from the DAG.
        Uses D1/D2 summaries + fresh D0 tail.
        """
        nodes = self.dag.build_context_window(max_tokens=max_tokens)
        messages = []
        for node in nodes:
            messages.append({
                "role": node.role,
                "content": node.content,
            })
        return messages

    def search_dag(self, query: str) -> list[tuple[str, str]]:
        """Search across all DAG nodes."""
        return self.dag.search_all_nodes(query)

    def expand_summary(self, node_id: str) -> list[ContextNode]:
        """Expand a summary node back to its D0 source nodes."""
        return self.dag.expand_to_d0(node_id)

    def get_stats(self) -> dict:
        """Return DAG statistics."""
        return self.dag.get_stats()

    def start_session(self):
        """Mark session start — used to track D0 count per session."""
        self._session_start_d0_count = len(self.dag.index.d0_ids)

    def is_enabled(self) -> bool:
        return self._enabled

    def set_enabled(self, enabled: bool):
        self._enabled = enabled


# Default instance (lazy loaded)
_default_integration: Optional[DAGHermesIntegration] = None


def get_dag_integration() -> DAGHermesIntegration:
    """Get or create the default DAG integration instance."""
    global _default_integration
    if _default_integration is None:
        _default_integration = DAGHermesIntegration()
    return _default_integration
