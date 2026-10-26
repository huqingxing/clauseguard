

# ReviewResult


## Properties

| Name | Type | Description | Notes |
|------------ | ------------- | ------------- | -------------|
|**reviewId** | **UUID** |  |  |
|**contractId** | **UUID** |  |  |
|**version** | **Integer** | 本次审查对应的合同版本（Stage 11 起落库）。 |  [optional] |
|**runId** | **String** | Python 运行 ID（run.started 捕获），断点恢复定位用。 |  [optional] |
|**status** | [**StatusEnum**](#StatusEnum) |  |  |
|**createdAt** | **OffsetDateTime** |  |  |
|**completedAt** | **OffsetDateTime** |  |  [optional] |
|**findings** | [**List&lt;Finding&gt;**](Finding.md) | 审查发现明细，来自 run.completed 的 findings 字段（与 events.schema.json 的 Finding 同构，已剔除 stale）。运行未完成时为空数组。 |  |



## Enum: StatusEnum

| Name | Value |
|---- | -----|
| QUEUED | &quot;queued&quot; |
| RUNNING | &quot;running&quot; |
| AWAITING_APPROVAL | &quot;awaiting_approval&quot; |
| COMPLETED | &quot;completed&quot; |
| FAILED | &quot;failed&quot; |


## Implemented Interfaces

* Serializable


