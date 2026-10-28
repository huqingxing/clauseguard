# 阶段 13 验收报告 · 给 review 加 `last_event_at`

> 目标：在 `review` 表增加「最后一次活动时间」`last_event_at`，让看门狗（阶段 14）能据此判断一条审查是否卡死。
> 验收环境：真实 MySQL(3306) + Redis(6379) + python-agent(:8000, mock) + java-service(:8099)。
> 全程未改 `contracts/openapi.yaml`、未用 `@PreUpdate`、未加 `NOT NULL`；`last_event_at` 不进 OpenAPI。

## 1. 迁移（V5）在已有数据跑通

`flyway_schema_history` 已记录版本 `5`（description: `review last event at`）：

```
version  description
5        review last event at
```

迁移逻辑：新增 `last_event_at DATETIME(3) NULL` → 用相关子查询回填
`last_event_at = (SELECT MAX(ts) FROM review_event WHERE review_id=r.id)` → 对仍为空者兜底填 `created_at`。
保持 `NULL` 不加 `NOT NULL`。

## 2. 迁移前后 NULL 计数

| 阶段 | `last_event_at IS NULL` |
|------|------------------------|
| 列刚加完（未回填） | 全部（历史 48 条均 NULL） |
| V5 回填后（历史） | **0** |
| 本次对齐修正后（全量 51 条） | **0** |

> 注：本次验收发现 3 条在「旧截断版实时写入」下产生的偏差行（见 §5），已用与 V5 回填一致的
> 幂等 `UPDATE`（`last_event_at = MAX(review_event.ts)`，无事件则 `created_at`）一次性对齐。
> 对齐后全表 `last_event_at <> MAX(ts)` 计数 = **0**。

## 3. 抽查 LIMIT 5（对齐后）

```
id                                  status            created_at           last_event_at
8977558a-0b69-4b30-9e79-7268ecea8b12 COMPLETED         2026-10-28 04:40:38 2026-10-28 04:41:23.000
51c2d800-c1ef-4b68-b9f6-4da7ffb52026 AWAITING_APPROVAL 2026-10-28 04:33:21 2026-10-28 04:33:23.000
03bd02fe-69fe-4d2b-8e12-23c9097c11a5 COMPLETED         2026-10-28 03:40:00 2026-10-28 03:40:04.000
14a1b626-f690-4929-8798-eecc11f4ff76 RUNNING           2026-10-28 02:57:09 2026-10-28 02:57:12.000
7d7bec2e-9213-4db6-9df7-473e5f39fd2d COMPLETED         2026-10-28 02:27:20 2026-10-28 02:47:16.000
```

## 4. 测试 #3：跑新审查，确认 `last_event_at` 随事件推进且 == MAX(ts)

新建审查 `8977558a`（针对 demo 合同 `0cf83b8f`）：

- 触发 human_approval 中断 → `AWAITING_APPROVAL`，中断瞬间：
  `last_event_at = 2026-10-28 04:40:40.000`，`MAX(ts) = 04:40:40`，**diff = 0**。
- 续跑完成后：
  `last_event_at = 2026-10-28 04:41:23.000`，`MAX(ts) = 04:41:23`，**diff = 0**。

结论：实时写入路径下 `last_event_at` 严格等于 `MAX(review_event.ts)`，无毫秒尾巴、无 1 秒偏差。

## 5. 测试 #4：审批续跑场景（approve + resume）

对 `8977558a` 执行审批续跑（带乐观锁基线 `contract_version=1` / `content_hash=7ee8…e1e7`）：

| 时点 | `last_event_at` | 说明 |
|------|----------------|------|
| 中断后（approve 前） | `2026-10-28 04:40:40.000` | 仅由中断前事件驱动 |
| approve 后**立即**读 | `2026-10-28 04:40:40.000` | **审批动作本身不写 `last_event_at`**（只把状态 `AWAITING_APPROVAL→RUNNING`） |
| resume 完成后 | `2026-10-28 04:41:23.000` | 由续跑新增事件驱动（+43s） |

续跑新增事件（ts 均 > 04:40:40）：`node.started` / `node.finished`×2 / `run.completed`（ts=04:41:23）。
最终 `last_event_at = MAX(ts)`，diff = 0，且**明显不等于审批时刻**（时间戳由续跑事件推进，而非审批逻辑写入）。

> 反例（乐观锁）：首次审批因 `contract_version` 传空被 `CONTRACT_CHANGED` 拒绝，状态与 `last_event_at` 均不变 —— 证明拒绝路径同样不污染时间戳。

## 6. 修复记录：1 秒偏差根因

初版实时写入用 `Instant.truncatedTo(SECONDS)` 截断到秒，但 `review_event.ts` 是 `TIMESTAMP(fsp=0)`，
MySQL **四舍五入**（如 `22.935995 → 23`），导致落在某秒后半段的事件让 `last_event_at` 比 `MAX(ts)` 少 1 秒。
修复：Java 侧改用 round-half-up（`+500ms` 后截断）与列定义严格一致；V5 回填本来就用 `MAX(ts)`（列值），
两条路径现在产出的秒级值完全相等。单元测试 `eq("2026-10-23 10:00:00")` 因用整秒时间戳不受影响。

## 7. 单元测试

`mvn -o test` 全绿（含阶段 14 看门狗断言 `r-order / r-race marked FAILED`）：

```
TEST_EXIT=0
```

## 8. 验收结论

- 迁移 V5 在真实库跑通，NULL 计数 0；全表 `last_event_at == MAX(ts)`（0 偏差）。
- #3 新审查实时推进、`last_event_at == MAX(ts)`（diff 0）。
- #4 审批续跑后 `last_event_at` 由续跑事件推进（非审批时刻），diff 0。
- 禁止项全部遵守：未改 OpenAPI、未用 `@PreUpdate`、未加 `NOT NULL`。
