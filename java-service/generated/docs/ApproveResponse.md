

# ApproveResponse


## Properties

| Name | Type | Description | Notes |
|------------ | ------------- | ------------- | -------------|
|**status** | [**StatusEnum**](#StatusEnum) | accepted&#x3D;审批已记录；conflict&#x3D;contract_version/content_hash 不匹配需重试。 |  |
|**decidedAt** | **OffsetDateTime** |  |  |



## Enum: StatusEnum

| Name | Value |
|---- | -----|
| ACCEPTED | &quot;accepted&quot; |
| CONFLICT | &quot;conflict&quot; |


## Implemented Interfaces

* Serializable


