# ADR-0003: 引用白名单（宁可标「不可信」，不可静默编法条）

- 状态：已接受
- 日期：2026-10-25

## 背景

合同审查的结论要能被法务核验。模型最容易犯的错不是「漏判」，而是**编一条看起来合理的
法条**——「《民法典》第九百九十九条」这种编号在语料里根本不存在，但格式完美，业务人员
很难一眼看穿。一旦编的编号进了 Word 批注，整份报告的可信度就塌了。

红线原文：**不让模型自由生成法条编号，引用只能来自检索结果白名单。**

## 决策

三道闸，逐层收紧，且都在确定性代码里（不依赖模型自觉）：

1. **输入闸（prompt 之外）**：专家 Agent 的引用必须来自其检索工具（`mock_search_law` /
   `mock_search_policy` / `mock_search_similar`）的返回；prompt 只描述「如何引用」，不提供
   任何编号清单。
2. **产出闸（`enforce_citation_whitelist`）**：以本次运行的检索结果构建白名单
   （`source_id → article/title/excerpt/url`）。模型给的 `source_id` 或 `article`
   命中不了 → 丢弃该条引用并计数；**引用内容一律用白名单条目重建**，不采信模型自带的
   title/excerpt（否则它仍可以把正确编号配错误内容）。
3. **复核闸（`critic` 节点）**：`extract_whitelist(messages)` 从 Agent 的 ToolMessage 里
   还原「产出该结论时真实发生的检索集合」，再逐条复核 finding 的引用是否属于这个快照，
   外加 `evidence` 必须是条款原文的逐字片段。

## 被丢空之后怎么办

引用被全部丢弃 → `risk_level` 降级为 `unverified`，**finding 保留**，交由人工审批环节
兜底。

这是本 ADR 最重要的一条取舍：如果直接删掉这条 finding，就变成「因为没法证明，所以假装
没看见」——漏判比误判更难被发现。标成 `unverified` 让它在报告里显眼地存在，同时
`quality.unverified_count` 会把它计入质量指标，趋势可观测。

## 后果

- 伪造法条在系统里不可能进入报告：Stage 7 验收中「伪造/越界引用 0 条」。
- Stage 13 评测的引用准确率 96%（48/50）：剩下 2 条是 examiner 刻意注入的「引相邻法条」
  缺陷——它们**能过**白名单（同一次检索返回的候选），但过不了金标比对。这正是白名单的
  能力边界：它保证「引用真实存在于本次检索」，不保证「语义最贴切」；后者要靠评测与人工抽检。
- 全流程无一处允许模型自造编号；新增专家复用同一套校验，不需要额外约定。

## 相关

- 实现：`app/agents/validation.py`、`app/graph/nodes/critic.py`、`app/tools/{law,policy,similar}.py`
- 契约：`contracts/events.schema.json` 的 `Finding.citations[]`（`source_id` 必填）
- 评测：`eval/metrics.py` 的 citation accuracy（金标 50 条，抽检比例如实报告）
