# 检索实验台 · 阶段 3 验收报告（构建标注查询集）

> 日期：2026-11-07
> 范围：新增 `lab/` 标注集与裁判逻辑；**未改动** LangGraph 拓扑、前端、
> `contracts/openapi.yaml`、`events.schema.json`、检索实现、Java 侧代码。
> 未新增任何依赖。所有输出均为本机真实运行结果，命令可复现。

---

## 0. 口径说明（两处必须先讲清）

**1）chunk_id 与任务书示例不一致，按真实规则写。**
任务书示例 gold 是 `民法典:第585条:0:S1_whole`，但阶段 1 的 `Chunk.chunk_id`
规则是 `{law_code}:{article}:{idx}:{strategy}`，`law_code` 取自
`retrieval/chunker.py` 的 `LAW_CODES`（`civil_code`…），`article` 是中文条号
（`第五百八十五条`）。本数据集一律写真实 id，例如
`civil_code:第五百八十五条:0:S1_whole`，映射直接复用 `LAW_CODES` 不另写一份。
若照抄示例，「gold 的 article 能在语料里找到」会 100% 失败，也无法与
`law_chunk` 表主键对齐。

**2）辅助 gold 收紧过一次。**
初版把「相邻条文 / 上位条文 / 处罚后果」也挂进 gold（例如委托开发的问题挂了
「合作开发」条、广告未送审挂了「真实性」条）。抽样复核时发现这些属于
「相关但答非所问」，会让 gold 正确率掺水、让 recall@k 把噪声算成正确。
收紧规则为：**只有 query 明确问到第二个法律争点、缺了答案就不完整时才挂**，
并同步改写 3 条场景问法让多 gold 有依据。最终 72 条单 gold、6 条 2 gold、2 条 3 gold。

---

## 1. 变更摘要（文件 + 一句话）

| 文件 | 变更 |
|---|---|
| `lab/dataset/queries.jsonl` | 80 条金标查询，`q001`–`q080`，6 场景 × 3 问法 |
| `lab/dataset/build_dataset.py` | 人工标注表（32 组主法条）+ 确定性展开 + 配额硬校验；不生成任何语义 |
| `lab/dataset/README.md` | 标注方法、chunk_id 口径偏差、场景定义、分布、命中判定、复现命令 |
| `lab/dataset/validate.py` | 可执行验收：12 项硬断言 + 分布表 + 5 策略覆盖 + 抽样复核材料 |
| `lab/dataset/review_sample.md` | seed 固定抽 10 条的人工复核记录，逐条附原文依据，结论 10/10 正确 |
| `lab/metrics.py` | 命中判定（唯一权威口径）+ `hit@k` / `recall@k` / `MRR` / 分组 `evaluate` |
| `lab/tests/test_metrics.py` | 16 项：跨策略命中、k 截断、去重、两种口径、分组指标 |
| `lab/tests/test_dataset.py` | 4 项：80 条/qid 连号/字段/分布/gold 格式 |
| `docs/retrieval-stage3-acceptance.md` | 本报告 |

---

## 2. 验收表（项 / 命令 / 实际输出 / 结论）

| 项 | 命令 | 实际输出 | 结论 |
|---|---|---|---|
| 数据集生成 | `python lab/dataset/build_dataset.py` | `已生成 … queries.jsonl 共 80 条` | PASS |
| 80 条 + 6 场景分布表 | `python lab/dataset/validate.py` | 6 场景 13/14/14/13/13/13，全部 ≥10 | PASS |
| 问法类型 40/30/30 | 同上 | `concept=32 (40%) scene=24 (30%) colloquial=24 (30%)` | PASS |
| gold 1–3 个 | 同上 | `{1: 72, 2: 6, 3: 2}`，组内无重复 | PASS |
| 每条 gold 的 article 在语料里 | 同上 | 校验 90 个 gold，失败 0 个 | PASS |
| 5 种切分策略都覆盖 | 同上 | 37 个不同 (law, article) × 5 = 185 项，缺失 0 项 | PASS |
| 命中判定口径自检 | 同上 / `pytest lab/tests` | 不同策略同 article 算命中、不同 article 不算命中 | PASS |
| 抽样 10 条人工复核 | `review_sample.md` | 10/10 正确，gold 正确率 100% | PASS |
| 单测 | `pytest ../lab/tests -q` | `21 passed in 0.09s` | PASS |
| 静态检查 | `ruff check ../lab` | `All checks passed!` | PASS |
| 类型检查 | `mypy ../lab/metrics.py` | `Success: no issues found in 1 source file` | PASS |
| 文本稳定性 | 重跑 build 前后 `sha256sum` | `QUERIES_UNCHANGED=YES`（`538bf786…dca541d9b50addf`） | PASS |

`validate.py` 最终退出码 `VALIDATE_EXIT=0`，末行 `===== 验收结论：全部 PASS =====`。

---

## 3. 真实输出片段

