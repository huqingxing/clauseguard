

# ReviewCreateRequest


## Properties

| Name | Type | Description | Notes |
|------------ | ------------- | ------------- | -------------|
|**contractId** | **UUID** | 已上传合同的 ID。 |  |
|**version** | **Integer** | 本次审查对应的合同版本号。Stage 11：增量重审按版本号定位基线快照， 与 base_version 共同决定 diff 的比对范围。 |  [optional] |
|**mode** | [**ModeEnum**](#ModeEnum) | Stage 11：full&#x3D;全量重审（不查缓存，成本对比基线）； incremental&#x3D;仅重审变更条款，未变更条款复用缓存结论。 |  [optional] |
|**baseVersion** | **Integer** | Stage 11：增量重审的基线版本号；0 表示由编排层按 version-1 推断。 仅 mode&#x3D;incremental 时有意义。 |  [optional] |



## Enum: ModeEnum

| Name | Value |
|---- | -----|
| FULL | &quot;full&quot; |
| INCREMENTAL | &quot;incremental&quot; |


## Implemented Interfaces

* Serializable


