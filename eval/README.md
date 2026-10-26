# eval/ 评测与回归（Stage 13）

没有评测集，这个项目在简历上就不成立。本目录是「能跑出指标、能证明涨跌」的评测体系：
固定种子数据集 + 确定性 examiner + 规则判分 + 与 baseline 的对比报告。

## 一条命令

```bash
make eval            # 全量 120 份 → eval/out/metrics-run.json + eval/out/report.md
make eval-smoke      # 前 10 份冒烟（CI push）
make eval-baseline   # 重建基线（prompt 变更后重设）
```

Windows 无 GNU make 时等价命令：

```bash
PYTHONPATH=python-agent:. python-agent/.venv/Scripts/python.exe -m eval.run --label run
```

## 目录

| 文件 | 职责 |
|---|---|
| `dataset/risk_catalog.py` | 22 个风险类型（19 启用 + 3 demo 禁用）、22 条法律来源池、fragile 相邻错引映射 |
| `dataset/generate.py` | 固定种子生成 120 份 docx + `dataset.jsonl`（466 条标注）+ `gold_citations.jsonl`（50 条引用金标）；金标 clause_id/span 由生产 loader+splitter 真实解析回填 |
| `dataset/dataset.jsonl` | `contract_id / path / annotations[{clause_no,risk_type,risk_level,span_hint}] / gold_clauses` |
| `examiner.py` | 确定性 examiner（两段式 tool_calls→final）+ 评测检索工具 + agent 工厂；**prompt 词表驱动**风险启用 |
| `runner.py` | 建图跑真实管线（合同间并发 3、合同内 limiter 5），处理高风险 interrupt，sqlite 落库 |
| `metrics.py` | 召回/误报（按 risk_type）、引用准确率、span IoU、P50/P95 时延、token/成本 |
| `report.py` | `report.md` 渲染（含 baseline 对比表、口径说明、复现命令） |
| `run.py` | CLI：数据集 → 跑批 → 指标 → 报告 |
| `out/` | `metrics-*.json` / `report.md` / `results.db`（每次结果落库） |

## 指标口径

- **召回率**：金标 `(contract_id, clause_id, risk_type)` 被终态 findings 命中（非 stale）
- **误报率**：系统产出但金标无对应标注的 finding / 全部产出（陷阱条款是主要来源）
- **引用准确率**：50 条人工金标逐条比对 `source_id`；`invoice_risk` / `jurisdiction_risk`
  为 fragile 类型（刻意引相邻法条），故该指标恒 <100%，是评测信号而非 bug
- **条款定位**：系统条款 span 与金标风险句 span 的 IoU > 0.8 记命中
- **时延**：P50/P95 端到端（图执行，不含脚手架解析）；examiner 固定 0.8s/轮延迟使其可复现
- **判分**：全部规则比对，**不使用 LLM-as-judge**；引用准确率为人工金标比对，
  抽检比例 = 金标条数 / 系统全部产出（报告如实标注）

## 可复现性

同一 commit 重复运行：质量/成本指标**逐位一致**（种子固定 + examiner 确定性 +
全量模式不查缓存），P50/P95 漂移 < 1%（sleep 主导）。
`results.db` 按 label 追加，可跨 run 审计。

## prompt → 指标耦合

examiner 只检测「系统提示词词表里出现的 risk_type」。改 `python-agent/app/prompts/<expert>/v1.md`
的词表行 → 该风险不再被检测 → 报告召回/误报同步涨跌（验收实测：删 `invoice_risk`
后 invoice 召回 100%→0%，整体召回 1.0→0.9657）。

## 换真模型

`examiner.build_eval_agent_factory` 是唯一模型入口：改为返回
`build_expert_agent(spec, default_chat_model())` 即可用真模型跑同一套指标与报告，
管线、数据集、判分零改动（真模型指标不可复现是预期，需以人工抽检与多次均值呈现）。
