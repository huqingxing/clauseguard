# 阶段 9 验收报告｜人工审批（interrupt）与断点恢复

- 日期：2026-10-24
- 关联：ADR-0001（双栈架构）、Stage 8（critic 确定性校验）

## 一、变更摘要

目标：审批是图的一部分（不是一次 HTTP 调用），进程重启后仍能从断点继续；Java 侧转发审批到 Python resume，并做乐观锁校验与审批落库。

### Python 侧（编排层，`python-agent`）

- **Checkpointer 换 PostgresSaver**：`app/db.py` 提供 `DbPool`（连接池由 `main.lifespan` 创建/关闭），`thread_id = thread_id_for(contract_id, version) = f"{contract_id}:{version}"`。`app/main.py` 仅在未注入 checkpointer 且配置了 `pg_dsn` 时升级为 Postgres；测试注入 `MemorySaver` 即可模拟「重启后持久化层仍在」。
- **`human_approval` 断点节点**：置于 critic 之后（无 HIGH 风险直接放行；存在 HIGH 风险调 `langgraph.interrupt()`）。节点本身不发射节点事件——`NODE_STARTED/INTERRUPT/NODE_FINISHED` 由编排层 `_run_pipeline` 在检测到中断/完成时发射，避免 resume 重跑导致的重复事件。
- **`/v1/runs/{run_id}/resume`**：POST，body 为审批结果数组（即 interrupt 的返回值）。复用同一 checkpointer 从 checkpoint 续跑，事件序号从 checkpoint 续接（不重置）。**本阶段将其改为 SSE 流式下发**（NODE_FINISHED(human_approval) + RUN_COMPLETED），使 Java 能把完成事件续接到前端订阅的 SSE，前端无需重连即收终态。
- **事件 seq 落盘**：`_persist_event_seq` 将 `emitter._seq` 写入 `ReviewState.event_seq`（checkpoint），恢复时 `_seed_emitter_seq` 续接。
  > **修正（Stage 10 验收时发现并修复）**：原实现用 `graph.aupdate_state` 写图 checkpoint，实测该调用会清掉中断态的 pending interrupt（`next` 从 `('human_approval',)` 变 `()`），导致生产链路 resume 空跑。现改为落 **run_registry**（断点元信息的正解），resume 端点从注册表续接 seq；`_seed_emitter_seq` 已删除。详见 `docs/stage10-acceptance.md` 变更摘要第 6 条。
- **中断 payload 增强**：`build_interrupt_payload` 现在逐条款携带 `clause_text`（条款原文），供 Java 审批记录「前措辞」留痕。

### Java 侧（业务层，`java-service`）

- **Flyway V2 迁移**：`review` 增加 `run_id / contract_version / content_hash / pending_payload`；`approval` 增加 `before_wording / after_wording / risk`（前后措辞 + 风险档）。
- **`Review` / `Approval` 实体**：补齐上述字段的列映射与 getter/setter。
- **`ReviewService` 事件捕获**：`handleAgentEvent` 在 `run.started` 捕获 Python 生成的 `run_id`（供 resume 定位 thread_id）；在 `interrupt` 缓存乐观锁基线（`contract_version`/`content_hash`/逐条款待审项）并将状态置 `AWAITING_APPROVAL`；在 `run.completed`/`run.failed` 置 `COMPLETED`/`FAILED`。
- **`approveReview`**：① 校验状态必须为 `AWAITING_APPROVAL`；② **乐观锁——逐条比对 `contract_version`/`content_hash` 与中断基线，任一不匹配即整体抛 `ContractChangedException`（409 CONTRACT_CHANGED），且判定发生在写入任何审批记录 / 触发 resume 之前（原子，无部分执行）**；③ 落库每条审批记录（含操作人、时间、前后措辞、风险档）；④ 状态切回 `RUNNING` 并转发 Python `resume`（审批数组即 interrupt 返回值），经 `streamResume` 以 SSE 订阅续跑事件，由 `handleAgentEvent` 把完成事件续接到前端 SSE。
- **`PythonAgentClient.streamResume`**：POST `/v1/runs/{run_id}/resume`，body 为审批数组，订阅 SSE，受熔断保护。
- **`ReviewController.approve`**：`@PostMapping("/{id}/approve")`，body 为 `List<ApproveRequest>`（数组），`@RequestHeader("X-Operator")` 取操作人。
- **`ContractChangedException` + `GlobalExceptionHandler`**：映射 409 CONTRACT_CHANGED。
- **`contracts/openapi.yaml`**：approve 请求体改为数组、新增 `X-Operator` 头、新增 409 响应。

