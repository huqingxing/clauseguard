# ADR-0007：审查状态与事件写入同事务（+ 终态补偿）

- 状态：**已实施并验收**（2026-11-12）
- 关联：README「已知限制」原第 9 条已更新；`V4__widen_event_payload.sql` 只消除了当次触发条件，本 ADR 处理结构性缺陷
- 不改：`contracts/events.schema.json`、数据库 schema（无新表）、事件语义、findings 逻辑

## 1. 背景

`ReviewService.handleAgentEvent` 原先分两次提交：先 `reviewRepository.save` 改 `review.status`
（自动提交），再在另一个 `TransactionTemplate` 里插 `review_event` + 刷 `last_event_at`。
Stage 5 撞到过：`run.completed` 的 payload 超过 `TEXT` 上限，事件插入失败而状态已是
`COMPLETED`，用户端重放看不到终态，页面永远停在「等待审批」。

V4 把列放宽到 `MEDIUMTEXT` 后触发条件消失，但只要「两次提交」还在，任何一次事件写失败
（列宽、约束、连接中断、磁盘满）都会重新制造这个组合。

### 修复前复现（本地真实 MySQL 8.0.34，`clauseguard` 库）

不改动表结构、不动历史数据：构造一条 17,000,000 字节的 `run.completed` payload
（超过 MEDIUMTEXT 上限 16,777,215 字节），`STRICT_TRANS_TABLES` 拒绝插入。
用例：`java-service/src/test/java/com/clauseguard/review/ReviewEventConsistencyIntegrationTest.java`

```
===== T-FAIL | review=repro-6822001e =====
DB  review.status          = COMPLETED
DB  review_event 总行数     = 0
DB  终态事件行数            = 0
DB  最后一条事件 type       = -
DB  review.last_event_at   = null
ETL 写入异常               = Data truncation: Data too long for column 'payload' at row 1
SSE replaySince(-1)        = []
===== END =====
ERROR o.h.engine.jdbc.spi.SqlExceptionHelper : Data truncation: Data too long for column 'payload' at row 1
```

没有采用「临时把列改窄」：现库里已有 100,283 字节的历史 payload，把列改回 `TEXT` 会破坏它们。

## 2. 决策

### 2.1 选方案 A（同一事务），否决方案 B（Outbox）

| | A：同一 `@Transactional` | B：Outbox 表 + 异步投递 |
|---|---|---|
| 改动面 | 1 个新写入 Bean + 2 处 Repository 方法 | 新表、投递器、幂等消费、至少一次语义、延迟可见 |
| 一致性 | 本地事务强一致，写失败即回滚 | 最终一致，需要额外对账 |
| 适用 | 单实例、事件量与状态量同级 | 重放存储与业务库**外部分离**、需要向外部系统可靠投递 |

判据按任务书：当前是单实例，重放存储（Redis Stream / 内存实现）只是 `review_event` 表的
**派生投影**，不是独立外部消费者。B 的收益要等「重放存储与外部分离」才显现，现在引入只会
多一张表和一个投递链路。因此选 A，并把 B 里真正有用的那部分（事实源 + 可重建投影 + 周期补偿）
用**现有 `review_event` 表**实现，不新增表、不引入 MQ。

### 2.2 唯一事务写入口

新增 `com.clauseguard.review.ReviewEventWriter`（独立 Bean，让 `@Transactional` 真正走 Spring 代理）：

```java
@Transactional
public void persist(String reviewId, Consumer<Review> stateMutation,
                    ReviewEvent event, String lastEventAtUtc) {
    Review review = reviewRepository.findById(reviewId).orElse(null);
    if (review != null) { stateMutation.accept(review); reviewRepository.save(review); }
    eventRepository.save(event);          // 失败 → 上面的状态一起回滚
    if (lastEventAtUtc != null) reviewRepository.updateLastEventAt(reviewId, lastEventAtUtc);
}
```

- 为什么独立 Bean：`handleAgentEvent` 由同一 Bean 内的 reactor 回调调用，直接加注解会被自调用绕过。
- 状态变更改成 `Consumer<Review>`，由事件纯解析推导，**只在事务内对受管实体执行**；
  服务层不再自己 `findById` + `save` 抢先改状态。
- `updateLastEventAt` 加 `flushAutomatically/clearAutomatically`：单事务里受管 `Review` 的
  UPDATE 必须先落库，再执行原生时间戳 UPDATE，且清空持久化上下文，避免 commit 时旧字段值把
  `last_event_at` 覆盖回去。

### 2.3 终态最高写入优先级

事务保证「同一次写入不分叉」，但还要防止「一条非关键事件把流打断，真正的终态再也来不了」：

| 写失败的事件 | 处理 | 是否吞异常 |
|---|---|---|
| `run.completed` / `run.failed` | 状态随事务回滚 → 新事务补偿一条契约合规的 `run.failed`（`EVENT_WRITE_FAILED`, `retryable=true`），状态置 `FAILED` | 否；补偿本身再失败则原异常向上抛给调用方 |
| `run.started` / `interrupt` | 丢了 `run_id` / 审批基线就没法 resume，不能降级 → 原异常向上抛，调用方（reactor `onError`）标失败 | 否 |
| `node.*` / `message.*` / `tool.*` | 不中断 SSE 流；在同一 seq 落一条契约合规的 `node.failed` 诊断事件（`EVENT_PERSIST_FAILED`），序列连续、终态仍可写入 | 否（有持久化动作 + WARN，不是「catch 掉只打日志」） |

补偿事件严格使用 `events.schema.json` 已有类型与字段（`additionalProperties:false` 不变），
不改契约、不私加字段。

