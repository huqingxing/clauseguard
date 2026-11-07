# 召回塌方归因实验（lab/exp/attribution）

诊断目标：区分 96.6%→29.1% 召回塌方的四个竞争假说（H1 引用门槛 / H2 不敢报 / H3 识别不足 / H4 评测污染）。
**只诊断不治疗**：本目录下的脚本与文档不得修改 `app/` 的默认行为；临时开关一律走环境变量。

## 环境要求
- 真模型可达：`CLAUSEGUARD_AGENT_LLM_API_KEY`（或进程环境 `LLM_API_KEY`）+ DashScope compatible-mode 端点。
  **本沙箱下 chat/embeddings 端点均返回 HTTP 401（空密钥），故 E2/E3/E5 无法在此执行**；PG `127.0.0.1:55432` 在线。
- E3/E5 还需本地 pgvector：`127.0.0.1:55432` 已灌入 `S5_context` 向量（见 `docs/retrieval-stage2-acceptance.md`）。

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

# E5（需真模型+PG；校验3：检索覆盖度 top-20，20个gold点）
PYTHONPATH='python-agent;.' $PY lab/exp/attribution/e5_retrieval_coverage.py
```

## 状态
| 实验 | 状态 | 数据来源 |
|---|---|---|
| E1 / H1 | ✅ 已离线跑完 | 阶段7真LLM产物 mock/live |
| E4 / H4 | ✅ 已离线跑完 | 生成器溯源 + 20条抽检 |
| E2 / H3 | ⛔ 本沙箱阻塞（DashScope 401） | 脚本就绪，可达环境重跑 |
| E3 / H2 | ⛔ 本沙箱阻塞（DashScope 401） | 脚本就绪，可达环境重跑；H2 vs H3 暂由 mock-vs-live 天然对照支撑 |
| E5 / 检索覆盖 | ⛔ 本沙箱阻塞（DashScope 401） | 脚本就绪；离线部分证据见 `docs/attribution-recall-collapse.md` §6 校验3 |

## 重跑前三项校验（用户指令，详见报告 §6）
1. **E2 纯度**：设计保证 `citations-nonempty ≡ 0`（tools=()→白名单空→引用全剥离）；实证阻塞于 401。
2. **E3 通路**：✅ 已实证——live 落盘 6/70 findings 为空引用 unverified，schema 允许空引用，E3 ≠ E1。
3. **检索覆盖**：部分实证——citation_audit 严格支撑率 46.7%、3 类风险召回 0%；完整 top-20 待 E5 在密钥环境跑。

## 设计要点
- E2 用子类化 `ExpertSpec`（内存覆写 `load_system_prompt`，**不写磁盘**）把工具置空 `tools=()`，仅给条款+风险清单。
- E3 保留 live 检索，但在系统提示追加「检索仅供参考，不足时仍须给出风险判断并标 unverified」。
- E2/E3/E5 均设 preflight：LLM 不可达时明确 UNREACHABLE（exit 2/3）且不写误导性 metrics。
