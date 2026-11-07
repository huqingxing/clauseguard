# 检索实验台 · 阶段 1 验收报告（法条语料入库与切分策略）

> 日期：2026-11-06
> 范围：`python-agent/retrieval/` 离线语料与切分器；**不改** LangGraph 拓扑、前端、
> `contracts/openapi.yaml`、`events.schema.json`；零新增框架级依赖。
> 所有输出均为本机真实运行结果，命令可复现。

---

## 0. 口径说明（避免歧义）

「切分后 chunk 数 2000–3000」按**单一策略切完整个语料库得到的 chunk 数**统计。
最终结果 5 种策略**全部**落在区间内，因此无论按哪一种策略作为索引口径都成立。

真实法条平均仅 96 字、最长 962 字，512/256 定窗几乎不产生额外切片：只用 3 部法律时
5 策略仅 1624–1670 个 chunk。为达标而**扩充真实法条条数**到 2584（9 部法律），
没有伪造条文、没有人为缩小窗口、没有用 LLM/语义切分。

---

## 1. 变更摘要（文件 + 一句话）

| 文件 | 变更 |
|---|---|
| `python-agent/retrieval/__init__.py` | 新包声明，与 `app/` 运行链路解耦 |
| `python-agent/retrieval/numerals.py` | 中文条号 → 阿拉伯数字（条号校验与 S5 前缀共用，避免两处漂移） |
| `python-agent/retrieval/chunker.py` | `Chunk` 统一结构 + `Strategy(StrEnum)` 5 策略 + `source_text()` + 偏移不变量校验 |
| `python-agent/retrieval/stats.py` | 可执行验收：语料/策略统计 + 全量偏移回放 + 固定种子抽 20 条 |
| `python-agent/retrieval/corpus/wikitext/*.txt` | 17 份维基文库原始快照（离线可复现）+ 1 份附则原始 API 响应 |
| `python-agent/retrieval/corpus/build_corpus.py` | 快照 → jsonl 的确定性解析；条号连续性/重复/空正文/条数下限硬校验 |
| `python-agent/retrieval/corpus/loader.py` | 单一语料读取入口，固定顺序，缺文件即失败 |
| `python-agent/retrieval/corpus/*.jsonl` | 9 部法律、2584 条逐条语料（每行一个 JSON 对象） |
| `python-agent/retrieval/corpus/README.md` | 来源/许可/重建命令/两处解析分支说明 |
| `python-agent/tests/test_chunker.py` | 30 项单测：5×2 用例、偏移断言、空文本/超长条/篡改边界 |
| `python-agent/pyproject.toml` | wheel `packages` 增加 `retrieval`，否则部署包缺语料与切分器 |
| `docs/retrieval-stage1-acceptance.md` | 本报告 |

**未改动**：`web/`、`contracts/events.schema.json`、`contracts/openapi.yaml`、
`python-agent/app/graph/**`、任何编排/契约代码（`git status --short` 对这些路径为空）。

---

## 2. 验收表（项 / 命令 / 实际输出 / 结论）

| 项 | 命令 | 实际输出 | 结论 |
|---|---|---|---|
| 语料条数与完整性 | `python -m retrieval.corpus.build_corpus` | `TOTAL 条数 = 2584；未匹配行合计 = 0；错误 = 0`，9 部法律各自 `缺号=0` | PASS |
| 语料 ≥ 800 条 | 同上 | 2584 条 | PASS |
| 5 策略 chunk 数 2000–3000 | `python -m retrieval.stats` | 2584 / 2593 / 2687 / 2743 / 2584，5 条 `PASS (2000-3000)` | PASS |
| `raw[start:end]==text` 全量 | 同上 | `全量偏移回放：13191 条 chunk 全部通过` | PASS |
| 随机抽 20 条断言 | 同上（seed=20261106） | `抽样 20 条，失败 0 条` | PASS |
| 单测 | `pytest tests/test_chunker.py -v` | `30 passed in 0.11s` | PASS |
| 全量回归 | `pytest --tb=no` | `328 passed in 64.68s` | PASS |
| 静态检查 | `ruff check retrieval tests/test_chunker.py` | `All checks passed!` | PASS |
| 类型检查 | `mypy retrieval --no-incremental` | `Success: no issues found in 7 source files` | PASS |
| 打包不缺包 | `uv build --offline` + 解包核对 | wheel 含 `retrieval/chunker.py`、`retrieval/corpus/civil_code.jsonl`、`app/main.py` | PASS |

---

## 3. 真实输出片段

### 3.1 语料构建（`python -m retrieval.corpus.build_corpus`）