### 2.4 重放投影是派生的，可重建

`replayStore.append` 不参与 DB 事务。约定 **DB `review_event` 是唯一事实源**：

- append 抛异常 → 按 seq 从 `findByReviewIdOrderBySeqAsc` 重建投影（幂等，只补缺失 seq）；
- 终态事件发布后强校验投影里必须有终态，否则立刻重建，仍缺失则抛异常；
- 客户端 `?since=0` 因此总能拿到终态。

### 2.5 看门狗补偿（已有定时任务）

`ReviewWatchdog.repairTerminalGaps()`（与超时回收同间隔，独立 `@Scheduled` + `@Transactional`）：

- 扫 `status IN (COMPLETED, FAILED)` 的审查，查该审查有没有终态事件；
- 缺行 → `insertTerminalIfAbsent` 用 `INSERT ... SELECT ... WHERE NOT EXISTS` 原子条件补写一条
  （`run.completed` 或 `run.failed`，按状态选），同步刷 `last_event_at`、进投影、扇出在线订阅；
  多副本竞争时只有一个拿到 affected=1，不会重复补；
- 行在、投影丢了（Redis 降级 / 进程重启）→ 从 DB 原样补回 `replayStore`；
- 每轮有修复就打 **WARN：本轮 dbWritten / replayRestored / failed + 累计修复次数**；
  单条失败单独 WARN 并计入 `failed`，不阻断本轮其余审查。

事务兜不住的三类情况由它收口：历史脏数据、DB 提交后投影写入前进程崩溃、重放存储过期/被清。

## 3. 代价与边界

- 单事务里多了一次 `findById` 和一次状态 UPDATE；事件量不大，可忽略。
- 终态 payload 写失败时该笔审查降级为 `FAILED`（附 `run.failed`），**不会**静默变成 `COMPLETED`；
  要保留原始 findings 需要上游重发或另做载荷压缩，本 ADR 不含。
- 分布式事务边界：DB 与 Redis 之间仍不是原子的，靠「事实源 + 重建 + 看门狗」达到最终一致；
  若未来重放存储外部分离，应重新评估升级到 Outbox（方案 B）。
- 补写事件是**最小契约载荷**（`findings_count=0`、`degraded=true` / `TERMINAL_EVENT_REPAIRED`），
  只保证「终态可见」，不还原丢失的业务明细。
- 演示库真实存在若干「终态状态无终态事件」的历史行，看门狗上线首轮会修它们并打 WARN；
  这是预期行为，不是新脏数据。集成测试用 mock 把扫描范围限定到自己的 review，不改动这些历史行。

## 4. 影响面

| 层 | 是否改动 | 说明 |
|---|---|---|
| Java 编排 | ✅ | `ReviewService` 委托 `ReviewEventWriter`；新增失败补偿/投影重建逻辑 |
| Java Repository | ✅（仅增方法） | `findTerminalReviews`、`findByReviewIdOrderBySeqAsc`、`countByReviewIdAndTypeIn`、`insertTerminalIfAbsent`；`updateLastEventAt` 加 flush/clear |
| Java 看门狗 | ✅ | 新增 `repairTerminalGaps()` |
| 数据库 schema | ❌ | 无新表、无新列；不清理任何历史数据 |
| 事件契约 | ❌ | `events.schema.json` 一行未动 |
| Python 编排 | ❌ | 无改动 |
| 外部中间件 | ❌ | 无 Kafka/MQ/新表 |

## 5. 验收（真实输出）

```
# 全量单测：mvn -o test（无需 MySQL）→ 121 tests, 0 failures, 0 errors
# 真实 DB 验收：mvn -o -Dtest=ReviewEventConsistencyIntegrationTest -Dclauseguard.db.integration=true test
===== T-FAIL | 修复后 =====
DB  review.status          = FAILED          # 不再进入 COMPLETED
DB  review_event 总行数     = 1
DB  终态事件行数            = 1
DB  最后一条事件 type       = run.failed
SSE replaySince(-1)        = [99:run.failed]
LOG ERROR 终态事件写入失败，状态已随事务回滚；补偿 run.failed ...

===== T-OK | 正常路径 =====
DB  review.status          = COMPLETED
DB  review_event 总行数     = 2
DB  最后一条事件 type       = run.completed
DB  review.last_event_at   = 2026-11-12 00:00:02
SSE replaySince(-1)        = [1:run.started, 2:run.completed]

===== T-REPAIR | 看门狗补 =====
DB  review.status          = COMPLETED
DB  终态事件行数            = 1
DB  最后一条事件 type       = run.completed
DB  review.last_event_at   = 2026-11-12 05:19:45
SSE replaySince(-1)        = [2:run.completed]
LOG WARN  review watchdog: 终态缺口修复次数 本轮 dbWritten=1 replayRestored=0 failed=0 累计修复次数=1 scanned=1
```

单测覆盖：`ReviewEventAtomicityTest`（5 例：回滚场景、正常路径、终态优先级、状态关键事件上抛、
投影重建）、`ReviewWatchdogTest`（14 例，其中 7 例为补偿/幂等/WARN 计数）、
`ReviewStreamReplayIntegrationTest::sinceZeroReplaysTerminalEvent`（真实 HTTP `?since=0`）。

## 6. 禁止项核对

- 不吞异常：写失败要么补偿成终态、要么落诊断事件、要么上抛调用方；见 2.3 表。
- 不改事件 schema：所有补写/降级事件复用 `node.failed` / `run.failed` / `run.completed` 既有字段。
- 不引入新中间件：无 Kafka/MQ；连 Outbox 新表都没加，复用 `review_event` 作事实源。
