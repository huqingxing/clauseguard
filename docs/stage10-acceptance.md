# Stage 10 验收报告｜Word 批注回写

- 日期：2026-10-24
- 范围：仅阶段 10（Word 批注回写 + MinIO 产物 + artifact 事件契约）；附带的 Stage 9 修复见「变更摘要」第 6 条

## 一、阶段目标（复述）

用 lxml 直接操作 OOXML 四件套（document.xml / comments.xml / document.xml.rels / [Content_Types].xml），基于 Clause.start/end 全文偏移重建 run 级批注锚点，为每条 finding 写入「风险等级+类型+建议+引用法条」批注，高/中风险分别用黄色/浅黄高亮，产物上传 MinIO 返回签名 URL；不重排原文、不用 python-docx 的 add_comment。

## 二、变更摘要（文件清单）

**新增（Python）**
- `app/render/docx_writer.py`：lxml 批注手术。锚点坐标系重建（与 loader raw 逐块自校验）、run 中部边界拆分、跨 run 多段连续覆盖、commentRangeStart/End + commentReference 四件套、w:hyperlink 内锚点的段落级插入、高亮按 CT_RPr 元素序插入、重打包保留无关部件。
- `app/render/service.py`：源解析（config 注入 > MinIO > local_dir）→ 解析层条款 span join → 挂批注 → 上传；三种不可写情况显式 `PLACEHOLDER_RENDER` 占位。
- `app/render/__init__.py`。
- `app/clients/minio_client.py`：httpx + SigV4（不引 minio SDK，铁律 5）；预签名 GET URL / PUT 请求头签名；MinioClient + LocalStorageClient（真实本地后端，非 mock）+ 对象键约定。
- `tests/render/`：fixtures（20 份确定性样本生成器 + OOXML 结构强校验助手）、test_docx_writer.py（13）、test_minio_client.py（15）、test_render_service.py（11）。
- `tests/agents/test_acceptance_stage10.py`：20 样本回写 + 抽检锚点 + 中文无乱码 + artifact 全链路验收。
- `docs/stage10-acceptance.md`（本文件）。

**修改**
- `app/graph/nodes/render.py`：占位节点改为真实回写（RenderError 冒泡不吞）。
- `contracts/events.schema.json` + `app/contracts/models.py`（重新生成）：RunCompleted 增加可选 `artifact`（object_key/download_url/comments_count/size_bytes）。
- `app/main.py`：`_artifact_of` 从终态 render_result 提取产物；run.completed 携带 artifact。
- `pyproject.toml`：mypy lxml override（无 stub 包且版本表不外扩）。

**Stage 9 修复（Stage 10 端到端验收发现，随本阶段一并修复）**
- `app/main.py`：`_persist_event_seq` 原通过 `graph.aupdate_state` 把 event_seq 写进图 checkpoint——实测该调用会清掉中断态的 pending interrupt（next 从 `('human_approval',)` 变 `()`），导致生产链路 resume 变成空跑（render 永不执行）。现改为落 run_registry（断点元信息的正解），resume 端点从注册表续接 seq；`_seed_emitter_seq` 删除。
- `tests/test_acceptance_stage9.py`：直驱测试改用 registry 续接；端点测试加强——断言 resume 后 render 节点真的开跑（防空跑回归）。

## 三、验收结果（真实输出）

```
$ uv run pytest -q
238 passed in 66.02s

$ uv run ruff check
All checks passed!

$ uv run mypy app
Success: no issues found in 47 source files
```

| 验收点（阶段任务书） | 结果 |
|---|---|
| Word 与 WPS 打开无「需要修复」 | ✅ 自动化代理全过：zip CRC + python-docx 重开 + 四件套 id 集合一致 + rels comments 关系 + [Content_Types] Override + 三部件 UTF-8 声明 + 无关部件逐字节不变（页眉/样式保留） |
| 20 份样本回写后文件结构完整 | ✅ 20/20，每份 5 条批注（2 high + 2 medium + 1 low），python-docx 重开无异常 |
| 人工抽检 10 条：批注锚点与原文 span 对齐 | ✅ 全量 100 条逐字对齐（commentRange 覆盖文本 vs 条款 span，剥空白与合成 token 后相等），超过抽检基线 |
| 中文批注内容无乱码 | ✅ 摘要/建议/法条标题与编号逐字回念；UTF-8 声明 + 序列化双保险 |
| 任务 1 高亮 | ✅ 高风险 w:highlight=yellow、中风险 w:shd=FFFFCC、低风险不着色，无杂色 |
| 任务 4 MinIO + 签名 URL | ✅ SigV4 黄金值锁定（预签名 GET + PUT 请求头）；上传/下载 MockTransport 全链路；run.completed artifact 四字段经「断点→resume」全链路下发 |
| 附加：resume 断点续跑（Stage 9 回归） | ✅ resume 后 render 真跑、ingest/dispatch 不重跑、seq 续接不重置 |

## 四、遗留风险

1. **WPS 实机验证未做**：当前环境无 Word/WPS，「无需要修复提示」由结构强校验代理（四件套一致性 + rels/ContentTypes + 元素序 + CRC）。建议部署后用 WPS 各抽 1 份人工确认。
2. **MinIO 环境变量未接线**：存储连接用自带前缀 `CLAUSEGUARD_MINIO_*`（endpoint/access_key/secret_key/bucket/region/local_dir），docker-compose 的 python-agent service 尚未注入这些变量；未配置时 render 走显式占位（不假失败）。.env.example 的既有命名待统一。
3. **PDF 源不回写**：非 docx 源走 `non-docx-source` 占位（TODO 标注 Stage 11+）。
4. **ingest 仍用 mock 元信息**：`mock_fetch_minio_meta` 恒返回 version=1；真实合同元信息拉取属后续阶段（render 侧已能自行解析源，不阻塞本阶段）。
5. **LocalStorageClient 是开发后端**：生产须配置 MinIO（TODO 标注移除时机）。
6. **表格/标题段批注**：表格合成 token 区不可锚定（设计如此，span 完全落在 token 区会跳过并计数）；真实合同若需批注表格单元格文本，解析层需输出 cell 级 span（后续）。

## 五、下一阶段建议

1. **Stage 11｜PDF 回写或前端下载闭环**：二选一——(a) PDF 源转 docx 后回写；(b) 前端按 artifact.download_url 下载 + 批注面板展示（消费 run.completed.artifact）。
2. **部署接线**：compose 注入 CLAUSEGUARD_MINIO_*，2G 服务器验证 MinIO + python-agent 联通；WPS 实机抽检。
3. **ingest 真实化**：用 minio_client 拉源对象元信息替换 mock_fetch_minio_meta，version 随合同版本流转。
