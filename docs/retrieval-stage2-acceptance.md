# 检索实验台 · 阶段 2 验收报告（pgvector 索引 + BM25 + 融合）

> 日期：2026-11-07
> 范围：`python-agent/retrieval/` 检索层 + `POST /v1/retrieval/search`。
> **未改动** LangGraph 拓扑、前端、`contracts/openapi.yaml`、`events.schema.json`、
> Java 侧任何业务代码；未引入框架级新依赖（仅允许清单内的 jieba / rank-bm25 / numpy）。
> 所有输出均为本机真实运行结果，命令可复现。

---

## 0. 口径与环境

| 项 | 值 |
|---|---|
| 数据库 | PostgreSQL 16.15 + pgvector 0.8.6，独立集群 `127.0.0.1:55432` |
| 库名 | `clauseguard_retrieval`（与业务 MySQL 完全隔离） |
| Embedding | E1 百炼 `text-embedding-v3`，1024 维，OpenAI 兼容 `/v1/embeddings` |
| 语料 | 阶段 1 的 9 部法律 / 2584 条真实法条 / 5 策略 13191 个 chunk |
| 检索实现 | HNSW cosine（`<=>`）+ jieba/rank_bm25 + RRF(k=60) |

**为什么不用 Docker**：本机 `docker compose up -d` 拉 pgvector 镜像返回
`unexpected media type text/html ... not found`，Docker daemon 亦未运行。为不阻塞验收，
改为「复制本机 PG 运行时到可写目录 + conda-forge 预编译 `vector.dll` + `initdb` 建独立
trust 集群」，端口 55432，不动用户正在使用的 5432 实例、不改系统目录。
该临时环境位于 `_pg_tmp/`（已加入 `.gitignore`），可随时删除重建。

---

## 1. 变更摘要（文件 + 一句话）

| 文件 | 变更 |
|---|---|
| `python-agent/retrieval/schema.sql` | 独立 DDL：`CREATE EXTENSION vector`、`law_chunk`、`vector(1024)` 列、HNSW cosine 索引、strategy 索引；**不进** Java Flyway |
| `python-agent/retrieval/config.py` | `RetrievalSettings`：E1/E2/E3 档位、融合权重、候选数、批大小全部配置化；`env_file` 按文件位置定位根 `.env` |
| `python-agent/retrieval/store.py` | PG 存取层；本轮两处关键修复见 §4：CTE + `iterative_scan` 让 HNSW 生效、标准库连接池消除建连开销 |
| `python-agent/retrieval/embedder.py` | 批量 32 组织 / 服务端上限 10 切子批、失败重试 2 次、token 消耗统计、维度 fail-fast、并发回填按输入下标对齐 |
| `python-agent/retrieval/stopwords.txt` | 中文停用词；刻意保留「应当/必须/不得/规定」等承载法律义务语义的词 |
| `python-agent/retrieval/bm25.py` | jieba 分词 + 停用词/单字/纯数字过滤 + `BM25Okapi` 内存索引，启动时从 PG 加载 |
| `python-agent/retrieval/fusion.py` | `rrf_fuse`（k=60，支持权重）与 `weighted_fuse`（min-max 归一化后线性加权），统一入口 `fuse` |
| `python-agent/retrieval/service.py` | `search(query,k,strategy,embedding,use_bm25,use_rerank)` → `list[Hit]`；单路召回不套融合公式；`use_rerank=True` 显式报错而非静默忽略 |
| `python-agent/retrieval/api.py` | `POST /v1/retrieval/search`，同步 `def` 走线程池；懒加载 service，端点回显配置（不含密钥） |
| `python-agent/retrieval/acceptance.py` | 可执行验收：建表→全量入库→count 对账→三组对比→**HNSW EXPLAIN 硬断言**→P50→端点；拒绝指向 5432 的 DSN |
| `python-agent/app/main.py` | 仅在 `create_app()` 末尾 `include_router`，不碰审查图 |
| `python-agent/pyproject.toml` | 新增 jieba / rank-bm25 / numpy；`jieba.*`、`rank_bm25.*` 的 mypy override |
| `python-agent/tests/test_fusion.py` | 7 项：RRF 共享排名、k 改变分差、权重、边界；加权退化、量纲归一、入口分发 |
| `python-agent/tests/test_retrieval_stage2.py` | 11 项：分词/停用词、BM25 排序、批次切分、重试与 token、维度 fail-fast、三模式 service、端点与配置回显；全部用 FakeStore/MockTransport，不读本机 `.env`、不连真库 |
| `python-agent/app/config.py`、`app/agents/settings.py` | 仅缩短行尾注释，修复 `ruff check .` 的两处 E501 |
| `.gitignore` | 忽略 `_pg_tmp/`（本地验收集群） |
| `docs/retrieval-stage2-acceptance.md` | 本报告 |

