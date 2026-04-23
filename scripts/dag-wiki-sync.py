#!/usr/bin/env python3
"""
DAG → Wiki 同步脚本
将 DAG 中的 D1/D2 摘要导出为可读的 Wiki 文档

运行方式:
  python3 ~/.hermes/scripts/dag-wiki-sync.py

由 cronjob 定期调用 (hourly)
"""

import json
import os
import sys
from datetime import datetime

DAG_DIR = os.path.expanduser("~/.hermes/dag")
WIKI_DIR = os.path.expanduser("/mnt/d/.hermes/hermes-agent/wiki/concept")
SYNC_LOG = os.path.expanduser("~/.hermes/scripts/.dag-wiki-sync.log")

def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    with open(SYNC_LOG, "a") as f:
        f.write(line + "\n")

def load_dag_index():
    idx_path = os.path.join(DAG_DIR, "index.json")
    if not os.path.exists(idx_path):
        log(f"ERROR: index.json not found at {idx_path}")
        return None
    with open(idx_path) as f:
        return json.load(f)

def load_node(node_id):
    node_path = os.path.join(DAG_DIR, "nodes", f"{node_id}.json")
    if not os.path.exists(node_path):
        return None
    with open(node_path) as f:
        return json.load(f)

def format_node(node, node_id):
    """Format a single DAG node as markdown."""
    depth_labels = {0: "D0 (原始)", 1: "D1 (摘要)", 2: "D2 (全局摘要)"}
    depth_label = depth_labels.get(node.get("depth", 0), f"D{node.get('depth', '?')}")
    created = node.get("created_at", "unknown")
    token_count = node.get("token_count", 0)
    content = node.get("content", "")
    parent_ids = node.get("parent_ids", [])

    lines = [
        f"## {node_id[:8]}… — {depth_label}",
        f"",
        f"- **创建时间**: {created}",
        f"- **Token 数**: ~{token_count}",
        f"- **父节点**: {', '.join(p[:8] + '…' for p in parent_ids) if parent_ids else '无'}",
        f"",
        f"```",
        f"{content[:500]}{'...' if len(content) > 500 else ''}",
        f"```",
        f"",
    ]
    return "\n".join(lines)

def build_summaries_doc(index):
    """Build the DAG-Summaries.md document from current DAG state."""
    nodes = index.get("nodes", {})
    d0_ids = index.get("d0_ids", [])
    d1_ids = index.get("d1_ids", [])
    d2_ids = index.get("d2_ids", [])

    # Load D1 and D2 nodes sorted by creation time
    d1_nodes = []
    for nid in d1_ids:
        n = load_node(nid)
        if n:
            n["node_id"] = nid
            d1_nodes.append(n)

    d2_nodes = []
    for nid in d2_ids:
        n = load_node(nid)
        if n:
            n["node_id"] = nid
            d2_nodes.append(n)

    d1_nodes.sort(key=lambda x: x.get("created_at", ""))
    d2_nodes.sort(key=lambda x: x.get("created_at", ""))

    lines = [
        "---",
        "created: " + datetime.now().strftime("%Y-%m-%d"),
        "updated: " + datetime.now().strftime("%Y-%m-%d %H:%M"),
        "type: concept",
        'tags: ["dag", "summaries", "context-management"]',
        'summary: "DAG 分层摘要文档 — 由 dag-wiki-sync.py 自动生成"',
        "---",
        "",
        "# DAG 分层摘要概览",
        "",
        f"**同步时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "## 统计",
        "",
        f"| 层 | 节点数 |",
        f"|----|--------|",
        f"| D0 (原始消息) | {len(d0_ids)} |",
        f"| D1 (会话摘要) | {len(d1_ids)} |",
        f"| D2 (全局摘要) | {len(d2_ids)} |",
        f"| **总计** | {len(nodes)} |",
        "",
    ]

    if d1_nodes:
        lines += [
            "## D1 会话摘要",
            "",
        ]
        for n in d1_nodes:
            lines.append(format_node(n, n["node_id"]))

    if d2_nodes:
        lines += [
            "## D2 全局摘要",
            "",
        ]
        for n in d2_nodes:
            lines.append(format_node(n, n["node_id"]))

    if not d1_nodes and not d2_nodes:
        lines += [
            "_（暂无摘要节点）_",
            "",
        ]

    lines += [
        "---",
        f"_由 dag-wiki-sync.py 自动生成 · DAG 节点总数: {len(nodes)}_",
    ]

    return "\n".join(lines)

def main():
    log("=== DAG → Wiki 同步开始 ===")

    index = load_dag_index()
    if not index:
        log("ABORTED: 无法加载 DAG index")
        sys.exit(1)

    nodes = index.get("nodes", {})
    log(f"DAG 状态: {len(nodes)} 节点 "
        f"(D0={len(index.get('d0_ids',[]))}, "
        f"D1={len(index.get('d1_ids',[]))}, "
        f"D2={len(index.get('d2_ids',[]))})")

    # Ensure wiki directory exists
    os.makedirs(WIKI_DIR, exist_ok=True)

    # Build and write summaries doc
    summaries_doc = build_summaries_doc(index)
    out_path = os.path.join(WIKI_DIR, "DAG-Summaries.md")
    with open(out_path, "w") as f:
        f.write(summaries_doc)
    log(f"已写入: {out_path}")

    # Update the main DAG concept doc with updated status
    concept_path = os.path.join(WIKI_DIR, "DAG-上下文管理实现方案.md")
    if os.path.exists(concept_path):
        with open(concept_path) as f:
            content = f.read()
        # Update Phase 4.2 line
        marker = "### Phase 4: 与现有系统集成"
        if marker in content:
            old_line = "- [ ] AgentMemory MCP 连接"
            new_line = "- [x] AgentMemory MCP 连接（DAG index → Wiki 同步脚本已创建）"
            if old_line in content:
                content = content.replace(old_line, new_line)
                with open(concept_path, "w") as f:
                    f.write(content)
                log(f"已更新 Phase 状态: {concept_path}")
        log(f"DAG 概念文档已检查: {concept_path}")
    else:
        log(f"WARNING: 概念文档不存在: {concept_path}")

    log("=== DAG → Wiki 同步完成 ===")

if __name__ == "__main__":
    main()
