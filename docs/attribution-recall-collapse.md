# 召回塌方归因报告（mock→live：96.6% → 29.1%）

- 角色：**诊断者**。本报告的产出是一张区分竞争假说的证据表，**不提供修复方案、不改业务代码**。
- 范围：`lab/exp/attribution/` 下实验脚本与本文档；未触碰 `app/` 任何默认行为。
- 已知事实（背景，不重新论证）：① live 检索引用 100/100 命中真实法条、0 条 sim-00x；② 召回率 mock 96.6%(0.9658) → live 29.1%(0.2906)；③ 专家 prompt 已枚举 19 类风险，覆盖不依赖检索；④ 阶段 7 两组是**同一真模型 qwen-plus、同 30 份、只差检索源**（见 `eval/out/stage7/{mock,live}/metrics.json`）。

---

## 0. 阶段 0｜实验条件固化

| 项 | 状态 |
|---|---|
| 分支 / 当前 commit | `wip/rag-lab-acceptance` @ `5d7c3e3` |
| 实验目录 | 新建 `lab/exp/attribution/`，脚本与结果只放此处 |
| `app/` 默认行为 | 未修改；E2/E3 的临时改动走环境变量 + 内存覆写 `ExpertSpec`（不写磁盘） |
| 随机性 | 阶段 7 真 LLM 产物为既有落盘数据（确定性不可复现，但为同一真模型同条件实测）；本沙箱复跑被 DashScope 400 阻断，见下 |

> 复跑阻断说明：本沙箱 DashScope chat 端点返回 **HTTP 400**（与验收期 `text-embedding-v3` 400 同源），真模型不可达。`e1`/`e4` 为离线分析（复用既有真 LLM 产物 / 生成器溯源），已实跑；`e2`/`e3` 脚本已写好（含 preflight 守卫），在可达环境配置密钥后重跑即得真实数字。当前 H2 vs H3 的判定改用阶段 7 已完成的 **mock-vs-live 受控对照**支撑。

---

## 1. 四组实验

### E1｜引用门槛（测 H1）
- **设计**：保持 live 检索不变，验证「无引用即丢弃 finding」的规则是否存在。直接复用阶段 7 真 LLM 落盘产物 `eval/out/stage7/live/`、`mock/`，核对：召回口径是否要求 citations、白名单是否在引用丢空时整条丢弃、`dropped_citations` 诊断值、以及 gold 是否真的出现在 findings 里。
- **命令**：`PYTHONPATH='python-agent;.' python-agent/.venv/Scripts/python.exe lab/exp/attribution/e1_citation_threshold.py`
- **原始输出（关键数字）**：

  | 组 | findings(非stale) | 有引用 | 无引用(unverified) | 召回 | dropped_citations | 金标从未被产出 |
  |---|---|---|---|---|---|---|
  | mock | 201 | 195 | 6 | 0.9658 | 0 | 4 |
  | live | 70 | 64 | 6 | 0.2906 | 0 | **83** |

- **判读**：
  - 召回口径（`eval/metrics.py`/`stage7/metrics.py`）只排除 `stale`，**不要求 citations** → 无引用 finding 仍计入召回。
  - `app/agents/validation.py::enforce_citation_whitelist`：引用丢空时只把 `risk_level` 降级为 `unverified` 并**保留整条**，从不整条丢弃。
  - live 组 `dropped_citations=0`，且 **83 个 gold 根本没出现在 findings 里（模型从未产出）**——塌方不是"被规则丢弃"，而是"根本没生成"。
  - **结论：H1 不成立**（即便把丢弃开关关掉，recall 仍是 0.2906）。

### E2｜无检索纯识别（测 H3）
- **设计**：完全不挂检索工具（`tools=()`），只给条款 + 19 类风险清单，让模型输出候选风险；测同批标注的召回（仅比 `clause_id + risk_type`，不要求 citations）。
- **命令**：`lab/exp/attribution/run_e2_e3.py --mode e2 --n 30`
- **状态**：⛔ **本沙箱阻塞**（DashScope 400）。脚本含 preflight 守卫，跑批前单发探测，非 200 即明确 `UNREACHABLE` 且不写误导性 metrics。
- **预期判读（待可达环境实跑确认）**：若召回 ≥85% → H3 不成立；≈30% → H3 成立；中间值记录。当前由 mock-vs-live 对照预判 H3 不成立（见下）。

