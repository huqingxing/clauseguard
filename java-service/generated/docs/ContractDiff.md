

# ContractDiff


## Properties

| Name | Type | Description | Notes |
|------------ | ------------- | ------------- | -------------|
|**contractId** | **UUID** |  |  |
|**baselineVersion** | **Integer** |  |  |
|**targetVersion** | **Integer** |  |  |
|**changed** | **List&lt;String&gt;** | 编号相同但内容变化的条款 ID。 |  |
|**added** | **List&lt;String&gt;** |  |  |
|**removed** | **List&lt;String&gt;** |  |  |
|**staleFindings** | **Integer** | 因条款删除被标 stale 的历史 findings 条数（保留追溯）。 |  [optional] |


## Implemented Interfaces

* Serializable


