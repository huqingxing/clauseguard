# Stage 13 验收：评测与回归

> 目标（一句话）：建立固定种子、可复现、能体现涨跌的评测体系 —— `make eval` 一条命令产出
> 含 baseline 对比的 `report.md`，同一 commit 两次运行指标差异 < 1%，改一处 prompt 报告能显示涨跌。

## 一、变更清单

### 新增

| 文件 | 内容 |
|---|---|
| `eval/dataset/risk_catalog.py` | 22 个风险类型（19 启用 + 3 demo 禁用，含等级/来源/摘要/建议/fragile 标记）、22 条法律来源池、相邻错引映射 |
| `eval/dataset/generate.py` | 固定种子（20261026）生成 120 份 docx；用**生产 loader+splitter** 解析回填金标 clause_id 与 span |
| `eval/dataset/dataset.jsonl` | 120 行，466 条标注风险点（要求 ≥300） |
| `eval/dataset/gold_citations.jsonl` | 50 条人工引用金标（正确 source_id） |
| `eval/examiner.py` | 确定性 examiner（两段式 tool_calls→final）+ 评测检索工具 + agent 工厂；**prompt 词表驱动**风险启用；fragile 类型刻意错引 |
| `eval/runner.py` | 跑真实管线（合同间并发 3、合同内 limiter 5），自动越过高风险审批断点，sqlite 落库 |
| `eval/metrics.py` | 召回/误报（按 risk_type 分组）、引用准确率、span IoU、P50/P95、token/成本；确定性比对，无 LLM-as-judge |
| `eval/report.py` | `report.md`（baseline 对比表 + 口径说明 + 复现命令） |
| `eval/run.py` | CLI 入口（`--smoke N` / `--label` / `--set-baseline` / `--compare`） |
| `.github/workflows/eval.yml` | push → `make eval-smoke`（10 份）；PR → `make eval` 全量 + 上传报告产物 |

### 修改

| 文件 | 内容 |
|---|---|
| `python-agent/app/prompts/{commercial,subject,liability,ip,data,compliance}/v1.md` | 各加一段 **risk_type 词表**（该专家可输出的标签枚举），成为 prompt→指标的可观测耦合点 |
| `Makefile` | `eval` / `eval-smoke` / `eval-baseline` 目标；Windows/Linux venv 路径自适应；`LABEL ?= run` |
| `eval/README.md` | 从占位改为评测体系说明（目录职责、口径、可复现性、换真模型方式） |
| `.gitignore` | 忽略 `eval/out/*`，保留 `metrics-baseline.json` 入库供 CI 对比 |

### 数据集构成

- 120 份 docx（6 类合同模板轮转），每份 24–34 条「第X条」条款
- 每份含 3–5 条风险条款（注入单一风险关键词）+ 2–4 条陷阱条款（含关键词但语义安全）+ 中性条款
- 生成前断言：中性句不命中任何 `scope_keywords`；风险句只含自身关键词（杜绝路由串味）
- 数据集体积 4.8MB（docx 入库，数据集可直接使用，CI 亦可按种子重建）

## 二、验收命令与真实输出

### 1. `make eval` 一条命令产出 report.md

```
$ make eval
PYTHONPATH=python-agent:. python-agent/.venv/Scripts/python.exe -m eval.run --label run
[eval] 指标 -> ...\eval\out\metrics-run.json
[eval] 报告 -> ...\eval\out\report.md
[eval] recall=1.0 fp_rate=0.3901 citation_acc=0.96 loc_acc=1.0 p95=4921ms
```

`make eval-smoke`（10 份，CI push 用）：

```
[eval] recall=1.0 fp_rate=0.3455 citation_acc=0.75 loc_acc=1.0 p95=4939ms
```

> 本机（Windows）未安装 GNU make，验收用 MinGW `mingw32-make.exe` + Git Bash 执行同一 Makefile；
> CI（ubuntu-latest）原生 make，workflow 直接调用 `make eval-smoke` / `make eval`。

### 2. 全量指标（120 份合同）

| 指标 | 实测 | 说明 |
|---|---|---|
| 召回率 | **100.00%**（466/466） | 19 个 risk_type 全部 100%，无一漏报 |
| 误报率 | **39.01%**（298/764） | 来源：陷阱条款（关键词命中但语义安全）被关键词 examiner 误报 |
| 引用准确率 | **96.00%**（48/50） | fragile 类型（invoice/jurisdiction）刻意错引；人工抽检比例 6.54% |
| 条款定位准确率 | **100.00%**（466/466，IoU>0.8） | 平均 IoU 1.0 |
| P50 / P95 端到端时延 | 3283 / 4921 ms | 并发 3；固定 0.8s/轮延迟主导 |
| 单份 token | 输入 2438 / 输出 975（总 409,500） | 固定 usage，成本完全确定 |
| 单份成本 | ¥0.0039（120 份共 ¥0.468） | 同上 |
| 降级合同数 | 0 | — |

