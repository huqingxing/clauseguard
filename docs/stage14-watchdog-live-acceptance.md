# 阶段 14 看门狗 · 实机验收报告

> 目标：确认「卡死的 RUNNING 自动转 FAILED」在真实 MySQL + 真实调度下端到端生效。
> 验收环境：java-service `:8099`（提交后代码），真实 MySQL `clauseguard`，python-agent 关闭（不影响看门狗）。
> 默认配置：`clauseguard.review.watchdog.enabled=true`、`timeout=30m`、`interval=5m`。

## 验收过程中发现并修复的两个真 bug（单测 mock 没覆盖）

1. **JPQL 把 `Instant` 绑 `DATETIME` 列 → 时区偏移，候选集恒为空**
   - `findStaleRunningIds` / `reapStuckRunning` 原用 JPQL（`@Query` 写实体名、参数类型 `Instant`）。
   - Hibernate 把 `Instant` 绑 `DATETIME(3)` 列时会系统性偏 8 小时，导致 `COALESCE(last_event_at, created_at) < cutoff` 恒为 FALSE —— 看门狗静默地一条都选不出来，连卡了几小时的老 `14a1b626` 都纹丝不动。
   - 修复：两个方法都改为**原生 SQL + UTC 字符串参数**（与阶段 13 已验证的 `updateLastEventAt` 同一套路）。本环境下 `TIMESTAMP` 与 `DATETIME` 显示在同一墙钟框架，UTC 字符串直接比较既正确又无需 `CONVERT_TZ`（避免依赖时区表）。

2. **`@Modifying` 查询缺事务 → `TransactionRequiredException` 被 catch 吞掉**
   - `reapStuckRunning`（`@Modifying`）在 `scan()` 内调用，而 `scan()` 没有事务上下文，真实运行抛 `TransactionRequiredException`，被 `@Scheduled` 入口的 try/catch 记 WARN 后丢弃，照样不回收。
   - 修复：在带 `@Scheduled` 的入口方法 `reapStuckRunning()` 上加 `@Transactional`，事务覆盖整个 `scan()` + reap（自调用发生在代理开启的事务内，规避自调用绕过代理的问题）。

## 实机验收步骤与真实输出

### 1. 候选集 SQL（修复后直接跑，证明能正确选出卡死 RUNNING）
```sql
SELECT r.id FROM review r
WHERE r.status = 'RUNNING'
  AND COALESCE(r.last_event_at, r.created_at) < UTC_TIMESTAMP() - INTERVAL 30 MINUTE;
-- 返回：14a1b626-...（老卡死行）、wd-test-running-0001（受控样本）
```

### 2. 受控样本
| id | status | last_event_at (UTC) | 角色 |
|---|---|---|---|
| `14a1b626-...` | RUNNING | 02:57:12 | 真实遗留卡死行（活动停在 ~10h 前） |
| `wd-test-running-0001` | RUNNING | `UTC_TIMESTAMP()-40m` | 受控卡死样本 |
| `wd-test-awaiting-0001` | AWAITING_APPROVAL | `UTC_TIMESTAMP()-40m` | **反例**：审批中，绝不能被动 |

### 3. 触发看门狗（临时用 `interval=10s` 加速验证，提交代码仍默认 5m）
轮询结果（节选）：
```
[1] running=RUNNING awaiting=AWAITING_APPROVAL old14a=FAILED   ← 14a1b626 已被回收
...
[1] running=FAILED awaiting=AWAITING_APPROVAL                  ← wd-test-running-0001 已被回收
```

### 4. 回收证据（最终状态）
| id | status | run.failed 事件数 |
|---|---|---|
| `14a1b626-...` | **FAILED** | 1 |
| `wd-test-running-0001` | **FAILED** | 1 |
| `wd-test-awaiting-0001` | **AWAITING_APPROVAL（不变）** | 0 |

`run.failed` 事件 payload 示例（14a1b626）：
```json
{"seq":73,"ts":"2026-10-28T05:31:27.127806200Z","type":"run.failed",
 "code":"REVIEW_TIMEOUT","message":"审查超过 PT30M 没有任何事件，判定为卡死并终止","retryable":true}
```

### 5. 反例 / 并发安全
- **AWAITING_APPROVAL 不被回收**：全库 AWAITING_APPROVAL 计数从 17 增至 18（仅多了一条受控 `wd-test-awaiting-0001`），原有 17 条审批中审查全部不动 —— 审批中的真实合同永不被误杀。
- **同一条只回收一次（并发幂等）**：条件更新 `WHERE status=RUNNING AND COALESCE(...) < cutoff` 保证多副本只有拿到 `affected=1` 的那个才补 `run.failed` 事件；单测 `onlyOneSideWinsWhenTwoInstancesRaceOnTheSameRow` 已锁住（赢的写 1 次事件、输的 0 次），实机每条受控行 `run.failed` 事件恰为 1 条。

## 验收结论
- 看门狗在真实调度下能正确识别并回收超时卡死的 RUNNING，补 `run.failed(REVIEW_TIMEOUT, retryable=true)` 终态事件。
- AWAITING_APPROVAL（及 COMPLETED/FAILED）严格排除，不会误伤审批中的合同。
- 并发幂等由条件更新 + `affected=1` 判定保证。
- 单测（`ReviewWatchdogTest`）：`shouldReap` 三断言 + 扫描/并发断言 + 测试 profile 关调度，全绿。

## 提交
- 修复随「fix(java): 看门狗实机验收修复」提交（在阶段 14 初始提交之后）。
- 验收用的合成行（`wd-test-*`、`14a1b626` 等本会话测试产物）已在验收后清理，库内无遗留。
