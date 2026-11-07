# RAG 检索实验台 · 总验收报告

> 验收方立场：**怀疑，不是确认**。每条结论均有「命令 + 实际输出 + 判据」。
> 验收基线：`wip/rag-lab-acceptance` @ `34a3c3e`（由 `fix@dc71a87` 切出，含 39 项改动）。
> 全程未改业务代码；只跑、只读、只记录、只写本报告与建基线 commit。

---

## 0. 总览表（项 / 命令 / 实际输出 / 结论）

| # | 项 | 命令（关键） | 实际输出（关键行） | 结论 |
| --- | --- | --- | --- | --- |
| 0 | 固化现状 | `git status --short \| wc -l` | 39 | PASS |
| 0 | 建分支并提交基线 | `git checkout -b wip/rag-lab-acceptance` + `git add -A` + `git commit` | `34a3c3e`；`git status` 干净(0) | PASS |
| 1 | 结构与文档完整性 | `ls` / `wc -l` 19 个必需文件 | 19/19 存在且非空；`queries.jsonl`=80 行；6 场景各 ≥10 | PASS |
| 2 | 可复现性 | `python -m lab.run --strategies S5_context --embeddings E1 --rerank off --label accept-a/b` | a/b 均 `recall@5=0.5896 mrr@10=0.4440 ndcg@10=0.4764`；逐位相同，仅计时字段差异 | PASS |
| 3 | 指标真实性 | `lab/out/report.md` → `lab/out/v1/metrics.json` → 复跑最优组 | 报告声称 15 组 / 第15组 `S5_context\|E3` recall@5=0.6146；v1 原始 metrics 逐位存在；复跑=0.6146（Δ=0） | PASS |
| 4A | rerank 是否完成 | `find . -iname '*rerank*'`；`run_rerank.py --help`；查 `rerank.json` | 全仓库**无 `rerank.json`**；v1 报告**无 rerank 节**；仅 `switch_check` 有 rerank=on（deterministic 通断测试，tokens=0） | **FAIL** |
| 4B | E3 是否替身 | `sed -n '45,49p;124,149p' lab/providers.py` | `E3=((2,),"cg-e3-char2-lowdim")` 字符 2-gram + `zlib.crc32` 哈希；第15组（最优）正用 E3 | 记录（事实） |
| 4C | 未提交 | 见第 0 步 | 已提交至 `34a3c3e` | PASS |
| 5 | 端到端真用 | 直连 `RetrievalService`（等价 `/v1/retrieval/search`）+ SQL 溯源 + 读 Java 降级契约 | 引用 100/100 真实法条、0 sim-00x；3/3 article 溯源命中；P50=4.76ms P95=6.36ms；降级契约由单测覆盖 | 部分验证（见 5 局限） |
| 6.1 | Python 回归 | `uv run pytest -q` | **353 passed, 0 failed** | PASS |
| 6.2 | Java 回归 | `mvn clean test`（JDK 21+） | 默认 JDK17→类版本错配红；JDK25→**103 passed, 0 fail, 2 skipped** | PASS（需 JDK21+） |
| 6.3 | Web 回归 | `npm run test:unit` | **36 passed, 0 fail** | PASS |
| 6.4 | 契约未改 | `git diff dc71a87 HEAD -- contracts/` | 差异 0 行 | PASS |
| 6.5 | 前端未改 | `git diff dc71a87 HEAD --stat -- web/` | 差异 0 行 | PASS |
| 7 | 简历可写性 | 见第 7 节 | 见「简历最多一句话」 | — |
| 8 | 收口 | 本报告 + 清理副产物 + `docs` 提交 | 见第 8 节 | — |

---

## 1. 第 0 步：固化现状（PASS）

- `git status --short | wc -l` → **39**（16 modified + 23 untracked）。
- 从 `fix@dc71a87` 切出 `wip/rag-lab-acceptance`，`git add -A`（`.gitignore` 已忽略 `.env`/`_pg_tmp/`/缓存/`eval/out/*`，无污染）+ 提交 → **`34a3c3e`**。
- 提交后 `git status` 干净（0）。验收期间可回滚。
- 未推送、未 amend `dc71a87`、未 force push。