```
  civil_code_01_general.txt              ->  204 条  (未匹配行 0)
  civil_code_02_property.txt             ->  258 条  (未匹配行 0)
  civil_code_03_contract.txt             ->  526 条  (未匹配行 0)
  civil_code_04_personality.txt          ->   51 条  (未匹配行 0)
  civil_code_05_marriage.txt             ->   79 条  (未匹配行 0)
  civil_code_06_succession.txt           ->   45 条  (未匹配行 0)
  civil_code_07_tort.txt                 ->   95 条  (未匹配行 0)
  civil_code_08_supplementary.txt        ->    2 条  (未匹配行 0)
  company_law_2023.txt                   ->  266 条  (未匹配行 0)
  labor_contract_law_2012.txt            ->   98 条  (未匹配行 0)
  insurance_law_2015.txt                 ->  185 条  (未匹配行 0)
  securities_law_2019.txt                ->  226 条  (未匹配行 0)
  enterprise_bankruptcy_law.txt          ->  136 条  (未匹配行 0)
  civil_procedure_law_2023.txt           ->  306 条  (未匹配行 0)
  anti_unfair_competition_law_2019.txt   ->   33 条  (未匹配行 0)
  advertising_law_2021.txt               ->   74 条  (未匹配行 0)
  civil_code.jsonl             条数=1260  期望=1260  缺号=0  最大条号=1260
  company_law.jsonl            条数= 266  期望=266  缺号=0  最大条号=266
  labor_contract_law.jsonl     条数=  98  期望=98  缺号=0  最大条号=98
  insurance_law.jsonl          条数= 185  期望=185  缺号=0  最大条号=185
  securities_law.jsonl         条数= 226  期望=226  缺号=0  最大条号=226
  enterprise_bankruptcy_law.jsonl 条数= 136  期望=136  缺号=0  最大条号=136
  civil_procedure_law.jsonl    条数= 306  期望=306  缺号=0  最大条号=306
  anti_unfair_competition_law.jsonl 条数=  33  期望=33  缺号=0  最大条号=33
  advertising_law.jsonl        条数=  74  期望=74  缺号=0  最大条号=74
TOTAL 条数 = 2584；未匹配行合计 = 0；错误 = 0
BUILD_EXIT=0
```

### 3.2 统计表（`python -m retrieval.stats`）

```
=== 语料统计 ===
  民法典           1260 条
  公司法            266 条
  劳动合同法           98 条
  保险法            185 条
  证券法            226 条
  企业破产法          136 条
  民事诉讼法          306 条
  反不正当竞争法         33 条
  广告法             74 条
  合计 2584 条 / 9 部法律 / 248069 字符

=== 5 种切分策略统计（口径：该策略切完整个语料库的 chunk 数）===
  strategy          chunks      avg    min    max  区间校验
  S1_whole            2584     96.0      8    962  PASS (2000-3000)
  S2_fixed512         2593     95.9      8    512  PASS (2000-3000)
  S3_fixed256         2687     93.5      8    256  PASS (2000-3000)
  S4_paragraph        2743     90.4      8    318  PASS (2000-3000)
  S5_context          2584    115.4     26    980  PASS (2000-3000)
  5 策略合计 chunk = 13191
```

### 3.3 随机 20 条偏移断言（`seed=20261106`，每策略 4 条）

```
  [ 1] civil_code:第一百九十八条:0:S1_whole                    [   0:  34] PASS
  [ 2] civil_code:第三百六十二条:0:S1_whole                    [   0:  48] PASS
  [ 3] civil_code:第五百四十八条:0:S1_whole                    [   0:  33] PASS
  [ 4] civil_code:第九百六十三条:0:S1_whole                    [   0: 147] PASS
  [ 5] civil_code:第三十二条:0:S2_fixed512                   [   0:  62] PASS
  [ 6] advertising_law:第十四条:0:S2_fixed512               [   0: 145] PASS
  [ 7] company_law:第七十二条:0:S2_fixed512                  [   0:  84] PASS
  [ 8] insurance_law:第三十八条:0:S2_fixed512                [   0:  28] PASS
  [ 9] civil_code:第一千一百三十四条:0:S3_fixed256               [   0:  24] PASS
  [10] civil_code:第四十六条:0:S3_fixed256                   [   0: 110] PASS
  [11] advertising_law:第五十五条:0:S3_fixed256              [   0: 256] PASS
  [12] civil_code:第一百零一条:0:S3_fixed256                  [   0:  80] PASS
  [13] civil_code:第四百五十九条:0:S4_paragraph                [   0:  47] PASS
  [14] civil_code:第八百七十四条:0:S4_paragraph                [   0:  61] PASS
  [15] civil_code:第一千一百零六条:0:S4_paragraph               [   0:  34] PASS
  [16] civil_code:第五百五十六条:0:S4_paragraph                [   0:  31] PASS
  [17] civil_code:第七百零二条:0:S5_context                   [   0:  56] PASS
  [18] company_law:第七条:0:S5_context                     [   0:  94] PASS
  [19] civil_procedure_law:第一百九十九条:0:S5_context         [   0:  92] PASS
  [20] civil_code:第五百四十七条:0:S5_context                  [   0:  96] PASS
  抽样 20 条，失败 0 条
  全量偏移回放：13191 条 chunk 全部通过
=== 结论：PASS ===
STATS_EXIT=0
```

