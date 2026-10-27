

# ApprovalQueueItem


## Properties

| Name | Type | Description | Notes |
|------------ | ------------- | ------------- | -------------|
|**reviewId** | **UUID** |  |  |
|**contractId** | **UUID** |  |  |
|**contractTitle** | **String** | 合同文件名（合同表里没有单独的标题字段，文件名就是人认得出的那个名字）。 |  |
|**riskHighCount** | **Integer** | 待审的高风险条款数（按 clause_id 去重）。同一条款可能被多个专家各报一次， 审核者要拍板的是条款，不是风险点。 |  |
|**createdAt** | **OffsetDateTime** | 审查发起时间。 |  |
|**waitingSince** | **OffsetDateTime** | 进入待审批的时间（取 interrupt 事件的 ts）。与 created_at 分开是因为 「等了多久」要按进审批算，按发起时间算会把运行耗时也算成审核者的等待。 |  |


## Implemented Interfaces

* Serializable


