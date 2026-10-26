# ADR-0004: 中断恢复（图内人工审批，跨进程可续跑）

- 状态：已接受
- 日期：2026-10-25

## 背景

高风险条款必须人工确认后才能出报告。审批是**人的节奏**：可能 3 分钟，也可能 3 小时。
这带来两个硬约束：

1. 不能靠「把 HTTP/SSE 连接挂着等人」——连接会断，进程会重启；
2. 恢复后不能从头重跑——重跑意味着重复付费（token）与结果漂移。

红线原文：**每个图节点必须可中断、可恢复；运行态只放在 `ReviewState` 里，不用全局变量。**

## 决策

1. **断点在图内**：`human_approval` 节点检测到 `risk_level=high` 的 finding 就调用
   LangGraph 的 `interrupt(payload)`，把待审批项与乐观锁基线（`contract_version` /
   `content_hash`）作为 payload 抛给调用方，图本身挂起。
2. **运行态落 Postgres**：编译图时注入 `PostgresSaver`，`thread_id = contract_id:version`。
   进程重启后，同一 thread 的 checkpoint 还在。
3. **恢复走独立端点**：`POST /v1/runs/{run_id}/resume`，body 是审批结果数组（即 `interrupt()`
   的返回值），内部 `graph.ainvoke(Command(resume=approvals), config)` 续跑。
   恢复不是重跑：只重跑 `human_approval`（幂等，读 resume 值写 `approved`）与 `render`。
4. **事件序号单独持久化**：`run_id → thread_id / version / content_hash / trace_id /
   event_seq` 存 `run_registry`，中断时更新 `event_seq`。

## 踩过的坑（写进 ADR，避免后人重蹈）

用 `graph.aupdate_state()` 把 `event_seq` 写回图状态，**会清掉 pending interrupt**
（`next` 从 `('human_approval',)` 变成 `()`）——之后 `Command(resume=...)` 无处可恢复，
render 永远不执行。最小复现已验证。所以序号随 `run_registry` 落盘，绝不写图状态。

## 备选与否决理由

| 方案 | 否决原因 |
| --- | --- |
| 审批放图外（拆成两次独立运行） | 第二次运行要重读全部 findings/预算/缓存，等于重跑；且状态在两次运行间要另建一套存储 |
| 把待审批项写进 MySQL 业务表，前端轮询 | 断言与恢复逻辑散落两栈；Python 侧仍需持有图上下文，等于两处状态 |
| 内存 checkpointer + 长连接等待 | 进程重启即丢失，无法支撑真实审批时长 |

## 后果

- 中断时**不下发** `run.completed`，只发 `node.started(human_approval)` + `interrupt`
  ——前端据此停在待审批态，不会把半程结果当结论。
- 恢复沿用同一 `traceparent`，链路里出现「首次运行」与「审批后续跑」两段根 span，
  同一 `trace_id` 下串起（Stage 14 的 `clauseguard.resumed` 标记区分）。
- 审批期间的增量运行不会 miss 缓存：`cache_store` 在 `human_approval` **之前**，
  缓存必须在断点前落盘（这个顺序是刻意设计的，见 Stage 11）。
- 代价：Postgres 成为恢复链路的强依赖；本地/测试用 `MemorySaver` 兜底（`pg_dsn` 为空）。

## 相关

- 实现：`app/graph/nodes/human_approval.py`、`app/main.py`（`_run_pipeline` / `resume_run`）、
  `app/db.py`（checkpointer + run_registry）
- Java 侧：`ReviewService.approveReview` 转发审批结果到 resume 端点，并做版本/哈希乐观锁
- 验收：`docs/stage9-acceptance.md`
