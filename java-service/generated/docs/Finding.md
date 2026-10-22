

# Finding


## Properties

| Name | Type | Description | Notes |
|------------ | ------------- | ------------- | -------------|
|**findingId** | **UUID** |  |  |
|**clauseId** | **String** |  |  |
|**riskLevel** | [**RiskLevelEnum**](#RiskLevelEnum) |  |  |
|**category** | **String** | 风险类别，如 违约责任 / 保密义务。 |  |
|**summary** | **String** | 风险摘要。 |  |
|**quote** | **String** | 命中原文片段。 |  [optional] |
|**suggestion** | **String** | 修改建议。 |  [optional] |
|**references** | **List&lt;String&gt;** | 引用白名单条目，模型不得生成 schema 外法条编号。 |  [optional] |
|**agent** | **String** | 产出该发现的专家 Agent。 |  [optional] |
|**createdAt** | **OffsetDateTime** |  |  |



## Enum: RiskLevelEnum

| Name | Value |
|---- | -----|
| LOW | &quot;low&quot; |
| MEDIUM | &quot;medium&quot; |
| HIGH | &quot;high&quot; |


## Implemented Interfaces

* Serializable


