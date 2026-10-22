

# ReviewResult


## Properties

| Name | Type | Description | Notes |
|------------ | ------------- | ------------- | -------------|
|**reviewId** | **UUID** |  |  |
|**contractId** | **UUID** |  |  |
|**status** | [**StatusEnum**](#StatusEnum) |  |  |
|**createdAt** | **OffsetDateTime** |  |  |
|**completedAt** | **OffsetDateTime** |  |  [optional] |
|**findings** | [**List&lt;Finding&gt;**](Finding.md) |  |  |



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


