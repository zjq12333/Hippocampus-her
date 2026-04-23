#!/usr/bin/env python3
"""
Wiki → Obsidian 单向同步脚本
将 hermes-agent wiki 中的核心文档同步到 Obsidian vault

运行方式:
  python3 ~/.hermes/scripts/wiki-obsidian-sync.py

由 cronjob 定期调用 (hourly)
"""

import json
import os
import shutil
import sys
from datetime import datetime

WIKI_DIR = os.path.expanduser("/mnt/d/.hermes/hermes-agent/wiki")
OBSIDIAN_DIR = os.path.expanduser("/mnt/d/obsidian/memory/wiki")
SYNC_LOG = os.path.expanduser("~/.hermes/scripts/.wiki-obsidian-sync.log")

# 要同步的文件/目录列表
SYNC_PATHS = [
    # DAG 相关
    "concept/DAG-上下文管理实现方案.md",
    "concept/DAG-Summaries.md",
    # 核心系统文档
    "concept/Memory-System-Architecture-Vision.md",
    "concept/Two-Layer-Memory-Architecture.md",
    "concept/hermes-os-architecture.md",
    # Meta
    "meta",
    "concept/skills-usage-guide.md",
]

def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    with open(SYNC_LOG, "a") as f:
        f.write(line + "\n")

def sync_file(src_path, obsidian_path):
    """Sync a single file. Returns True if changed."""
    if not os.path.exists(src_path):
        return False

    os.makedirs(os.path.dirname(obsidian_path), exist_ok=True)

    # Check if destination needs update
    if os.path.exists(obsidian_path):
        with open(src_path, "rb") as sf:
            src_content = sf.read()
        with open(obsidian_path, "rb") as df:
            dst_content = df.read()
        if src_content == dst_content:
            return False  # No change

    shutil.copy2(src_path, obsidian_path)
    return True

def main():
    log("=== Wiki → Obsidian 同步开始 ===")

    if not os.path.exists(WIKI_DIR):
        log(f"ERROR: Wiki 目录不存在: {WIKI_DIR}")
        sys.exit(1)

    os.makedirs(OBSIDIAN_DIR, exist_ok=True)

    synced = 0
    skipped = 0
    errors = 0

    for path in SYNC_PATHS:
        src = os.path.join(WIKI_DIR, path)
        dst = os.path.join(OBSIDIAN_DIR, path)

        if os.path.isdir(src):
            # Sync entire directory
            for root, dirs, files in os.walk(src):
                rel = os.path.relpath(root, WIKI_DIR)
                for file in files:
                    if file.startswith("."):
                        continue
                    src_file = os.path.join(root, file)
                    dst_file = os.path.join(OBSIDIAN_DIR, rel, file)
                    try:
                        if sync_file(src_file, dst_file):
                            log(f"  + {rel}/{file}")
                            synced += 1
                        else:
                            skipped += 1
                    except Exception as e:
                        log(f"  ERROR {rel}/{file}: {e}")
                        errors += 1
        elif os.path.isfile(src):
            try:
                if sync_file(src, dst):
                    log(f"  + {path}")
                    synced += 1
                else:
                    skipped += 1
            except Exception as e:
                log(f"  ERROR {path}: {e}")
                errors += 1
        else:
            log(f"  SKIP (not found): {path}")
            skipped += 1

    log(f"同步完成: {synced} 文件更新, {skipped} 跳过, {errors} 错误")
    log("=== Wiki → Obsidian 同步结束 ===")

if __name__ == "__main__":
    main()