---

## 2. 验收表（项 / 命令 / 实际输出 / 结论）

| 项 | 命令 | 实际输出 | 结论 |
|---|---|---|---|
| 建表 + 全量写入 | `python -m retrieval.acceptance` | `[1] schema.sql 执行完成`；`[3] 全量写入 13191 行` | PASS |
| `SELECT count(*)` 与切分统计一致 | 同上 | 5 策略逐项 + 合计 `13191 == 13191`，全部 PASS | PASS |
| 真实向量化 | 首次运行 `_pg_tmp/acceptance_run1.log` | `token=158534 requests=323 retries=0 elapsed=18.4s`，`embedding IS NOT NULL = 2584` | PASS |
| dense-only / bm25-only / hybrid | 同上 | 三组各 5 条结果（§3.3），top1 均为民法典第 585 条，dense∩bm25 top5 重叠 = 4 | PASS |
| HNSW 索引真的被用 | 同上 `[7]` | `USES_HNSW=True SEQ_SCAN=False BTREE_FALLBACK=False candidates=50/50` | PASS |
| 单次检索 P50 < 200ms | 同上 `[8]` | dense 6.05 / bm25 6.80 / hybrid 13.60 ms | PASS |
| 服务端点 | 同上 `[9]` | `HTTP 200 hits=3 elapsed_ms=12.821`，config 不含密钥 | PASS |
| 融合函数单测 | `pytest tests/test_fusion.py tests/test_retrieval_stage2.py -q` | `18 passed` | PASS |
| 三文件回归 | `pytest tests/test_fusion.py tests/test_retrieval_stage2.py tests/test_chunker.py -q` | `48 passed`，退出码 0 | PASS |
| Python 全量 | `pytest -q` | 点阵全绿（346 项），`FULL_PYTEST_EXIT=0` | PASS |
| 静态检查 | `ruff check .` | `All checks passed!` | PASS |
| 类型检查 | `mypy retrieval` | `Success: no issues found in 15 source files` | PASS |
| Java 回归 | `JAVA_HOME=<jdk-25> mvn clean test -q` | 退出码 0（详见 §5 备注） | PASS |

验收脚本最终退出：`ACCEPT_EXIT=0`，末行 `===== 验收结论：全部 PASS =====`。

---

## 3. 真实输出片段

### 3.1 生效配置（密钥不回显）

```
dsn_host_port       127.0.0.1:55432/clauseguard_retrieval
embedding_backend   E1        model  text-embedding-v3   dim  1024
batch_size          32        provider_batch_limit  10
fusion_method       rrf       rrf_k  60
dense_weight 0.6    sparse_weight 0.4     candidate_k 50
key_present         true
```

`batch_size=32` 是「一次组织多少条」，`provider_batch_limit=10` 来自真实报错：百炼兼容端点
对 `batch>10` 返回 400 `batch size is invalid, it should not be larger than 10`。
`Embedder.iter_batches()` 先按 32 分组、再按 10 切子批并携带起始下标，保证并发回填不错位。

### 3.2 建表与 count 对账