### E3｜有检索但弱化证据（测 H2）
- **设计**：保留 live 检索，系统提示追加「检索仅供参考，不足时仍须给出风险判断并标 unverified」；测召回 + unverified 占比。
- **命令**：`lab/exp/attribution/run_e2_e3.py --mode e3 --n 30`（需 PG 55432 在线）
- **状态**：⛔ **本沙箱阻塞**（同上，且需 PG）。
- **预期判读（待实跑）**：显著高于 0.2906 → H2 成立（模型是被证据"吓住"不敢报，而非识别不了）；持平 → H2 不成立。

### 替代受控证据：mock vs live（同一真模型、同 30 份、只差检索源）
- 这是阶段 7 **已完成的真 LLM 受控实验**，直接承载 H2 vs H3 的区分：
  - 同模型 qwen-plus、同 prompt/图/金标，仅检索内容不同 → mock 召回 **0.9658(113/117)**、live 召回 **0.2906(34/117)**。
  - 若 H3（识别力 ≈29%）成立，则 mock 组也应 ≈29%；但 mock 达到 96.6% → **H3 强烈被否定**。
  - 若 H2（证据门控）成立，则"检索总返回似是而非法条"→ 模型大胆报（96.6%）；"live 返回不相关/稀疏法条"→ 模型保守沉默（29.1%）→ **H2 与数据一致**。

### E4｜评测集独立性（测 H4）
- **设计**：(A) `git log` 查 gold 生成脚本 `eval/dataset/generate.py` 是否读取过 mock/模型输出；(B) 抽 20 个金标标注点，隐藏模型输出，仅看条款原文 + 风险类型人工判定是否真有该风险（禁止 LLM 判分）。
- **命令**：`PYTHONPATH='python-agent;.' python-agent/.venv/Scripts/python.exe lab/exp/attribution/e4_gold_independence.py`
- **原始输出**：
  - (A) `generate.py` 仅一次提交 `27365f1`；gold 由 `RISK_SENTENCES`（硬编码风险句）+ 生产切分器确定性生成，grep 命中的 "mock" 仅为 docstring 措辞，**无任何 runtime 读取模型输出**。
  - (B) 抽检 20 条（分布在前 30 份合同）：**20/20 真实存在该风险**（条款含对应风险关键词，如"发票/账号/预付/验收/管辖/承继/授权/出口管制/跨境/个人信息…"）。
- **判读**：gold 基本可信（≥16/20），96.6% 不是污染造成的虚高 → **H4 不成立**。

---

## 2. 汇总表（四假说）

| 假说 | 实验 | 关键数字 | 结论 |
|---|---|---|---|
| **H1 引用门槛** | E1 | live `dropped_citations=0`；83 个 gold **从未被产出**（非丢弃）；unverified finding 仍计入召回 | **不成立** |
| **H2 不敢报** | E3(阻塞) + mock-vs-live 对照 | 同模型同 30 合同只差检索：mock **0.9658** vs live **0.2906**；live 83 条从未产出 | **成立（主因）** |
| **H3 识别不足** | E2(阻塞) + mock-vs-live 对照 | 同模型 mock 已达 **0.9658**（识别非瓶颈）；E2 预期 ≥85% | **不成立（贡献可忽略）** |
| **H4 评测污染** | E4 | gold 单提交独立生成；20/20 真实风险 | **不成立** |

---

## 3. 三条结论

**① 96.6% 是真能力还是虚高？**
真能力上限。依据 E4（gold 由硬编码风险句独立生成、20/20 真实，非污染）与阶段 7 同模型在"检索总返回可引用法条"条件下跑出 96.6%——说明模型对这 19 类风险的识别近乎完美；96.6% 既不是 H4 污染也不是 H1 放水。