## 2. 第 1 步：结构与文档完整性（PASS）

19 个必需文件全部存在且非空（行数 / 最后修改）：

- `python-agent/retrieval/{chunker,bm25,embedder,fusion,service,store,config}.py`、`python-agent/config/retrieval.yaml`(59)、`python-agent/retrieval/schema.sql`(22)
- `lab/{run,metrics,report,verify}.py`
- `lab/dataset/queries.jsonl`(80 行)
- `docs/rag-lab.md` + `docs/retrieval-stage{1,2,3,6}-acceptance.md`

`queries.jsonl`：80 条；按 scene 分组 **commercial 14 / compliance 13 / data 13 / ip 13 / liability 14 / subject 13**（6 场景均 ≥10，满足「各 ≥10」）。
> 注：任务清单仅要求 stage{1,2,3,6} 四份；stage 4/5 验收文档不在清单内（与历史遗留「4/5 文档未落盘」一致），不判 FAIL。

## 3. 第 2 步：可复现性（PASS，最关键）

按当前 `retrieval.yaml` 配置跑两次（deterministic provider，seed 固定）：

```
[accept-a] S5_context|E1  recall@5=0.5896  mrr@10=0.4440  ndcg@10=0.4764
[accept-b] S5_context|E1  recall@5=0.5896  mrr@10=0.4440  ndcg@10=0.4764
```

逐位比对 `metrics.json` 的 `overall`：

- `recall`/`mrr`/`ndcg` 三者 **sha256 内容逐位相同**。
- 唯一差异字段：`index_ms`、`latency_ms_p50/max/mean`（计时，任务允许「耗时可差异，指标不可」）。

→ 复现性成立。

## 4. 第 3 步：指标与结论真实性（PASS）

- `lab/out/report.md`（组号索引）声称：**15 组**、第 15 组 `S5_context|E3` recall@5=**0.6146**。
- `lab/out/v1/metrics.json` 核验：15 组合全部 `status=ok`；第 13 组 `S5_context|E1`=0.5896、第 15 组 `S5_context|E3`=0.6146 —— **与报告逐位一致，非手抄编造**。
- 复跑最优组 `S5_context|E3` → `recall@5=0.6146`（Δ=0，落在 0.61±0.02 内）。
- 局限（如实记录）：15 组**全部 `bm25=on`**，无 `bm25=off` 对照组 → 无法隔离 BM25 单独贡献，属结论局限。

## 5. 第 4 步：三处已知问题（A=FAIL；B/C 如实记录）

### A. rerank —— **FAIL（事实）**

> **阶段 5 的 rerank 未接入生产（`lab/rerank.py` 已实现但生产 RetrievalService 未接线），retrieval.yaml 的 `use_rerank=false` 是基于「未接入」而非「实验结论」。**

事实依据（不淡化）：
- `lab/rerank.py`、`lab/run_rerank.py` **确实存在且是可运行 CLI**（`--reranker {live,deterministic}`、`--limit` 最小样例均在）。
- 但全仓库**无任何已提交/落盘的 `rerank.json`**（其输出 `lab/out/<label>/rerank.json` 不存在）；`lab/out/v1/report.md` **无 rerank 节**（report.py 仅在检测到 rerank.json 时追加）。
- 唯一 `rerank=on` 运行是 `lab/out/switch_check/` 的 `S1_whole|E1`，其 `embedding_usage.source=deterministic-hash, tokens=0` —— 是**确定性 provider 的开关通断测试**，非真实 `gte-rerank-v2` 对最优组的 on/off 对比。
- 真实 `gte-rerank-v2` 的 on/off 对比**从未以可复现产物形式交付**；`retrieval.yaml` 的 rerank 段自述「阶段 5 的 gte-rerank-v2 对比尚未产出可复现报告」属实。

### B. E3 替身 —— 必须写入的结论（事实）

```
lab/providers.py:48   "E3": ((2,), "cg-e3-char2-lowdim")
lab/providers.py:132  class DeterministicVectorizer:  # 字符 n-gram + signed hashing trick → L2 归一化；完全确定性、零 IO
lab/providers.py:143  h = zlib.crc32(cleaned[i:i+n].encode("utf-8"), self._seed)
```

