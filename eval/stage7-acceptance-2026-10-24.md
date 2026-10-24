# Stage 7 六专家并行与结果合并 验收报告

- 日期：2026-10-24
- 模型：MockScriptedChatModel（CI 确定性替身；真实模型需 CLAUSEGUARD_AGENT_LLM_* 环境变量，
  harness 相同）
- 延迟注入：每次模型调用 sleep 20ms（并行/串行对照的唯一变量）
- 语料：tests/agents/fixtures.py stage7_corpus()，50 条款 / 6 专家各 8 条 + 2 条跨专家重叠加名
- Prompt：app/prompts/<expert>/v1.md（版本化文件，非硬编码）

## 指标汇总

| 指标 | 实测 | 目标 | 结论 |
| --- | --- | --- | --- |
| 并行 vs 串行 P95 | serial 3286ms → parallel 1012ms，下降 69.2% | ≥ 60% | ✅ |
| findings 无重复 | 20 条 / 20 个唯一键 | 集合相等 | ✅ |
| 伪造/越界引用 | 0（全部 finding 有有效 citations） | 白名单强制 | ✅ |
| 预算降级（max_tokens=1） | degraded=true，跳过低级 | 优雅降级 | ✅ |
| 20 并发事件流 | 20 个请求，seq 各自 1..6，run_id 互异 | 不串流 | ✅ |
| 节点级埋点 | node.finished 成对，duration_ms/tokens 齐全 | 全覆盖 | ✅ |
| token 记账 | in=15600 out=6240 cost=0.0250 元 | 经 merge_budget 累加 | ✅ |

串行样本耗时（ms）：[3258, 3274, 3280, 3286, 3275]
并行样本耗时（ms）：[882, 1003, 941, 1012, 944]

## 每专家节点埋点（并行样本）

| 专家节点 | 分片数 | 节点耗时合计ms | token | findings |
| --- | --- | --- | --- | --- |
| review.commercial | 1 | 172 | 3360 | 4 |
| review.compliance | 2 | 1608 | 4200 | 3 |
| review.data | 2 | 1342 | 3780 | 4 |
| review.ip | 2 | 1047 | 3780 | 3 |
| review.liability | 1 | 406 | 3360 | 3 |
| review.subject | 1 | 266 | 3360 | 3 |

## findings 分布

- 按专家：{'commercial_expert': 4, 'subject_expert': 3, 'liability_expert': 3, 'ip_expert': 3, 'data_expert': 4, 'compliance_expert': 3}
- 按风险类型：{'payment_risk': 2, 'acceptance_risk': 1, 'invoice_risk': 1, 'qualification_risk': 1, 'authorization_risk': 1, 'succession_risk': 1, 'breach_risk': 1, 'force_majeure_risk': 1, 'disclaimer_risk': 1, 'ip_ownership_risk': 1, 'open_source_risk': 1, 'confidentiality_dispute_risk': 1, 'data_processing_risk': 1, 'cross_border_risk': 1, 'credential_risk': 1, 'data_dispute_risk': 1, 'jurisdiction_risk': 1, 'applicable_law_risk': 1, 'export_control_risk': 1}
- 跨专家重叠加名条款 x-01（ip+compliance）/ x-02（data+compliance）各产出 2 条原始 finding，
  经 merge_findings 按 (clause_id, risk_type) 收敛为 1 条，citations 取并集去重。

## 说明

1. 并行拓扑：ingest → dispatch（Send 按专家分片，每片 ≤8 条）→ expert_review ×N → END；
   分片内条款 asyncio.gather 并发，模型调用经 per-run asyncio.Semaphore(5) 限流。
2. 预算控制：dispatch 规划门（预估成本养不起低优先级专家即跳过）+ 节点入口门
   （跑到一半预算耗尽即跳过）+ 节点后置记账（实测 tokens/cost 经 merge_budget 累加）。
   三种超限（max_tokens/max_cost_cny/deadline_ts）均有单测覆盖。
3. 失败语义：单分片异常被捕获后发 node.failed 并置 degraded，其余分支继续；
   装配期错误（缺 limiter）显式抛出，不吞成 node.failed。
4. 复跑：`cd python-agent && uv run pytest tests/agents/test_acceptance_stage7.py -q`。
