# 召回塌方归因实验（lab/exp/attribution）

诊断目标：区分 96.6%→29.1% 召回塌方的四个竞争假说（H1 引用门槛 / H2 不敢报 / H3 识别不足 / H4 评测污染）。
**只诊断不治疗**：本目录下的脚本与文档不得修改 `app/` 的默认行为；临时开关一律走环境变量。

## 环境要求
- 真模型可达：`CLAUSEGUARD_AGENT_LLM_API_KEY`（或进程环境 `LLM_API_KEY`）+ DashScope compatible-mode 端点。
  **本沙箱下 chat 端点返回 HTTP 400（与验收期 embeddings 400 同源），故 E2/E3 无法在此执行**。
- E3 还需本地 pgvector：`127.0.0.1:55432` 已灌入 `S5_context` 向量（见 `docs/retrieval-stage2-acceptance.md`）。

## 脚本与命令（仓库根执行）
```bash
PY="python-agent/.venv/Scripts/python.exe"
# E1（离线，复用阶段7已落盘的真LLM产物；测 H1）
PYTHONPATH='python-agent;.' $PY lab/exp/attribution/e1_citation_threshold.py

# E4（离线，gold 溯源 + 人工抽检20条；测 H4）
PYTHONPATH='python-agent;.' $PY lab/exp/attribution/e4_gold_independence.py

# E2（需真模型；测 H3：无检索纯识别）
PYTHONPATH='python-agent;.' $PY lab/exp/attribution/run_e2_e3.py --mode e2 --n 30

# E3（需真模型 + PG；测 H2：有检索但弱化证据）
PYTHONPATH='python-agent;.' $PY lab/exp/attribution/run_e2_e3.py --mode e3 --n 30

# 连通性探针
PYTHONPATH='python-agent;.' $PY lab/exp/attribution/probe_llm.py
```

## 状态
| 实验 | 状态 | 数据来源 |
|---|---|---|
| E1 / H1 | ✅ 已离线跑完 | 阶段7真LLM产物 mock/live |
| E4 / H4 | ✅ 已离线跑完 | 生成器溯源 + 20条抽检 |
| E2 / H3 | ⛔ 本沙箱阻塞（DashScope 400） | 脚本就绪，可达环境重跑 |
| E3 / H2 | ⛔ 本沙箱阻塞（DashScope 400） | 脚本就绪，可达环境重跑；H2 vs H3 暂由 mock-vs-live 天然对照支撑 |

## 设计要点
- E2 用子类化 `ExpertSpec`（内存覆写 `load_system_prompt`，**不写磁盘**）把工具置空 `tools=()`，仅给条款+风险清单。
- E3 保留 live 检索，但在系统提示追加「检索仅供参考，不足时仍须给出风险判断并标 unverified」。
- 两实验均设 preflight：LLM 不可达时明确 UNREACHABLE（exit 3）且不写误导性 metrics。
