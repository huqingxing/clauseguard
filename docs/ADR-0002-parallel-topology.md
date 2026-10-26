# ADR-0002: 并行拓扑（Send fan-out + 纯函数 reducer）

- 状态：已接受
- 日期：2026-10-24

## 背景

一份 50 条的合同要过 6 个专家。串行执行时，每个条款至少一次模型调用：50 × 6 = 300 次调用。
用 Stage 7 的确定性替身（每次调用固定 20ms）实测串行 P95 = 3244ms；换成真实模型（单次
1–3 秒），一份中等合同就是 5–15 分钟——演示与压测都不可接受。

结论：并发不是优化项，是能否交付的分界线。

## 决策

用 LangGraph 的 `Send` 动态派发做「专家 × 分片」级并行：

1. `dispatch` 节点按专家范畴（`scope_keywords`）把条款筛成本专家的子集，再切成 ≤8 条的
   分片（分片是为了让慢专家不拖住整轮，也给预算门控留粒度）；
2. 每个分片一个 `Send("expert_review", ReviewTask(...))`，同一节点被并发调用，各分片只读
   自己的载荷（Send 隔离，节点看不到父状态）；
3. 并发写同一条状态通道靠自定义 reducer 收敛：`findings` 追加去重、`budget` 求和、
   `degraded` 取或、`reviewed_pairs` 并集。reducer 全部是纯函数，禁止 IO；
4. 在途模型调用数由按 run 创建的 `asyncio.Semaphore`（`concurrency_limit`）压住，
   防止一次 fan-out 把供应商限流打满。

## 备选与否决理由

| 方案 | 否决原因 |
| --- | --- |
| 节点内 `asyncio.gather` 直接并发调 6 个 agent | 并发藏在节点里 → 图上看不到、checkpoint 无法在分片级中断、预算门控失去粒度 |
| 线程池 / 进程池并发 | 迁移不到 Postgres checkpointer 的恢复语义；中断后无法只重跑失败分片 |
| 全量条款一次派给一个专家（不分片） | 慢专家成为整轮 P95 的唯一下界；预算耗尽时无法只砍低优先级分片 |

## 后果

- **收益**：Stage 7 实测 P95 从 3244ms 降到 967ms（−70.2%），超过 ≥60% 的验收线；
  findings 合并后 20 条 / 20 个唯一键（无重复）。
- **代价 1**：节点间不能共享可变状态，任何跨分片信息必须以载荷或 reducer 通道传递
  （例如 Critic 打回指令写在 `ReviewTask.rework_hints` 里随 Send 带下去）。
- **代价 2**：单分片失败不能抛给 LangGraph（会炸掉整轮），必须显式降级为
  `node.failed` + `degraded=true`，其余分片继续。
- **代价 3**：并行下事件交错，`seq` 仍是全局单调（单一 EventEmitter），
  但「同超步内谁先发」不确定——前端必须按 seq 排序、不能假设到达顺序。

## 相关

- 实现：`app/graph/build.py`、`app/graph/nodes/dispatch.py`、`app/graph/nodes/expert_review.py`
- 验收：`eval/stage7-acceptance-2026-10-24.md`
- 串行对照：`execution_mode=serial`（同引擎顺序执行，用于降级兜底与 A/B）