- E1/E2/E3 在默认 `deterministic` provider 下**全部是确定性哈希投影基线**（字符 n-gram + crc32），并非真实 embedding 模型（真实模型需 `127.0.0.1:9997` 推理服务，本机未监听；且 E3 的 512 维与 `law_chunk.embedding vector(1024)` 不兼容）。
- **矩阵最优结论（第 15 组 `S5_context|E3`，recall@5=0.6146）建立在离线确定性哈希投影替身上，不能用于模型选型。**
- 整张 15 组矩阵本质是「三种哈希粒度 × 切分策略」的对比，不是「三个 embedding 模型」的对比；报告顶部已声明此点（诚实）。

### C. 未提交 —— 已在第 0 步处理（PASS）

39 项改动已提交至 `wip/rag-lab-acceptance@34a3c3e`，验收期间可回滚；未推送、未 amend `dc71a87`、未 force push。

### 额外发现（文档与数据不一致，按铁律#3 记录事实）

- `docs/rag-lab.md` 第 4 节第 99 行写「最优参数组合是 `S5_context|E1`，`recall@5 = 0.6146`」——但权威 `v1/metrics.json` 显示 **0.6146 属于 E3（替身）**，E1 实为 **0.5896**。即把「矩阵最优组的数值」错挂到「生产落地组合 E1」名下。
- `docs/rag-lab.md` 第 4 节声称「rerank 没有证明出统计显著的收益」却**无 on/off 指标表**，与 `retrieval.yaml`「对比尚未产出可复现报告」自相矛盾。
- 这两处需在决策后修正（见第 8 节）。

## 6. 第 5 步：端到端真用（核心判据已验证；全栈 e2e 受环境限制）

### 已验证（强证据）

- PG 在线（`127.0.0.1:55432` accepting）；`law_chunk` 共 **13191 行**，其中 **2584 行有 embedding**（正好是生产策略 `S5_context`），**13191 行均有 `article`** → 数据基础具备。
- 直接调用与 `/v1/retrieval/search` 等价的 `RetrievalService`（因整 App 依赖 langgraph/langchain 重管线，未起全量 uvicorn，避免误引入无关失败）：
  - 引用 **100/100 为真实法条**（`law=民法典, article=第二十四条/第十八条…`，**0 条 sim-00x**），20 查询覆盖 69 个不同 article。
  - SQL 溯源：返回的 3 个真实 `chunk_id`（含「民法典 第二十四条/第十八条」「民事诉讼法 第二百零一条」）**100% 命中 `law_chunk`**。
  - 单次检索延迟（BM25-only）：**P50=4.76ms、P95=6.36ms**（远低于 1s，无风险）。
- 降级契约（代码 + 单测双重证据）：`PythonRetrievalClient.java` 钉死 **HTTP/1.1 + 3s 超时 + 最多 2 次（首+重试1）+ 任意异常 catch→WARN 并返回空列表（绝不抛→不 500）**；`LiveLawSearchToolTest.pythonDownReturnsEmptyAndWarnsInsteadOfThrowing` 覆盖「Python 不可达→空数组+WARN+不抛」。契约 `{id,article,text,score}` 且 `id=chunk_id` 由 `parseHits` 与单测确认。

### 局限（如实记录，非实现失败，但影响「完整 e2e」判定）

1. **DashScope `text-embedding-v3`（E1）当前在本环境返回 HTTP 400**（key 存在 len=116，但 embedding 端点 400）→ live 的 **dense 混合检索无法 live 跑通**，本次用 **BM25-only** 验证「真实法条引用」判据。chunk 向量是早前成功灌入 PG 的。建议用可用 key 重跑以补全 hybrid 验证。
2. **完整 Java+MySQL+Redis+RabbitMQ 全栈 e2e 未在沙箱执行**（上传合同→建 review→跑审查；以及「停 Python→审查仍完成且 citations 空+unverified、不 500」）。降级契约已由单测覆盖；`LiveLawSearchToolTest` 的两个真机联调测试（`hitsRealPythonRetrievalEndpoint` / `degradesWhenRealPythonIsDown`）由 `@EnabledIfEnvironmentVariable` 门控，需部署环境跑。**建议实现方在部署环境补跑全栈 e2e**。

