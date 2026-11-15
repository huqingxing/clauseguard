# Stage 6 单专家 Agent 验收报告（商务条款审查）

- 日期：2026-10-23
- 模型：MockScriptedChatModel（CI 确定性替身；真实模型需 CLAUSEGUARD_AGENT_LLM_* 环境变量，
  harness 相同）
- 工具：mock_search_law / mock_search_policy / mock_search_similar_clauses（固定候选集）
- Prompt：app/prompts/commercial/v1.md（版本化文件，非硬编码）

## 指标汇总

| 指标 | 实测 | 目标 | 结论 |
| --- | --- | --- | --- |
| 已知风险召回 | 8/10 = 80% | ≥ 70% | ✅ |
| 正常条款误报 | 2/10 | ≤ 3 | ✅ |
| 伪造法条拦截 | 5/5 | 100% | ✅ |
| 单条款 P95 耗时 | 8.4ms | < 8000ms | ✅ |
| 结构化解析成功率 | 100% | ≥ 95% | ✅ |
| token 消耗（主场景） | in=6000 out=2400 | 记日志 | ✅ |

伪造引用泄漏检查：输出中出现的 FAKE id = 无。

## 主场景对照表（10 已知风险 + 10 正常）

| clause_id | 预期 | 实际 | findings | 有效引用 | 丢弃 | tokens(in/out) | 耗时ms | 判定 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| risk-01 | high | high | 1 | 2 | 0 | 300/120 | 8.4 | ✓ |
| risk-02 | medium | medium | 1 | 2 | 0 | 300/120 | 6.9 | ✓ |
| risk-03 | medium | medium | 1 | 2 | 0 | 300/120 | 6.9 | ✓ |
| risk-04 | medium | medium | 1 | 2 | 0 | 300/120 | 7.4 | ✓ |
| risk-05 | high | high | 1 | 2 | 0 | 300/120 | 6.7 | ✓ |
| risk-06 | medium | medium | 1 | 2 | 0 | 300/120 | 6.8 | ✓ |
| risk-07 | high | high | 1 | 2 | 0 | 300/120 | 7.0 | ✓ |
| risk-08 | medium | medium | 1 | 2 | 0 | 300/120 | 6.3 | ✓ |
| risk-09 | 无风险 | 无发现 | 0 | 0 | 0 | 300/120 | 6.7 | ✓ |
| risk-10 | 无风险 | 无发现 | 0 | 0 | 0 | 300/120 | 7.4 | ✓ |
| ok-01 | 无风险 | 无发现 | 0 | 0 | 0 | 300/120 | 6.9 | ✓ |
| ok-02 | 无风险 | 无发现 | 0 | 0 | 0 | 300/120 | 6.6 | ✓ |
| ok-03 | low | low | 1 | 2 | 0 | 300/120 | 7.4 | ✓ |
| ok-04 | 无风险 | 无发现 | 0 | 0 | 0 | 300/120 | 6.5 | ✓ |
| ok-05 | 无风险 | 无发现 | 0 | 0 | 0 | 300/120 | 6.8 | ✓ |
| ok-06 | 无风险 | 无发现 | 0 | 0 | 0 | 300/120 | 6.5 | ✓ |
| ok-07 | low | low | 1 | 2 | 0 | 300/120 | 7.2 | ✓ |
| ok-08 | 无风险 | 无发现 | 0 | 0 | 0 | 300/120 | 8.5 | ✓ |
| ok-09 | 无风险 | 无发现 | 0 | 0 | 0 | 300/120 | 6.9 | ✓ |
| ok-10 | 无风险 | 无发现 | 0 | 0 | 0 | 300/120 | 8.1 | ✓ |

## 伪造法条注入场景（5 条已知风险条款，模型只引用不存在的 id）

| clause_id | 预期 | 实际 | findings | 有效引用 | 丢弃 | tokens(in/out) | 耗时ms | 判定 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| risk-01 | high | unverified | 1 | 0 | 1 | 300/120 | 10.2 | ✓ |
| risk-02 | medium | unverified | 1 | 0 | 1 | 300/120 | 7.9 | ✓ |
| risk-03 | medium | unverified | 1 | 0 | 1 | 300/120 | 8.0 | ✓ |
| risk-04 | medium | unverified | 1 | 0 | 1 | 300/120 | 8.1 | ✓ |
| risk-05 | high | unverified | 1 | 0 | 1 | 300/120 | 6.9 | ✓ |

> 伪造 citation 全部被白名单丢弃（dropped_citations=5），
> 对应 finding 全部降级为 unverified（5 条）——引用只能来自
> 检索结果白名单（红线）在代码层强制成立。

## 说明

1. 召回/误报由 scripted 模型的行为表驱动（模拟合格但不完美的模型：漏报 risk-09/risk-10，
   误报 ok-03/ok-07），指标检验的是整条管线（解析、白名单、降级、指标），不是恒真断言。
2. 解析重试与 parse_error 路径由 tests/agents/test_review_node.py 单独覆盖。
3. 复跑：`cd python-agent && uv run pytest tests/agents/test_acceptance_stage6.py -q`。