```
[2] 语料 2584 条 / 9 部法律；5 策略切分合计 13191 个 chunk
[3] 全量写入 13191 行（文本/元数据，未覆盖已有向量）
[4] 默认策略 S1_whole: 需向量 2584 条，已有 2584 条
[5] 向量已齐，复用（不重复调用收费接口）

strategy      切分统计(阶段1)  DB count  一致
S1_whole      2584             2584      PASS
S2_fixed512   2593             2593      PASS
S3_fixed256   2687             2687      PASS
S4_paragraph  2743             2743      PASS
S5_context    2584             2584      PASS
合计          13191            13191     PASS
    embedding IS NOT NULL = 2584（应等于默认策略 chunk 数 2584）
```

**为什么只给默认策略做 embedding**：五种策略是同一批法条的五种视图，全部向量化会让调用量
翻五倍且 dense 候选池混入重复语义；实验台按 `strategy` 隔离检索，只有被选中的策略需要向量。
其余策略仍全量入库，保证 count 与阶段 1 统计逐项可对账。

### 3.3 三组结果对比（query = 违约金过分高于损失能否请求减少）

| 排名 | dense-only | bm25-only | hybrid (RRF) |
|---|---|---|---|
| 1 | 第五百八十五条 `0.8078` | 第五百八十五条 `46.9116` | 第五百八十五条 `0.032787` |
| 2 | 第五百八十八条 `0.7560` | 第五百八十八条 `23.1125` | 第五百八十八条 `0.032258` |
| 3 | 第五百九十一条 `0.7536` | 第五百九十二条 `16.8976` | 第五百九十一条 `0.031498` |
| 4 | 第五百九十二条 `0.7471` | 第五百九十一条 `16.6165` | 第五百九十二条 `0.031498` |
| 5 | 第五百八十四条 `0.7269` | 第八百五十八条 `15.9959` | 第五百八十四条 `0.030090` |

`dense∩bm25 top5 重叠数 = 4` —— 两路排序确有分歧，融合不是无意义拼接。
`Hit.score` 在融合模式是 RRF 分，`dense_score` / `sparse_score` 回显两路原始分，
避免「同一文档在 dense-only 与 hybrid 下分数不同却无法解释」。

### 3.4 HNSW 索引有效性（本轮新增的硬断言）

```
[7] HNSW 索引有效性（EXPLAIN ANALYZE 同一份 dense_search SQL）
    -> Index Scan using law_chunk_embedding_hnsw on law_chunk
       (cost=444.22..16923.76 rows=2584 width=420) (actual time=0.664..3.710 rows=200 ...)
    -> CTE Scan on candidates (rows=200 loops=1)
    Execution Time: 5.079 ms
    USES_HNSW=True  SEQ_SCAN=False  BTREE_FALLBACK=False  candidates=50/50
```

### 3.5 延迟（每种模式 40 次，预置 query embedding，不含外部 API 与 rerank）

```
模式          min(ms)  P50(ms)  max(ms)  结论
dense-only    5.59      6.05     7.23     PASS
bm25-only     6.16      6.80     8.29     PASS
hybrid       12.35     13.60    15.80     PASS
```

### 3.6 端点真实响应

```
[9] POST /v1/retrieval/search 真实响应
    HTTP 200
    elapsed_ms=12.821  hits=3
    config={"embedding_backend":"E1","embedding_model":"text-embedding-v3",
            "embedding_dim":1024,"fusion_method":"rrf","rrf_k":60,
            "dense_weight":0.6,"sparse_weight":0.4,"candidate_k":50,
            "default_strategy":"S1_whole","provider_batch_limit":10,
            "strategy":"S1_whole","use_dense":true,"use_bm25":true,
            "use_rerank":false,"elapsed_ms":12.821,
            "query_embedding_source":"provided"}
    top1=civil_code:第五百八十五条:0:S1_whole
```

---

## 4. 本轮主动发现并修复的两个真问题

首轮验收虽然 `ACCEPT_EXIT=0`、P50 也「达标」，但 `EXPLAIN` 暴露了两个会让结论在规模
扩大后失效的隐患。两处都做了根因定位、真库对照实验和修复，并写进验收脚本防止回归。

### 4.1 问题一：HNSW 索引建了，查询却没用它

