

# ApproveRequest


## Properties

| Name | Type | Description | Notes |
|------------ | ------------- | ------------- | -------------|
|**clauseId** | **String** | 被审批条款的 ID。 |  |
|**decision** | [**DecisionEnum**](#DecisionEnum) |  |  |
|**comment** | **String** | 审批意见，可空。 |  [optional] |
|**editedText** | **String** | decision&#x3D;edit 时必填，审批后的条款正文。 |  [optional] |
|**contractVersion** | **String** | 审批所基于的合同版本号，乐观锁用。 |  |
|**contentHash** | **String** | 被审批条款内容的哈希，防止并发篡改。 |  |



## Enum: DecisionEnum

| Name | Value |
|---- | -----|
| APPROVE | &quot;approve&quot; |
| REJECT | &quot;reject&quot; |
| EDIT | &quot;edit&quot; |


## Implemented Interfaces

* Serializable


