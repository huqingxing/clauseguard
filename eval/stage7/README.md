# 阶段 7｜把检索改进量化到业务指标

回答一个问题：**检索从假数据换成真法条之后，审查质量到底变没变？**

## 实验设计（控制变量）

| 项 | 改造前（mock 组） | 改造后（live 组） |
| --- | --- | --- |
| 模型 | 阿里云百炼 `qwen-plus`（真模型） | 同一个 `qwen-plus` |
| 数据集 | `eval/dataset/` 前 30 份 | 同一批 30 份 |
| 图 / prompt / 金标 / 判分 | 生产同一套 | 生产同一套 |
| **检索工具返回** | 22 条固定样例（`src-*`），与查询无关 | pgvector 真实法条（`chunk_id`），随查询变化 |

唯一变量是检索。两组都跑真模型，不用确定性 examiner 冒充。

## 复现（仓库根）

```bash
export PYTHONPATH='python-agent;.'
python-agent/.venv/Scripts/python.exe -m eval.stage7.run --mode mock --n 30 --concurrency 2
python-agent/.venv/Scripts/python.exe -m eval.stage7.run --mode live --n 30 --concurrency 2
python-agent/.venv/Scripts/python.exe -m eval.stage7.citation_audit --build
# 人工逐条填写 eval/stage7/annotations.jsonl（30 行 verdict + reason）
python-agent/.venv/Scripts/python.exe -m eval.stage7.citation_audit --render
python-agent/.venv/Scripts/python.exe -m eval.stage7.acceptance
```

前置：`CLAUSEGUARD_AGENT_LLM_API_KEY` 已配置（百炼）；本机 pgvector 库在
`127.0.0.1:55432`（见 `docs/retrieval-stage2-acceptance.md`），且 `S5_context`
已按 `retrieval.yaml` 灌入向量。密钥与 DSN 都不写进代码。

## 产物

| 路径 | 内容 |
| --- | --- |
| `eval/out/stage7/{mock,live}/results.jsonl` | 每份合同的终态 findings（含 citation） |
| `eval/out/stage7/{mock,live}/metrics.json` | 召回率、混淆计数、诊断计数、token/成本 |
| `eval/out/stage7/citation_pool.jsonl` | 30 条待抽检引用 + 条款原文 + 法条原文 |
| `eval/stage7/annotations.jsonl` | 单一标注者的逐条判定与理由 |
| `eval/out/stage7/citation_audit.md` | 30 行明细表 + 严格/宽松准确率 |
| `docs/rag-lab.md` | 对外可读的完整实验报告 |

## 口径要点

- 风险召回率沿用 `eval/metrics.py`：金标 `(contract_id, clause_id, risk_type)`
  被终态非 stale findings 命中即 TP。
- 引用准确率**不**沿用金标 ID 比对：`gold_source_id` 是 mock 池 ID，与改造后
  `chunk_id` 不可比；改为人工读法条原文，判断它是否支撑结论。
- 无引用的 `unverified` finding 不进 30 条引用抽检池，但会在报告里单列数量，
  不藏起来。
