# 存储对象键约定（单一事实来源）

跨服务（Java 上传方 / Python 渲染方）共享的源合同与回写产物的对象键约定。
**两侧都必须调用各自的生成函数，禁止在两处各写一份字符串字面量。**

## 源合同（source）

- 约定键：`contracts/{contract_id}/v{version}/source.{ext}`，`ext ∈ {docx, pdf}`
- Java 生成函数：`com.clauseguard.contract.ContractStorageKeys#sourceKey(contractId, version, ext)`
- Python 生成函数：`app.clients.minio_client#source_object_key(contract_id, version, ext)`
- 两份实现必须产出完全一致的字串；由 `ContractStorageKeysTest` 与
  `tests/render/test_minio_client.py::test_object_key_conventions` 双向断言守门。

## 回写产物（annotated docx）

- 约定键：`artifacts/{contract_id}/v{version}/annotated.docx`
- Python 生成函数：`app.clients.minio_client#artifact_object_key(contract_id, version)`
- Java 侧不生成此键（产物只由 Python 写回对象存储）。

## 历史遗留键（兼容，只读回退）

- 修复前（Stage 12）Java 把源落在 `{contract_id}-v{version}.{ext}`（单段文件名，无前缀）。
- 该遗留键是**旧上传**的物理落点；render 在找不到新约定键时，按 `source_candidates`
  的回退顺序尝试它（仅当新键缺失时），命中即 WARN 提示走 legacy 路径，不报错。
- 新上传一律写新约定键；遗留键不再产生。

## 边界

- 键是受控约定字符串，解析端（Python `LocalStorageClient`、Java `ContractService.loadContent`）
  仍防 `..` 目录穿越与越界写；允许 `/`（约定键本身含层级），但绝不越过存储根。
- 约定变更必须同步改 Java/Python 两处生成函数 + 两个守门测试 + 本文档。