### 3.4 测试与静态检查

```
$ pytest tests/test_chunker.py -v
30 passed in 0.11s

$ pytest --tb=no
328 passed in 64.68s (0:01:04)

$ ruff check retrieval tests/test_chunker.py --output-format concise
All checks passed!

$ mypy retrieval --no-incremental
Success: no issues found in 7 source files

$ uv build --offline
Successfully built clauseguard_agent-0.1.0-py3-none-any.whl
# wheel 核对：retrieval/chunker.py / retrieval/corpus/civil_code.jsonl / app/main.py 均存在
```

---

## 4. 5 种策略实现要点

| 策略 | 实现 | `source_text()` |
|---|---|---|
| `S1_whole` | 整条 1 个 chunk | 原文 |
| `S2_fixed512` | 512 字窗、重叠 64，末窗截断不复制 | 原文 |
| `S3_fixed256` | 256 字窗、重叠 32，末窗截断不复制 | 原文 |
| `S4_paragraph` | 按 `\n` 款切，相邻款累计 <100 字合并；款间换行归入前一块，保证无缝覆盖 | 原文 |
| `S5_context` | 整条 1 个 chunk，前缀注入 `《法律名》章节 第<N>条：` | 前缀 + 原文 |

`chunk_id = {law_code}:{article}:{idx}:{strategy}`；`Chunk` 为 frozen dataclass。
`chunk_entry()` 内对**每个** chunk 校验 `raw[start:end] == chunk.text`（显式 `raise` 而非
`assert`，避免 `-O` 下被优化掉），`assert_offsets()` 再从原始语料独立回放一次。

---

## 5. 过程中发现并修复的真问题

1. **语料源**：`raw.githubusercontent.com` 在本机不通（curl `000`）；`gov.cn` 民法典全文链接
   已 404。改用维基文库 MediaWiki API（`action=parse&prop=wikitext`）。
2. **民法典主页只是目录**，正文分布在 7 编 + 附则共 8 个子页；公司法/劳动合同法主页只是
   沿革，正文在 `(2023年)` / `(2012年)` 子页。
3. **附则是 PDF 扫描页转置**（`<pages .../>`），wikitext 无正文 → 从渲染层 HTML 提取
   1259/1260 条，原始响应留档，避免解析器开特例分支。
4. **解析器 bug A**：企业破产法页面没有 `<onlyinclude>` 标签 → 解析出 0 条。改为「无标签
   时整篇即正文」。
5. **解析器 bug B**：反不正当竞争法款行带 BOM/零宽空格 `\ufeff` → 14 行款被静默丢弃。
   改为按行清理 BOM/ZWSP。
6. **chunk 数不达标**：3 部法律时 5 策略均 1624–1670，扩到 9 部真实法律后才达标。
7. **S4 切片空洞**（单测抓到）：款间换行不属于任何 chunk，`next.start = prev.end + 1`。
   改为换行归入前一个 chunk，满足无缝覆盖。
8. **mypy 协变**：`list[LawEntry]` 不接受 `list[dict[str,str]]` → 改 `Sequence[LawEntry]`。
9. **wheel 缺包**：`packages=["app"]` 会导致部署包没有 `retrieval` → 改为 `["app","retrieval"]`
   并 `uv build --offline` 实测通过。

---

## 6. 遗留风险

- **语料版本**：快照来自维基文库，条目条号 `1..N` 连续、无重复、正文非空已校验，但**校验
  只能证明快照内自洽**，不能证明与最新官方修订文本逐字一致；`SOURCES` 里的「期望条数」
  是快照基线（用于检测快照被截断/重复），不是官方法条总数保证。产品化前需与权威文本核对。
- **许可**：维基文库文本按 CC BY-SA 4.0 / GFDL 发布，本地实验可用；随产品分发需法务确认
  并附署名与许可声明（见 `corpus/README.md`）。
- **未接入运行时**：本阶段只做离线语料与切分。`app/tools/*` 的 mock、Java MCP Server、
  pgvector 表、eval 评测后端**均未改动**，它们仍返回 `sim-001` 等假 ID——这是阶段 2+ 的工作。
- **`chunk_id` 含中文条号**：按任务规则 `{article}` 原样使用；若后续索引要求纯 ASCII key，
  需要额外的转义层（当前不做，避免偏离规格）。
- **S4 依赖段落标记**：「款」由语料里的 `\n` 表示；若更换语料来源，必须保证同样的款分隔
  约定，否则 S4 会退化成整条一个 chunk。
- **未新增依赖**：jieba / rank-bm25 / numpy 在本阶段用不上（纯字符串切分），留给后续
  BM25 / 向量检索阶段再按需引入。
