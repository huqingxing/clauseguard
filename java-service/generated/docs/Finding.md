

# Finding

审查发现。Stage 12：与 contracts/events.schema.json 的 $defs/Finding 严格同构 （run.completed.findings 是唯一事实源），此前声明的 finding_id/category/quote/ references 形状从未实现，本次对齐纠偏。

## Properties

| Name | Type | Description | Notes |
|------------ | ------------- | ------------- | -------------|
|**clauseId** | **String** | 关联条款 ID，点击定位与批注锚点共用。 |  |
|**agent** | **String** | 产出该发现的专家 Agent 名（如 commercial-v1）。 |  |
|**riskType** | **String** | 风险类别标签，前端筛选维度。 |  |
|**riskLevel** | [**RiskLevelEnum**](#RiskLevelEnum) | 风险等级；unverified 为引用校验降级值。 |  |
|**summary** | **String** | 发现摘要，面向业务人员的结论。 |  |
|**suggestion** | **String** | 修改建议。 |  [optional] |
|**evidence** | **String** | 条款原文逐字片段，定位风险位置用。 |  [optional] |
|**stale** | **Boolean** | Stage 11：条款已删除的历史结论标记（明细中恒为 false）。 |  [optional] |
|**citations** | [**List&lt;FindingCitationsInner&gt;**](FindingCitationsInner.md) | 白名单引用；来源只能是检索结果，前端可展开核对。 |  [optional] |



## Enum: RiskLevelEnum

| Name | Value |
|---- | -----|
| LOW | &quot;low&quot; |
| MEDIUM | &quot;medium&quot; |
| HIGH | &quot;high&quot; |
| UNVERIFIED | &quot;unverified&quot; |


## Implemented Interfaces

* Serializable


