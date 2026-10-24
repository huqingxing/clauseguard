# Stage 8 验收报告｜Critic 确定性校验与打回重审

- 日期：2026-10-24
- 语料：tests/agents/fixtures.stage8_corpus（3 通过 + 1 可修复 + 1 永远失败）

## 验收结果

| 验收点 | 结果 |
|---|---|
| 闭环修复（r-02 打回后修正通过） | ✅ 并行/串行双拓扑 |
| 轮数上限收敛（r-03 两轮后 unverified，图不死循环） | ✅ critic_failures=2 即上限 |
| 不误伤通过项（c-01/s-01/l-05 风险档不变） | ✅ |
| high 风险触发人工审批断点并恢复至 END | ✅ interrupt -> resume([]) -> render |
| 埋点成对 + schema 校验 | ✅ critic 3 对 node.started/finished |

## 实现要点

- 三类校验全部纯代码（无 LLM 自评）：引用白名单 / 证据落地（归一化 + 覆盖率 ≥0.9） / high 必有 suggestion；
- 打回经墓碑删旧版 + Send/serial_review 重审，指令来自版本化模板 prompts/critic/v1.rework.md；
- 预算耗尽或轮数用尽 -> 标 unverified 放行（预算耗尽同时 degraded），图必然收敛。