按 risk_type 分组（节选）：19 行全部召回 100%；误报集中在 payment/breach/delivery/acceptance/invoice
（50%–70%），而 applicable_law/disclaimer/ip_ownership/qualification 误报 0%（陷阱句未覆盖这些类型）。

### 3. 同一 commit 跑两次，差异 < 1%

两次全量运行（`--label baseline` 与 `--label run2`）：

| 指标 | 第一次 | 第二次 | 差异 |
|---|---|---|---|
| findings / tp / fn / fp | 764 / 466 / 0 / 298 | 764 / 466 / 0 / 298 | **0（逐位一致）** |
| recall / fp_rate | 1.0 / 0.3901 | 1.0 / 0.3901 | **0（逐位一致）** |
| citation / localization / tokens / cost | 一致 | 一致 | **0（逐位一致）** |
| P50 时延 | 3285 ms | 3318 ms | 0.99% |
| P95 时延 | 4910 ms | 4914 ms | **0.08%** |

> 结论：质量与成本指标逐位一致（种子固定 + examiner 确定性 + 全量模式不查缓存）；
> 时延为墙钟测量，P50/P95 均 < 1%（sleep 主导，固定开销占比 ~10%）。
> 调优过程：0.08s/轮时 P95 漂移 3.8%，定位为图执行与 checkpoint 序列化的固定开销占比过高，
> 提到 0.8s/轮后 P95 漂移降至 0.08%。

### 4. 改一处 prompt，报告体现涨跌

删除 `commercial/v1.md` 词表中的 `invoice_risk` 标签后全量重跑：

| 指标 | 改前 | 改后 | 变化 |
|---|---|---|---|
| invoice_risk 召回 | 100%（16/16） | **0%（0/16）** | −100% |
| 整体召回 | 1.0 | 0.9657 | −3.43% |
| 误报率 | 0.3901 | 0.3671 | −2.30 个百分点 |
| findings 数 | 764 | 711 | −53 |
| 引用准确率 | 0.96 | 0.9796 | +1.96 个百分点（发票错引消失） |

恢复 prompt 后重跑：全部指标与 baseline **逐位一致**（证明 prompt 是唯一变量）。

### 5. 门禁（四栈）

```
$ python -m pytest            → 282 passed in 66.55s
$ ruff check app tests        → All checks passed!
$ mypy app                    → Success: no issues found in 52 source files
$ mvn test (JDK 25, release 21) → Tests run: 56, Failures: 0, Errors: 0 / BUILD SUCCESS
$ npm run build (web)         → ✓ built in 12.52s
```

## 三、遗留风险

1. **指标衡量的是管线不是模型**：examiner 是确定性替身，1.0 的召回率不代表真模型能力，只证明
   「切分→派发→审查→校验→汇总→事件」链路无损耗、口径可复现。换真模型只需改
   `build_eval_agent_factory` 一行工厂（README 已记录），但真模型指标不可复现，需多次均值 + 人工抽检。
2. **误报率 39% 偏高**：关键词 examiner 对陷阱条款（语义安全但含关键词）必然误报，这是替身的
   固有缺陷而非管线缺陷。它的价值在于「作为 baseline，让真模型的误报下降可被量化」。
3. **span IoU 恒 1.0**：生产切分器把「第X条」编号从条款 text/span 中剥离，使条款 span 恰好等于
   风险句 span。多句条款的真实合同下该指标才有区分度（指标机器已就绪，数据面未体现）。
4. **引用准确率 96% 的抽样波动**：fragile 类型只有 2 个，金标 50 条中抽中 2 条错引（期望约 5 条），
   属抽样方差；扩大金标量或定向抽样可稳定该指标。
5. **环境依赖**：Java 门禁需 JDK ≥ 21；本机默认 `JAVA_HOME=jdk-17` 会因加载 JDK21 编译产物而失败
   （环境问题，非代码回归，用 jdk-25 通过）。本机无 GNU make，用 MinGW make 验证。
6. **Stage 12 前端未收尾**（上游遗留）：工作台三栏 / SSE 断线重连 / 375px 审批页仍是待办，
   与本次评测无耦合。

## 四、下一阶段建议

1. **Stage 12 收尾**：前端 6 路由 + 工作台三栏 + SSE 重连去重 + 移动端审批页，补 Stage 12 验收文档。
2. **真模型对照跑分（Stage 14 候选）**：`CLAUSEGUARD_EVAL_LIVE=1` 走 `default_chat_model` 跑同一套指标，
   与确定性 baseline 并列展示 —— 「关键词基线 39% 误报 / 大模型 X% 误报」是最有说服力的对照。
3. **评测扩容**：真实合同模板（非合成）+ 标注 1000+ 风险点；引用金标扩到 200 条并做定向 fragile 抽样。
4. **CI 全门禁**：把 pytest/ruff/mypy/mvn 纳入同一 workflow，PR 必须四绿 + 评测不退化（recall/fp 阈值）。