**证据**（同一台库、同一 query 向量，`EXPLAIN ANALYZE`）：

| 写法 | 执行计划 | 耗时 |
|---|---|---|
| 无 strategy 过滤，默认 `ef_search=40` | `Index Scan using law_chunk_embedding_hnsw` | 5.10–5.63 ms |
| 同层带 `strategy = ...`（等值 / 参数化 OR 都一样） | `Index Scan using law_chunk_strategy_idx` + Sort | 19.75–20.26 ms |
| 同层 + `hnsw.iterative_scan=relaxed_order` | 仍是 `law_chunk_strategy_idx` | 19.77 ms |
| CTE `MATERIALIZED` + `ef_search=200` | `Seq Scan` + Sort | 18.94 ms |
| 无过滤 + `ef_search=200` | `Seq Scan` + Sort | 24.71 ms |
| 同层 + `enable_sort=off` 但**不**开 iterative | HNSW，但只返回 **40** 行（凑不满 50） | 5.34 ms |

**两个根因**：
1. `strategy` 谓词与 `ORDER BY distance` 同层时，规划器估算命中约 517 行，认为走 B-tree
   取行再排序更便宜，于是对命中行全量算 1024 维距离，HNSW 被完全绕过；
2. HNSW 默认搜索宽度 40，内层 `LIMIT 200` 也只吐 40 个候选。直觉做法是抬高
   `hnsw.ef_search`，但实测这会同时抬高 HNSW 的 cost 估算，规划器反而改走 `Seq Scan + Sort`。

**修复**（`retrieval/store.py`）：
- 用 `WITH candidates AS MATERIALIZED (...)` 把「取近邻」与「过滤 strategy」拆两层：
  内层没有 strategy 谓词可下推，HNSW 成为唯一可行路径，过滤留到外层，语义等价；
- 用事务级 `set_config('hnsw.iterative_scan','strict_order', true)` 让索引扫描持续迭代
  补候选，突破 `ef_search=40` 而不抬高 cost；`strict_order` 保证仍严格按距离有序，
  不像 `relaxed_order` 那样牺牲 top-k 精度；
- SQL 提为模块常量 `DENSE_SEARCH_SQL`，新增 `ChunkStore.dense_search_plan()` 让验收脚本
  对**同一份 SQL** 做 EXPLAIN 并硬断言 `USES_HNSW=True`，避免「验收的是另一条查询」。

**验证**：新方案 `Index Scan using law_chunk_embedding_hnsw`、`Execution Time 5.079 ms`、
内层候选 200 行、外层 50 行；top-50 与修复前 SQL 逐条比对 `RESULT_EQUAL_TOP50 = True`。

### 4.2 问题二：每次检索都在新建 PG 物理连接

定位 dense-only P50 155ms 与 SQL 本身 3.5ms 的巨大落差：

| 测量项 | P50 |
|---|---|
| `psycopg.connect()` 仅建连 | 125.84 ms |
| 修复前 `store.dense_search()`（每次新连接） | 154.45 ms |
| 同一连接直接跑 SQL | 3.52 ms |

第一版池加上后 P50 反而变成 163ms、`pool.qsize()` 恒为 0。查到根因：
**psycopg3 与 psycopg2 不同，`with conn:` 退出时会关闭连接**（实测退出后
`conn.closed=True`）。原来的 `with self.connect() as conn` 每次都是「新建 + 关闭」，
连接池自然永远为空。

**修复**：用标准库 `LifoQueue` 自己实现借还（池空新建、池满关闭，高并发不阻塞；
借出前 `SELECT 1` 探活），`connect()` 改成显式 `commit()` / `rollback()` 保留连接，
不引入 `psycopg_pool` 这类框架级新依赖。`close()` 供退出时排空。

**验证**：`pool.qsize()` 稳定为 1，`dense_search` 逐次 14.58 → 约 5.1 ms，P50 **5.25 ms**。
端到端 P50 变化：

| 模式 | 修复前 | 修复后 |
|---|---|---|
| dense-only | 155.64 ms | **6.05 ms** |
| bm25-only | 5.57 ms | 6.80 ms |
| hybrid | 165.06 ms | **13.60 ms** |