### 3.1 场景 × 问法类型分布

```
scene       concept  scene  colloquial  合计
----------  -------  -----  ----------  --
subject     5        4      4           13
commercial  6        4      4           14
liability   6        4      4           14
ip          5        4      4           13
data        5        4      4           13
compliance  5        4      4           13
合计          32       24     24          80
```

### 3.2 5 种切分策略覆盖

```
strategy      全库 chunk 数  覆盖 gold 条数
------------  ----------  ----------
S1_whole      2584        37
S2_fixed512   2593        37
S3_fixed256   2687        37
S4_paragraph  2743        37
S5_context    2584        37
```

`gold 对应条在 5 种切分下均可覆盖 = PASS（185 项，缺失 0 项）`。
这一项直接落实任务书「标注时对 5 种切分都确认能覆盖到对应条」：
标注锁的是**法条**，不是切片，允许 chunk 不同但 article 相同。

### 3.3 12 项硬断言

```
总条数 = 80                      PASS  实际 80
qid 唯一且格式 qNNN                PASS  q001 .. q080
query 非空且互不重复               PASS  唯一查询 80 条
scene / type 取值合法             PASS  6 场景 / 3 类型
每条 gold 1–3 个且组内不重复         PASS  分布 {1: 72, 2: 6, 3: 2}
6 场景各 ≥ 10 条                  PASS  subject=13, commercial=14, liability=14, ip=13, data=13, compliance=13
问法类型 40% / 30% / 30%          PASS  {'concept': 32, 'scene': 24, 'colloquial': 24}
每条 gold 的 article 都能在语料找到   PASS  校验 90 个 gold，失败 0 个
gold 对应条在 5 种切分下均可覆盖      PASS  37 × 5 = 185 项，缺失 0 项
不同策略、同 article 算命中（核心口径）  PASS  S3_fixed256:2 vs gold S1_whole:0 -> hit
不同 article 不算命中             PASS  第577条 vs gold 第585条 -> miss
evaluate 宏平均可算且不崩溃          PASS  hit_rate=0.5 mrr=0.5

共 12 项检查，全部 PASS；数据集 80 条，不同 gold 法条 37 条
```

### 3.4 数据样例

```json
{"qid": "q001", "query": "完全民事行为能力人的认定标准是什么", "gold": ["civil_code:第十八条:0:S1_whole"], "scene": "subject", "type": "concept"}
{"qid": "q040", "query": "同时约定违约金和定金时守约方应当如何主张", "gold": ["civil_code:第五百八十八条:0:S1_whole", "civil_code:第五百八十五条:0:S1_whole", "civil_code:第五百八十七条:0:S1_whole"], "scene": "liability", "type": "concept"}
{"qid": "q080", "query": "经营者不得实施哪些虚假或者引人误解的商业宣传行为", "gold": ["anti_unfair_competition_law:第八条:0:S1_whole"], "scene": "compliance", "type": "concept"}
```

---

## 4. 命中判定（写进 `lab/metrics.py` 模块注释）

```
命中 = 检索结果中任一条的 article 与 gold 的 article 相同
```

**为什么用 article 而非 chunk_id**：同一法条在 `S1_whole` 是 1 个 chunk，在
`S3_fixed256` 可能切成 3 个，`S5_context` 还会注入法条名前缀。按 chunk_id 比对，
切换策略时结果不可比，「策略 A 优于 B」会变成切分下标的假象。article 在 5 种策略下
恒定，是唯一可跨策略比较的粒度。

`metrics.py` 提供 `is_hit` / `recall_at_k` / `reciprocal_rank` / `evaluate`；
`evaluate` 按 `scene` 与 `type` 分组，避免「总体指标还行但口语问法全灭」被掩盖。
`match_key()` 同时接受阶段 2 的 `Hit`、端点 `dict`、字符串 `chunk_id`、裸 `article`，
避免调用方各处手工转换导致一处转错、全表指标虚高。

---

## 5. 人工复核（抽样 10 条）

`validate.py` 用 `seed=20261107` 固定抽 10 条，把「问法 + gold + 法条原文首句」
写进 `review_sample.md`。**脚本不会替人下「正确」结论**，复核栏留空由人对照
语料**完整正文**填写；逐条依据已写进该文件。

| qid | 场景/问法 | gold 数 | 结论 | 依据（摘要） |
|---|---|---|---|---|
| q073 | compliance/colloquial | 1 | ✔ | 广告法 28 条一款定义 + 二款列举虚假广告情形 |
| q049 | ip/scene | 1 | ✔ | 民法典 859 条同时答「归研究开发人」与「委托人可实施」 |
| q057 | data/colloquial | 1 | ✔ | 民法典 1034 条二款给个人信息定义与列举 |
| q005 | subject/scene | 1 | ✔ | 民法典 57 条「法人独立享有民事权利」 |
| q020 | commercial/concept | 1 | ✔ | 民法典 497 条正列举格式条款无效三情形 |
| q040 | liability/concept | 3 | ✔ | 588 条择一 + 585 违约金 + 587 定金罚则，完整一问多答 |
| q032 | liability/scene | 1 | ✔ | 民法典 584 条含可得利益与可预见限制 |
| q028 | liability/concept | 1 | ✔ | 民法典 577 条逐字列举责任形式 |
| q010 | subject/concept | 1 | ✔ | 公司法 23 条主体/行为/连带责任逐字对应 |
| q075 | compliance/scene | 1 | ✔ | 广告法 46 条明列医疗器械、保健食品须发布前审查 |