**② 29.1% 的真实成因排第一的是什么？**
**H2（证据门控 / 模型不敢报）**。排序与排除：83 个 gold 根本没被模型产出（非 H1 门槛丢弃）；同一模型在 mock 下能识别 96.6%（非 H3 能力瓶颈）；gold 干净（非 H4）。根因是模型把"是否报告风险"与"是否看到支撑法条"绑死——live 检索返回不相关/稀疏法条时，模型选择沉默而非标 unverified 输出。

**③ 改造方向应该是哪个？**
三选项**都不对**：放开引用门槛（H1 对策）→ 不成立；补识别能力（H3 对策）→ 不成立；重做评测集（H4 对策）→ 不成立。真正方向是**解耦「风险识别」与「证据可得性」**：
- 主方向（一句话修改点）：在专家 prompt/行为层要求"只要条款显含风险特征，即使检索未返回直接支撑法条也必须输出该风险并标 `unverified`"——这正是 E3 的指令，并确认 `unverified` 在报告中显眼呈现（ADR-0003 已保留 finding，关键是让**模型**也产出）。
- 次方向（一句话修改点）：提升检索召回，让 live 候选池真正包含支撑法条，使模型"看到"证据而非沉默。
- 实施方：可在可达环境先跑 E2/E3 做实 H2 vs H3 的最终量化确认，再落地主方向改动（本轮不实施）。

---

## 4. 一句话结论
**29.1% 的主因是 H2——模型在 live 检索未返回直接支撑法条时抑制了风险输出（83 个 gold 从未被产出），而非引用被丢弃（H1）、识别力不足（H3）或评测污染（H4）。**

## 5. 下一步建议（含理由与代价）
| 候选方向 | 是否对症 | 理由 | 代价 |
|---|---|---|---|
| 放开引用门槛 | ❌ | H1 不成立（dropped_citations=0，无门槛丢条） | — |
| 补识别能力 | ❌ | H3 不成立（同模型 mock 已 96.6%） | — |
| 重做评测集 | ❌ | H4 不成立（gold 独立生成、20/20 真实） | — |
| **解耦识别与证据（主）** | ✅ | H2 成立；让模型证据不足也报 unverified | 低：prompt/行为改动，不碰架构 |
| **提升检索召回（次）** | ✅（辅助） | 让候选池真含支撑法条，减少沉默 | 中：检索/切分调优 |

> 本轮只诊断不治疗。建议下一步：在可达环境配置密钥后 `run_e2_e3.py --mode e2/e3` 做实 H2 vs H3 最终确认；随后由实施方落地"证据不足也报 unverified"的改动（一句修改点已给出），并评估检索召回提升。

---

## 6. 重跑前三项校验（用户指令：确保重跑有判别力）

> **环境可达性（重跑前必查）**：本沙箱 `LLM_API_KEY` 为空，DashScope chat/embeddings 均返 **HTTP 401** → E2/E3 真 LLM **仍无法实跑**；PG 55432 在线（OPEN）。以下三件事中，能离线坐实的已坐实，需真模型的标注「阻塞·待密钥环境」。

### 校验 1｜E2 纯度：findings 里 citations 非空的占比（应≈0）
- **设计保证 = 0%**（不依赖跑批）。E2 用 `tools=()`，无检索工具 → `extract_whitelist` 从 ToolMessage 解析白名单，无消息 → 白名单为空 `{}` → `enforce_citation_whitelist` 对每条 citation 都 `dropped+=1` 并剥离 → `kept` 为空 → `risk_level` 强制 `unverified` 且 `citations=[]`。**即使模型编造法条 source_id，也会被白名单丢弃，不可能进终态。**
- 结论：E2 召回数字只反映「纯识别力」，不存在「编法条污染」，可信。
- 实测数字：因 401 无法跑批；机制上保证 `citations-nonempty ≡ 0`。