## 7. 第 6 步：回归与契约（PASS）

- Python：`uv run pytest -q` → **353 passed, 0 failed**（≥ 改动前，无倒退）。
- Java：`mvn test` 默认 JDK17 → **类版本错配红**（`AdminControllerTest` 由 JDK21 编译，class version 65.0，JDK17 只认到 61.0，`Tests run:0`）。项目 `pom.xml` 要求 `<java.version>21</java.version>`；本机无 JDK21 但有 **jdk-25**（≥21）。切换 `JAVA_HOME=jdk-25` 后 `mvn clean test` → **BUILD SUCCESS，103 passed, 0 fail, 2 skipped**（那 2 个 skipped 正是门控的真机联调测试，与第 5 步局限一致）。→ **红是环境/JDK 不匹配，非代码回归**。
- Web：`npm run test:unit` → **36 passed, 0 fail**。
- 契约：`git diff dc71a87 HEAD -- contracts/` → **0 行**（契约未改）。
- 前端：`git diff dc71a87 HEAD --stat -- web/` → **0 行**（前端未改）。

## 8. 第 7 步：结论可写性（简历用）

| 数字 / 结论 | 能否写简历 | 理由 |
| --- | --- | --- |
| 检索实验台 5×3=15 组矩阵，recall@5 区间 ≈0.5708–0.6146 | ✅ 可写 | v1/metrics.json 原始支撑、可复现、口径清 |
| 最优组合 `S5_context\|E3` recall@5=0.6146（确定性基线，未落地） | ✅ 可写（须注明基线属性） | 原始 metrics 支撑 |
| 生产落地组合 `S5_context\|E1` recall@5=0.5896（唯一真实跑通档位） | ✅ 可写 | 原始 metrics + 复跑一致 |
| 两次重跑 recall/mrr/ndcg 逐位一致（Δ=0） | ✅ 可写 | 复现框架成立 |
| BM25+RRF(k=60)+候选池50 的混合检索接入生产（Java law.search 转发 Python） | ✅ 可写 | 代码 + 单测 + 端到端引用验证 |
| 端到端引用为真实法条（100/100 非 sim-00x）、可优雅降级不 500 | ✅ 可写 | 第 5 步证据 |
| rerank 提升 X% / rerank 已验证 | ❌ 不可写 | 阶段 5 未产出可复现 on/off 对比（FAIL A） |
| E3 是「最优 embedding 模型」 | ❌ 不可写 | E3 是哈希替身，不能用于模型选型（4B） |
| 真实 embedding 模型 A 优于 B | ❌ 不可写 | 整矩阵是哈希粒度对比，CI 全部含 0 |
| 改造后风险召回率提升 | ❌ 不可写 | 阶段 7 实测 live recall 0.9658→0.2906（架构缺陷暴露，非提升） |
| 检索变准 = 审查变好 | ❌ 不可写 | 阶段 7 证明二者解耦前不成立 |

**按当前证据，简历里最多能写的那句话：**

> 从零搭建 RAG 检索实验台（5 切分×3 档位=15 组矩阵 + 可复现评测框架），落地混合检索（BM25+RRF+真实法条库）并接入生产审查链路，端到端引用为真实法条、可优雅降级；矩阵基于确定性基线，模型选型结论待真实 embedding 接入后补。

## 9. 第 8 步：收口

### 本次提交

- 第 0 步已建基线 commit `34a3c3e`（含 39 项全部改动）。
- 本报告 `docs/retrieval-acceptance.md` 作为 `docs:` 提交（不 amend `dc71a87`、不 force push）。
- 关于「提交拆分为 feat(retrieval)/feat(java)/docs」：与第 0 步「一次性固化全部改动为基线」指令互斥（拆分需重写基线，等于 amend/交互式 rebase）。**已在第 0 步按显式指令提交为单基线**；建议在合并前由实现方用交互式 rebase 将 `34a3c3e` 拆为 3 个逻辑 commit（不 amend `dc71a87`、不 force push）。

