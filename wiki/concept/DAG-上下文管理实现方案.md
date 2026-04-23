---
created: 2026-04-23
updated: 2026-04-23
type: concept
tags: ["memory", "dag", "context-management"]
summary: "为 Hermes 实现 DAG 结构的上下文管理，参考 Lossless Claw 的分层摘要思路，解决上下文断裂问题"
---

# DAG-上下文管理实现方案

## 问题定义

Hermes 当前处理上下文的方式：
- 对话窗口填满 → 截断最旧的消息
- flush_memories → AI 决定保留什么，没有结构化摘要
- 结果：**上下文断裂**，新会话无法重建完整上下文

## 核心思路（来自 Lossless Claw）

Lossless Claw 的关键设计：

```
原始消息层（D0）
    ↓ AI 摘要
第一层摘要（D1）  ←  第一次压缩
    ↓ AI 摘要
第二层摘要（D2）  ←  第二次压缩
    ↓
... （按需展开）
```

**核心特性：**
1. **无损**：原始消息永远不删除，只是被摘要引用
2. **按需展开**：可以从 D2 展开回 D0 原始消息
3. **跨深度搜索**：lcm_grep 可以搜索 DAG 中所有节点
4. **新鲜尾部保护**：最近的原始消息（D0）永远不压缩

## Hermes 的 DAG 实现方案

### 数据结构

```python
# 每个节点的结构
class ContextNode:
    node_id: str           # 唯一标识
    depth: int             # 0=原始消息, 1=D1摘要, 2=D2摘要
    parent_ids: list[str]  # 父节点（DAG 支持多父）
    content: str           # 消息内容或摘要文本
    created_at: datetime
    token_count: int
    expansion: list[str]   # 展开后的子节点 IDs（用于重建）
```

### 存储方式

```
~/.hermes/
  dag/
    nodes/
      {node_id}.json      # 每个节点一个文件
    index.json             # DAG 索引（快速查找）
    tail.lock              # 新鲜尾部锁定（最近 N 条不压缩）
```

### 压缩策略

| 层 | 触发条件 | 摘要内容 | 保留原始 |
|----|---------|---------|---------|
| D0 | 始终 | 原始消息 | ✓ 始终 |
| D1 | D0 累积 > 5000 tokens | 会话片段摘要 | ✓ 保留 |
| D2 | D1 累积 > 3000 tokens | 全局主题摘要 | ✓ 保留 |
| D3 | （可选）| 极简概览 | ✓ 保留 |

### 展开策略

当需要重建上下文时：
1. 从 D2（或最高可用层）开始
2. 向下逐层展开到所需粒度
3. D0 始终可用，不丢失任何信息

## 与 Hermes 现有组件的关系

| 现有组件 | 在 DAG 中的角色 |
|---------|---------------|
| MEMORY.md | D2/D3 层 — 跨 session 的最高层摘要 |
| flush_memories | 触发 D1 摘要的机制 |
| session_search | 跨深度搜索的实现基础 |
| Skills | 独立演进，不纳入 DAG |

## 实施步骤

### Phase 1: 基础 DAG 结构
- [x] 创建 dag/ 目录结构
- [x] 实现 ContextNode 数据模型
- [x] 实现节点存储和索引
- [x] 实现基本的 DAG 构建（追加模式）
- [x] 实现 D0 → D1 压缩触发逻辑
- [x] 实现上下文窗口构建
- [x] 实现按需展开（DAG 遍历）
- [x] 实现全节点搜索

### Phase 2: 分层摘要引擎
- [x] 实现 D0 → D1 压缩触发逻辑
- [x] 实现 DAG-Hermes 集成层（dag_hermes_integration.py）
- [x] 实现 LLM 摘要生成调用（_summarize_llm）
- [x] 集成到 _compress_context 流程（_dag_compress_and_archive 方法）
- [ ] 实现新鲜尾部保护逻辑
- [ ] 集成到 flush_memories 流程

### Phase 3: 上下文重建
- [x] 实现按需展开（DAG 遍历）
- [x] 实现 DAG context block 注入系统提示（_build_dag_context_block）
- [x] 实现 dag_search 工具（tools/dag_tool.py）
- [x] 实现 dag_expand 工具（展开摘要回原始消息）
- [x] 实现 dag_stats 工具（查看 DAG 状态）
- [x] DAG 工具自动注册到 hermes-agent 工具注册表
- [ ] 实现跨深度搜索（lcm_grep）
- [ ] 实现上下文窗口填充算法

### Phase 4: 与现有系统集成
- [x] AgentMemory MCP 连接（MCP 服务已运行，config.yaml 已配置）
- [x] DAG → Wiki 同步脚本（~/.hermes/scripts/dag-wiki-sync.py，每小时自动同步 D1/D2 摘要到 Wiki）
- [x] Wiki → Obsidian 单向同步（~/.hermes/scripts/wiki-obsidian-sync.py，每小时自动同步核心文档到 Obsidian vault）
- [x] MEMORY.md 作为 D3 层（~/.hermes/scripts/memory-dag-sync.py，每日同步 DAG D1 摘要到 MEMORY.md）

## 关键技术决策

1. **存储格式**：JSON 文件（节点独立）而非 SQLite
   - 原因：简单、人类可读、易于调试
2. **摘要触发**：基于 token 阈值而非时间
   - 原因：上下文窗口以 token 计量
3. **DAG 方向**：从旧到新的有向无环图
   - 原因：支持多父节点（一个摘要可能引用多个片段）

## 风险和缓解

| 风险 | 缓解措施 |
|-----|---------|
| 摘要质量下降 | D0 始终保留，展开可验证 |
| 存储膨胀 | D1/D2 达到阈值后合并 |
| 复杂度增加 | Phase 1 先做最小可用版本 |