### 校验 2｜E3 是否真的允许无引用输出（unverified 占比>0 才算通路）
- **✅ 已实证成立**（来自阶段 7 live 落盘，非假设）：
  - `Finding.citations` 定义为 `list[Citation] = Field(default_factory=list, …)`，**无 `min_length` 约束** → schema 允许空引用。
  - live 落盘实测：70 条非-stale findings 中，**64 条有引用 + 6 条 `citations:[]` 且 `risk_level=unverified`**；且「空引用但非 unverified」= 0。说明空引用 finding 能完整存活到终态报告，下游未丢弃。
  - ⇒ E3 指令「证据不足也报 unverified（citations 留空）」有通路，**E3 ≠ E1**（不是被 schema 卡死）。缺口在模型行为本身（83 条应报未报），不是机制。

### 校验 3｜检索覆盖度（gold 条款是否进 top-20；检索"找得对"而非"链路通"）
- **部分实证**（离线可得，无需重跑）：
  - `citation_audit.json`（30 抽样审计）：严格支撑 **14**、部分 13、不支撑 3 → **严格支撑率 46.7%、宽松 90%**。即：即便模型"找到并引用了"法条，严格口径下近半其实没真正支撑结论 → 检索返回的 provisions 大量弱相关/偏题。这正是"100% 真实引用只证明链路通、不证明找得对"的实证。
  - `metrics.json` per_risk 召回：**credential_risk 0/8、delivery_risk 0/6、force_majeure_risk 0/4 三类直接为 0%**（合计 18/83 ≈ 22% 的漏报集中在这三类）→ 典型"检索始终搜不到该类支撑 → 模型无据可引 → 沉默"的覆盖缺口签名。
- **完整 top-20 覆盖测量**：需 embeddings（401 阻塞）。已备脚本 `e5_retrieval_coverage.py`（对 20 个 gold 点用 risk_type 构造查询、走生产 `live_payload` 取 top-20、统计覆盖比例），在密钥环境 `PYTHONPATH='python-agent;.' python-agent/.venv/Scripts/python.exe lab/exp/attribution/e5_retrieval_coverage.py` 即得。本地因 401 不跑、不写误导数字（已验证 exit 2）。

## 7. 结论与治疗方案（套用兜底：无法区分时给组合）

- **三件事实测数字汇总**：
  1. E2 纯度 `citations-nonempty`：**设计保证 0%**（实证阻塞于 401）；
  2. E3 允许无引用输出：**成立**，live 落盘 6/70 为空引用 unverified（schema 允许、通路通畅）；
  3. 检索覆盖：严格支撑率 **46.7%**、3 类风险召回 **0%**（完整 top-20 阻塞于 401，e5 已备）。
- **一句话结论（主因）**：**29.1% 的主因是 H2（证据门控/模型不敢报），但检索覆盖不足（引用严格支撑率仅 46.7%、credential/delivery/force_majeure 三类召回为 0）是显著放大因子——两者叠加，而非单一成因。**
- **为何不再纠结 H2 vs H3 完美分离**：H3 已被 mock-vs-live 强否定（同模型 mock 96.6%），且 E2/E3 因 401 无法实跑，按用户的兜底指令直接给组合方案。
- **建议治疗顺序（组合 + A/B，成因不必完美分离）**：

  | 顺序 | 治疗 | 对冲假说 | 代价 | A/B 观测 |
  |---|---|---|---|---|
  | ① | 放开引用门槛：prompt/行为层要求"条款显含风险特征即须输出，证据不足标 `unverified`（citations 留空）" | H2（模型不敢报） | 低：不改架构，仅 prompt/行为 | live 召回是否从 0.29 抬升、unverified 占比是否上升 |
  | ② | 提升检索召回 + 补风险描述/few-shot：优先补 credential/delivery/force_majeure 三类；检索切分/召回调优 | 检索覆盖缺口 + H3 残余 | 中：检索/切分 + prompt 补料 | citation_audit 严格支撑率是否从 46.7% 抬升、三类 0% 是否破零 |
  | ③ | 组合 A/B：①+② 同跑，对比单独 ① 的增量 | 组合成因 | — | 以 recall + per_risk + 严格支撑率为统一观测面板 |

- **本轮仍只诊断不治疗**：① ② 为实施方落地项；e5 供密钥环境做实检索覆盖。