### FAIL 项与根因

| FAIL / 问题 | 根因 | 需决策 |
| --- | --- | --- |
| **A. rerank 未完成（FAIL）** | 真实 `gte-rerank-v2` 的 on/off 对比从未跑出可复现产物；`use_rerank=false` 基于「未接入生产」（`lab/rerank.py` 已实现但生产服务未接线） | **已决策：本轮不作为结论，待召回链路修好重做。** `retrieval.yaml` 的 rerank 段与 `rag-lab.md` §4 已据本 FAIL 事实如实化——明确「已实现、未接入生产；候选池可能缺 gold 致 rerank 评测无法归因（假阴性），待召回链路修好重做，本轮不作为结论」，并修正 §4 第 99 行 E1/E3 数字错挂（0.6146 归 E3 替身，E1 实为 0.5896），删除「无显著收益」未举证表述（见 §9 决策落实） |
| B. E3 替身（事实） | 默认 provider 全为哈希投影；最优组恰为 E3 | 简历/对外表述须注明「基线属性」，不得称模型结论 |
| rag-lab.md §4 第99行 E1/E3 数字错挂 | 文档笔误：0.6146 属 E3，误写 E1 | 已修正 §4 措辞 |
| 第 5 步全栈 e2e 未跑 | 沙箱缺 MySQL/Redis/RabbitMQ 全栈 | 部署环境补跑；真机联调测试已就绪（门控环境变量） |
| DashScope E1 当前 400 | 环境/key 问题，非代码 | 用可用 key 重跑补全 hybrid 验证 |
| mvn 默认 JDK17 红 | 项目需 JDK21+，沙箱默认 java=17 | 环境要求文档化（README/CI 标注 JDK21+） |

### PASS/FAIL 统计

- **PASS**：第 0、1、2、3、4B、4C、5（核心）、6 各子项、7、8。
- **FAIL**：第 4A（rerank 未完成 on/off 对比）。
- **部分/受限**：第 5 步全栈 e2e（核心判据已验证，全栈受环境限制）。
- **环境/工具链问题（非实现回归）**：mvn 默认 JDK 错配（已用 jdk-25 校正转绿）；DashScope 400（未校正）。

### 决策落实（验收后，由用户拍板）

- **rerank → 已实现、未接入生产，本轮不作为结论**：已据 4A FAIL 事实将 `python-agent/config/retrieval.yaml` 的 rerank 段与 `docs/rag-lab.md` §4 如实化——
  明确三点：①状态=已实现未接入生产（`lab/rerank.py` 保留为实验代码，不删除）；②原因=当前召回链路架构缺陷（风险召回 96.6%→29.1%），候选池可能缺 gold 致 rerank 评测无法归因（假阴性）；③结论=待召回链路（风险覆盖与引用证据解耦）修好后重做 on/off 对比，本轮不作为结论。并修正 §4 第 99 行 E1/E3 数字错挂（0.6146 属 E3 替身，E1 实为 0.5896），删除「无显著收益」未举证表述；§7 结论第 4 条同步更正。**简历不写 rerank。**
  本次改动**仅触及配置注释与文档**，未触碰业务代码、未改 `use_rerank` 的取值（仍为 `false`）、未改契约与前端。
- 提交于 `wip/rag-lab-acceptance`（不 amend `dc71a87`、不 force push）。

### 三个已知问题的结论（原文）

- **A**：阶段 5 的 rerank 未接入生产（`lab/rerank.py` 已实现，但生产 RetrievalService 未接线），retrieval.yaml 的 `use_rerank=false` 是基于「未接入」而非「实验结论」。
- **B**：矩阵最优结论（S5_context|E3，recall@5=0.6146）建立在离线确定性哈希投影替身上，不能用于模型选型。
- **C**：已在第 0 步处理——39 项改动提交至 `wip/rag-lab-acceptance@34a3c3e`，验收期间可回滚；未推送、未 amend `dc71a87`、未 force push。