**合计 10 条，正确 10 条，gold 正确率 100%。**

---

## 6. 测试与静态检查（原始输出）

```
$ uv run --project python-agent python lab/dataset/build_dataset.py
已生成 …/lab/dataset/queries.jsonl 共 80 条

$ uv run --project python-agent python lab/dataset/validate.py
VALIDATE_EXIT=0            # 12 项硬断言全部 PASS

$ uv run pytest ../lab/tests -q
.....................                                                    [100%]
21 passed in 0.09s

$ uv run ruff check ../lab
All checks passed!

$ MYPYPATH=../lab uv run mypy ../lab/metrics.py --explicit-package-bases
Success: no issues found in 1 source file

$ sha256sum queries.jsonl  # 重跑 build 前后
QUERIES_UNCHANGED=YES  (538bf7866028a99213e59c0d9044e496fe085f3cd9ff9e175dca541d9b50addf)
```

为修 ruff 的 E501，把 4 条长场景问法改成括号内字符串拼接；重跑前后
`queries.jsonl` 的 sha256 完全一致，证明排版调整没有改动任何一个字。

---

## 7. 遗留风险

1. **跨法同条号误命中（最需要关注）**：验收输出量化了
   `gold 中「条号被多部法律共用」的条目 = 16 / 37`。只比 article 时，召回
   「别部法律的同一条号」也会算命中（如 `civil_code:第四条` 命中
   `advertising_law:第四条`）。`metrics.is_hit(..., mode="law_article")`
   提供带 law_code 的严格口径，`test_metrics.py` 专门把这个已知缺陷固定成单测。
   默认仍按任务书用 `article` 保证实验口径一致；**建议阶段 4 同时报两种口径**，
   差值就是误命中带来的虚高幅度。
2. **ip / data 场景没有专门法**：9 部语料不含《著作权法》《专利法》《商标法》
   《个人信息保护法》。两类场景靠民法典技术合同、人格权编、反不正当竞争法、
   劳动合同法、广告法、民事诉讼法中真实存在的条文覆盖，问题深度受限，
   不能据此断言「系统具备完整 IP / 数据合规审查能力」。
3. **单一标注者 + 自查复核**：80 条由一人标注并复核，未做双人独立标注与
   Cohen's kappa；抽样 10 条也是自查，存在确认偏差。若用于对外结论，建议补
   第二标注人复核全量或至少 30 条。
4. **多 gold 偏少**：收紧后 72/80 是单 gold，只有 8 条 2–3 gold，
   `recall@k` 的区分度有限，更依赖 `hit@k` 与 `MRR`。
5. **覆盖率低**：37 条不同 gold 法条仅占语料 2584 条的 1.4%，
   其余语料未被裁判集覆盖，无法据此评估长尾法条的检索质量。
6. **场景问法是构造的**：情境由标注者按法条反推，不是真实用户查询日志；
   口语化程度靠人工判断，未经真实语料抽样验证分布。
7. **尚未接入 eval/**：本阶段只产出裁判集与判定函数，没有跑检索后端出指标；
   阶段 4 需要把 `/v1/retrieval/search` 或评测框架接到 `metrics.evaluate()`。
8. **复核记录会被重置**：重跑 `validate.py` 会把 `review_sample.md` 覆盖回
   「待人工填写」状态，需重新复核；这是刻意为之，避免脚本给自己盖章。

---

## 8. 禁止项自查

| 约束 | 状态 |
|---|---|
| 不用 LLM 生成 gold | `build_dataset.py` 的 `ANNOTATIONS` 是人工标注表；脚本只拼 `chunk_id`、分 qid、校验配额，无任何模型调用或语义生成 |
| 不标注自己没读过的法条 | 标注前按 6 场景逐条读取约 100 条候选法条的完整正文；`validate.py` 打印每条 gold 的原文供复核；`(law, article)` 不在语料即 FAIL |
| 不改拓扑 / 前端 / openapi / events.schema | `git status` 对这些路径为空 |
| 不新增框架级依赖 | 只用标准库 + 阶段 1/2 已有模块（`retrieval.chunker` / `corpus.loader`） |
| 注释写「为什么」 | 标注表逐组说明「为什么这样问 / 为什么挂这条」，metrics 模块注释写清口径成因与缺陷 |
| 每阶段可执行验收 | `lab/dataset/validate.py`，退出码 0 |
