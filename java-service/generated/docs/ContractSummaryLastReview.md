

# ContractSummaryLastReview

最近一次审查摘要；无审查时为 null。

## Properties

| Name | Type | Description | Notes |
|------------ | ------------- | ------------- | -------------|
|**reviewId** | **UUID** |  |  [optional] |
|**status** | [**StatusEnum**](#StatusEnum) |  |  [optional] |
|**mode** | [**ModeEnum**](#ModeEnum) |  |  [optional] |
|**findingsCount** | **Integer** |  |  [optional] |



## Enum: StatusEnum

| Name | Value |
|---- | -----|
| QUEUED | &quot;queued&quot; |
| RUNNING | &quot;running&quot; |
| AWAITING_APPROVAL | &quot;awaiting_approval&quot; |
| COMPLETED | &quot;completed&quot; |
| FAILED | &quot;failed&quot; |



## Enum: ModeEnum

| Name | Value |
|---- | -----|
| FULL | &quot;full&quot; |
| INCREMENTAL | &quot;incremental&quot; |


## Implemented Interfaces

* Serializable


