

# ContractDetail


## Properties

| Name | Type | Description | Notes |
|------------ | ------------- | ------------- | -------------|
|**contractId** | **UUID** |  |  |
|**filename** | **String** |  |  |
|**version** | **Integer** |  |  |
|**contentType** | **String** |  |  |
|**sizeBytes** | **Integer** |  |  |
|**createdAt** | **OffsetDateTime** |  |  |
|**contentUrl** | **String** | 原文下载地址（Java 端点，形如 /api/v1/contracts/{id}/content）。 |  [optional] |
|**lastReview** | [**ContractDetailLastReview**](ContractDetailLastReview.md) |  |  [optional] |


## Implemented Interfaces

* Serializable


