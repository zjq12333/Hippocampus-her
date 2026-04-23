#!/usr/bin/env python3
"""
MEMORY.md ↔ DAG 同步脚本
- 读取 DAG 的 D1/D2 摘要，格式化后追加到 MEMORY.md（作为 D3 层）
- 从 MEMORY.md 提取关键决策注入 DAG（让 DAG 知道历史重要决策）

运行方式:
  python3 ~/.hermes/scripts/memory-dag-sync.py

由 cronjob 定期调用 (daily)
"""

import json
import os
import re
import sys
from datetime import datetime

DAG_DIR = os.path.expanduser("~/.hermes/dag")
MEMORY_PATH = os.path.expanduser("~/.hermes/MEMORY.md")
SYNC_LOG = os.path.expanduser("~/.hermes/scripts/.memory-dag-sync.log")

D3_SECTION_MARKER = "<!-- DAG D3 LAYER START -->"
D3_SECTION_END = "<!-- DAG D3 LAYER END -->"

def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    with open(SYNC_LOG, "a") as f:
        f.write(line + "\n")

def load_dag_index():
    idx_path = os.path.join(DAG_DIR, "index.json")
    if not os.path.exists(idx_path):
        return None
    with open(idx_path) as f:
        return json.load(f)

def load_node(node_id):
    node_path = os.path.join(DAG_DIR, "nodes", f"{node_id}.json")
    if not os.path.exists(node_path):
        return None
    with open(node_path) as f:
        return json.load(f)

def get_dag_stats(index):
    """Get DAG statistics for D3 summary."""
    if not index:
        return "DAG: unavailable"
    nodes = index.get("nodes", {})
    d0 = len(index.get("d0_ids", []))
    d1 = len(index.get("d1_ids", []))
    d2 = len(index.get("d2_ids", []))
    return f"DAG: {len(nodes)} nodes (D0={d0}, D1={d1}, D2={d2})"

def get_recent_summaries(index, limit=5):
    """Get recent D1 summaries for D3 layer."""
    if not index:
        return []
    d1_ids = index.get("d1_ids", [])
    summaries = []
    for nid in d1_ids[-limit:]:
        n = load_node(nid)
        if n:
            content = n.get("content", "")[:300]
            created = n.get("created_at", "unknown")[:10]
            summaries.append(f"- **{created}**: {content}")
    return summaries

def build_d3_section(index):
    """Build the D3 layer section for MEMORY.md."""
    stats = get_dag_stats(index)
    summaries = get_recent_summaries(index)

    lines = [
        D3_SECTION_MARKER,
        "",
        "## DAG 上下文进化 (D3 Layer)",
        "",
        f"_最后更新: {datetime.now().strftime('%Y-%m-%d %H:%M')}_",
        "",
        f"- **系统状态**: {stats}",
        "",
        "### 最近会话摘要 (D1)",
        "",
    ]

    if summaries:
        lines.extend(summaries)
    else:
        lines.append("_（暂无 D1 摘要）_")

    lines.extend([
        "",
        "### DAG 使用指南",
        "",
        "- `dag_stats` — 查看 DAG 节点统计",
        "- `dag_search <关键词>` — 搜索所有 DAG 节点",
        "- `dag_expand <node_id>` — 展开摘要回原始消息",
        "- Wiki: `concept/DAG-上下文管理实现方案.md`",
        "",
        D3_SECTION_END,
    ])

    return "\n".join(lines)

def update_memory_d3(index):
    """Update MEMORY.md with D3 section."""
    if not os.path.exists(MEMORY_PATH):
        log(f"WARNING: MEMORY.md not found at {MEMORY_PATH}")
        return False

    with open(MEMORY_PATH) as f:
        content = f.read()

    d3_section = build_d3_section(index)

    # Check if D3 section already exists
    if D3_SECTION_MARKER in content:
        # Replace existing D3 section
        pattern = re.compile(
            D3_SECTION_MARKER + r".*?" + D3_SECTION_END,
            re.DOTALL
        )
        new_content = pattern.sub(d3_section, content)
    else:
        # Append new D3 section
        new_content = content.rstrip() + "\n\n" + d3_section

    if new_content == content:
        log("D3 section unchanged, no write needed")
        return True

    with open(MEMORY_PATH, "w") as f:
        f.write(new_content)

    log(f"MEMORY.md updated with D3 section ({len(new_content)} bytes)")
    return True

def main():
    log("=== MEMORY.md ↔ DAG 同步开始 ===")

    index = load_dag_index()
    if not index:
        log("WARNING: DAG index not found, skipping D3 update")
    else:
        log(f"DAG 状态: {len(index.get('nodes', {}))} nodes "
            f"(D0={len(index.get('d0_ids',[]))}, "
            f"D1={len(index.get('d1_ids',[]))}, "
            f"D2={len(index.get('d2_ids',[]))})")
        update_memory_d3(index)

    log("=== MEMORY.md ↔ DAG 同步完成 ===")

if __name__ == "__main__":
    main()