## 二、验收结果

| 验收点 | 结果 | 验证方式 |
|---|---|---|
| (a) 触发审批 → 前端收到 interrupt 事件 → 停住 | ✅ | `test_human_approval_interrupt_pauses_pipeline`：`_run_pipeline` 返回 False、事件流含 `interrupt`、无 `run.completed`、payload 含 `reason/contract_version/content_hash/items`、所有事件通过 `events.schema.json` 校验 |
| (b) 重启 Python → resume 跑完、seq 续接不重置、不重跑整图 | ✅ | `test_resume_continues_without_rerun_and_seq_not_reset`：resume 完成、`min(resume_seqs) > pause_max_seq`、resume 阶段仅 `render` 的 `node.started`、human_approval `NODE_FINISHED` 随 resume 补发；`test_resume_endpoint_resumes_paused_run`：SSE resume 下发 `run.completed` |
| (c) contract_version 改一位 → 409，无部分执行 | ✅ | `ReviewServiceTest.approveRejectsOnVersionMismatchWithoutPartialWrite`：抛 `ContractChangedException`，且 `approvalRepository.save` 从未被调用 |
| (d) 审批历史能在 MySQL 查到，含操作人 | ✅ | `ReviewServiceTest.approvePersistsApprovalsWithOperatorAndForwardsResume`：审批记录含 `operator=alice`、`before_wording`、`risk`、`after_wording`(edit 项) 落库；`ReviewControllerTest`：`/approve` 返回 200(accepted) / 409(CONTRACT_CHANGED) / 404 |

**禁止项核查**：
- 未用内存 dict 存中断状态——运行态全部由 checkpointer（Postgres/Memory）持久化。
- resume 未从头重跑整个图——使用 `Command(resume=...)` 从 checkpoint 续跑，仅重入 `human_approval` 后进入 `render`。

**测试总量**：Python `pytest` 全绿（169 passed，ruff clean）；Java `mvn test` 全绿（24 passed，含 7 个 Stage 9 新增用例）。

## 三、遗留风险

1. **乐观锁基线仍是 mock**：`contract_version` 当前为 `state["version"]`（mock 下为 0），`content_hash` 为 `_mock_content_hash`（按 contract_id 派生）。锁逻辑本身正确（比对回显值），但要在生产具备「防篡改」语义，需先把真实合同版本与 MinIO 对象 sha256 接入（见下一阶段）。
2. **`before_wording` 依赖 clause_text**：来自中断 payload 的条款原文；生产中需保证 ingest 阶段正确填充 `Clause.text`。当前测试语料已含文本，链路打通。
3. **Flyway V2 未在真实 MySQL 上跑过**：本仓测试用 `@WebMvcTest` 不触发 Flyway。V2 仅 `ALTER TABLE ... ADD COLUMN` + `TEXT`，语法在 MySQL / H2(MySQL 模式) 通用，风险低，但首次连真库时应观察迁移日志。
4. ~~**阶段 8 测试缺口**~~：已补齐（Stage 8 收尾时落地 `test_critic_validation.py` / `test_critic.py` / `test_acceptance_stage8.py`，并修复 critic 静态边与 `Command(goto)` 双路由冲突）。
5. **resume 中断早期断开**：若 Java 在续跑完成前断开 SSE，Python 会取消该 run（与首次运行语义一致），属预期；熔断已包裹 `streamResume`。

## 四、下一阶段建议

1. **接真实内容哈希与版本**：替换 `_mock_content_hash` 为 MinIO 对象字节 sha256；把真实合同版本写入 `state["version"]`，使乐观锁具备生产语义（直接服务验收点 c 的「防篡改」本意）。
2. **render 占位 → python-docx 批注 Word**：`render` 节点当前占位；下一阶段按 `after_wording` 生成修订批注文档，把审批结果真正回写。
3. **端到端 SSE 联调测试**：Java→Python 真实 `streamResume` 续跑 + 前端完成事件链路，用容器化 Python 跑一次，覆盖现有 mock 之外的真实网络/时序。
4. **补齐阶段 8 测试**，并考虑为 approve 增加前端审批界面（展示 clause_text + suggestion + decision + edited_text）。