---

## 5. 测试与静态检查（原始输出）

```
$ pytest tests/test_fusion.py tests/test_retrieval_stage2.py tests/test_chunker.py -q
................................................                         [100%]
PYTEST_EXIT=0                     # 48 passed

$ pytest -q
........................................................................ [ 20%]
........................................................................ [ 41%]
........................................................................ [ 62%]
........................................................................ [ 83%]
..........................................................               [100%]
FULL_PYTEST_EXIT=0                # 346 项全绿

$ ruff check .
All checks passed!

$ mypy retrieval
Success: no issues found in 15 source files

$ JAVA_HOME='C:\Program Files\Java\jdk-25' mvn clean test -q
Starting ReviewControllerTest using Java 25.0.1 ...
MVN_RETRY_EXIT=0
```

**mvn 备注**：直接 `mvn test` 首次失败，报
`AdminControllerTest has been compiled by a more recent version of the Java Runtime
(class file version 65.0), this version ... only recognizes ... 61.0`。
根因是本机 `JAVA_HOME` 默认指向 `jdk-17`，而 `pom.xml` 的 `<java.version>` 是 21，
`target/` 里残留 Java 21 编译的 class。按「失败先重试一次」的策略，显式切到
`jdk-25` 并 `mvn clean test` 清掉旧 class 后通过 —— 属本机 JDK 混用，不是代码缺陷。

---

## 6. 遗留风险（阶段 3 之前需要知道的）

1. **只索引了默认策略**：当前仅 `S1_whole` 的 2584 条有向量，其余 4 策略只入库文本。
   要做「5 种切分策略的检索效果对比」，需把 13191 条全量向量化（约 5 倍调用成本）。
2. **候选池固定 200**：`pool = max(k*4, 200)`。全策略向量化后，若目标策略在近邻 200 名
   之外占比高，外层过滤会凑不满 k 条。届时需按命中率上调，或按 strategy 建 partial
   HNSW 索引 / 改用 `relaxed_order`。
3. **连接池是进程内的**：`uvicorn --workers > 1` 时每进程独立池，总连接数 = workers × 8。
   生产环境建议 pgbouncer 或 `psycopg_pool`（需放宽「不新增框架级依赖」约束）。
4. **BM25 全量内存索引**：启动时从 PG 一次载入，语料增长需重启；当前 2584 篇约 6 ms，
   到十万级需要持久化/分片方案。
5. **E2 / E3 未实机验证**：bge-m3、bge-small-zh 只做了配置档位与 OpenAI 兼容协议对接，
   本机没有对应推理服务；真实跑通的只有 E1 百炼。
6. **rerank 未接**：`use_rerank=True` 显式抛 `NotImplementedError`（留给阶段 3），
   不是静默忽略。
7. **真库环境是临时的**：`_pg_tmp/`（333 MB）为本机独立集群，不入库；Docker 路线因
   镜像拉取失败未验证。CI/他人机器需按 §0 重建或改用 compose。
8. **Java 构建前置条件**：`mvn test` 需 `JAVA_HOME` 指向 JDK ≥ 21；本机默认 jdk-17。
9. **jieba 上游 SyntaxWarning**：`invalid escape sequence '\.'` 等 3 条，来自第三方库，
   不影响运行，未修改第三方代码。

---

## 7. 禁止项自查

| 约束 | 状态 |
|---|---|
| 不用 Elasticsearch | 未引入，仅 PG + pgvector |
| 检索链路不调用 LLM | dense 召回只用预置/现算的 embedding，融合与 BM25 均为确定性计算；仅 `Embedder` 调 embedding 接口 |
| 不改 LangGraph / 前端 / openapi / events.schema | `git status` 对这些路径为空 |
| 不新增框架级依赖 | 仅 jieba / rank-bm25 / numpy + 标准库 `queue` |
| 注释写「为什么」 | 关键决策均附实测数据与原因 |
| 每阶段可执行验收 | `python -m retrieval.acceptance`，退出码 0 |
