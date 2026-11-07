# 阶段 6 验收｜把最优参数接回 ClauseGuard

日期：2026-11-07
范围：仅「检索实验台」子模块。未改 LangGraph 拓扑、前端、`contracts/openapi.yaml`、`events.schema.json`；Python 未连业务库 MySQL；未新增框架级依赖。

## 1. 目标与结论

让 `law.search` 返回真实法条，参数来自实验台结论；引用白名单逻辑不变（只是候选集合从 mock 变成真结果）；提供 `mock | live` 兼容开关。

**结论：PASS（3/3 端到端 + 全量回归全绿）。**

## 2. 文件清单

| 文件 | 变更 |
| --- | --- |
| `python-agent/config/retrieval.yaml` | 新建。strategy / embedding / use_bm25 / use_rerank / top_k / fusion 等，每字段注明来源组号 |
| `lab/out/report.md` | 新建。组号索引，供 YAML 注释引用 |
| `python-agent/retrieval/config.py` | 用 `YamlConfigSettingsSource`；路径由 `__file__` 推导，不依赖 CWD；环境变量 > 根 `.env` > YAML > 代码默认值 |
| `python-agent/retrieval/api.py` | `k` / `strategy` / `use_bm25` / `use_rerank` 缺省回落到 YAML，响应回显生效配置 |
| `python-agent/retrieval/acceptance.py` | 向量补齐改为「生效策略独占向量」并加断言；`REINDEX` + `ANALYZE` 后复查 HNSW |
| `python-agent/retrieval/acceptance_stage6.py` | 新建。三项端到端验收（真法条 / 降级 / 重启生效） |
| `python-agent/retrieval/stage6_probe.py` | 新建。独立进程探针，验证「改完重启生效」 |
| `python-agent/app/tools/retrieval_backend.py` | 新建。live 走真实检索并映射为工具候选（id = chunk_id），失败降级空候选 + WARN |
| `python-agent/app/tools/{law,policy,similar}.py` | 按 mode 分流：live 用真实结果，mock 保留固定语料 |
| `python-agent/tests/conftest.py` | 新建。测试环境默认 mock，既有测试不受 live 影响 |
| `python-agent/tests/test_retrieval_stage6.py` | 新建。YAML 生效、字段映射、降级，不连库不发网络 |
| `java-service/.../mcp/PythonRetrievalClient.java` | 新建。3s 超时、失败重试 1 次、失败 WARN + 空结果，不抛异常 |
| `java-service/.../mcp/LiveLawSearchTool.java` | 新建。`POST /v1/retrieval/search` → `[{id, article, text, score}]` |
| `java-service/.../mcp/McpToolsConfig.java` | 按 `clauseguard.retrieval.mode` 条件注册 law.search Bean（默认 live） |
| `java-service/src/main/resources/application.yml` | 显式声明 `retrieval.mode` / `retrieval.timeout` |
| `java-service/src/test/.../LiveLawSearchToolTest.java` | 新建。契约、重试、超时、降级、mode 开关、真机联调 |

## 3. 配置来源与偏差说明

`python-agent/config/retrieval.yaml` 每个字段都标注 `lab/out/report.md` 组号。两处**有意偏离报告最优组**：

- `embedding: E1` 而非第 15 组的 `E3`。E3 是离线确定性哈希投影（512 维、依赖本地 `127.0.0.1:9997`），本机无该服务，且 512 维写不进 `law_chunk.embedding vector(1024)` 单列；报告 §5 已明确所有档位差值 95% CI 含 0、不足以作模型选型结论。故取同策略下唯一真实跑通且已入库的 E1（百炼 `text-embedding-v3`，1024 维），第 15 组作为「未落地最优」留痕。
- `use_rerank: false`。矩阵 15 组固定 off；阶段 5 的 `gte-rerank-v2` 对比尚未产出可复现报告，生产检索服务也还没接 `lab/rerank.py`，置 true 会退化成 503。

其余字段（S5_context / bm25=on / top_k=5 / rrf(k=60) / candidate_k=50）与第 15 组一致。

## 4. 关键设计决策（为什么）

- **条件注册一个 Bean，而非工具内部判 mode**：MCP 客户端看到的工具名/描述必须与实际行为一致，live 下还挂着 `mock_law_search` 会让 Inspector 使用者误判数据来源。
- **JDK HttpClient 而非 WebClient**：要的是「每次尝试独立 3s 硬截止 + 同步重试一次」，`HttpRequest.timeout` 直接表达；响应式客户端的 timeout 从订阅起算，重试容易把两次算进同一段预算。
- **强制 `HTTP/1.1`**：默认 HTTP_2 会在明文请求带 `Upgrade: h2c`，uvicorn/h11 当作协议升级请求，body 不交给 ASGI，FastAPI 回 422 `{"loc":["body"],"type":"missing"}`。真机联调时抓包定位。
- **`connect_timeout=2`**：库挂掉时 libpq 默认等 20~30s 才报错，一轮审查多次工具检索会被拖到 130s+；检索是旁路能力，必须快速失败降级。
- **验收用独立进程探针**：`get_retrieval_settings` 是 `lru_cache`，同进程改 YAML 不会重读；「改完重启生效」必须真重启，不能用 `cache_clear` 假装。
- **生效策略独占向量**：多策略同时带向量会让规划器改走 Seq Scan，HNSW 失效；已在阶段 2 验收脚本里加硬断言。

## 5. 验收真实输出

### 5.1 阶段 2 真库回归（`_pg_tmp/stage6_acceptance_bootstrap3.log`）

```
[8] 延迟：每种模式各 40 次（预置 query embedding，本地检索）
===== 延迟 P50 < 200ms =====
模式          min(ms)  P50(ms)  max(ms)  结论
dense-only  5.66     5.99     6.66     PASS
bm25-only   6.32     7.16     9.12     PASS
hybrid      12.5     13.27    15.09    PASS

[9] POST /v1/retrieval/search 真实响应
    HTTP 200
    elapsed_ms=14.756  hits=3
    config={... "strategy": "S5_context", "use_bm25": true, "use_rerank": false, "top_k": 3}
    top1={"chunk_id": "civil_code:第五百八十五条:0:S5_context", ...}
    dense∩bm25 top5 重叠数 = 4（混合检索有融合价值）
===== 验收结论：全部 PASS =====
```

### 5.2 阶段 6 端到端（`_pg_tmp/stage6_e2e_final.log`）

```
[1] 完整审查 citations 为真实法条 ... PASS
  status=200 findings=2 citations=2 unverified_count=0 degraded=false
  sample_source_ids=["civil_code:第五百八十五条:0:S5_context", ...]
  sample_titles=["《民法典》第五百八十五条", ...]

[2] Python 检索后端关停仍完成审查 ... PASS
  status=200 findings=2 citations=0 risk_levels=["unverified"]
  unverified_count=2
  warn_sample="live retrieval unavailable, degrade to empty candidates: connection timeout expired"

[3] retrieval.yaml 改完重启生效 ... PASS
  before : yaml_top_k=5 effective_top_k=5 hits=5 top1=civil_code:第五百八十五条:0:S5_context
  after  : yaml_top_k=2 effective_top_k=2 hits=2 top1=civil_code:第五百八十五条:0:S5_context
  restored: yaml_top_k=5 effective_top_k=5 hits=5 top1=civil_code:第五百八十五条:0:S5_context

===== 阶段 6 验收：3/3 PASS =====
ACCEPT_EXIT=0
```

`sim-00x` 已不再出现；`citations` 命中本次真实返回集合，白名单逻辑未改。

### 5.3 Java ↔ Python 真机联调（真实 uvicorn 8010）

```
$ curl http://127.0.0.1:8010/healthz
{"status":"ok","service":"python-agent"}

$ CLAUSEGUARD_RETRIEVAL_E2E_URL=http://127.0.0.1:8010 mvn -q test -Dtest=LiveLawSearchToolTest
JAVA_TO_PYTHON_LIVE_HITS=5 TOP1=civil_code:第五百八十五条:0:S5_context ARTICLE=《民法典》第五百八十五条
MVN_EXIT=0
```

Java 全量测试日志中的降级行为（预期内的 WARN，非失败）：

```
WARN PythonRetrievalClient : python retrieval attempt 1/2 failed: HTTP 503 body={"detail":"backend down"}
WARN PythonRetrievalClient : python retrieval attempt 2/2 failed: HTTP 503 body={"detail":"backend down"}
WARN PythonRetrievalClient : python retrieval unavailable after 2 attempts; law.search returns empty hits, review continues
WARN PythonRetrievalClient : python retrieval attempt 1/2 failed: java.net.http.HttpTimeoutException: request timed out
WARN PythonRetrievalClient : python retrieval attempt 2/2 failed: java.net.http.HttpTimeoutException: request timed out
```

停掉 Python 后 `JAVA_TO_PYTHON_DOWN_HITS=0 (review continues)`，无 500。

### 5.4 全量回归

| 检查 | 命令 | 结果 |
| --- | --- | --- |
| Python 全量 | `pytest -q` | 353 passed，`PYTEST_EXIT=0`（仅 jieba 三方 SyntaxWarning） |
| Java 全量 | `JAVA_HOME=jdk-25 mvn -q clean test` | `MVN_EXIT=0` |
| 静态检查 | `ruff check` / `ruff format --check`（阶段 6 的 11 个文件） | `All checks passed!` / `11 files already formatted` |

## 6. 变更摘要

- 生产检索参数从「散落在代码默认值」收敛到单一 `retrieval.yaml`，每项可追溯到实验台组号；改完重启生效已实测。
- `law.search` 从 Java 本地 mock 改为 HTTP 转发 Python，检索能力（切分 / 向量 / BM25 / 融合 / rerank）全部留在 Python；契约 `[{id, article, text, score}]` 不变，`id` 现在是 `chunk_id`。
- Python 审查链路三个检索工具在 live 下返回真实候选；白名单校验代码零改动。
- 两级降级：Java MCP 侧「空 hits + WARN，不阻断审查」；Python 审查链路侧「空候选 → unverified 标记」。
- `mode=mock|live` 双端开关，默认 live，mock 供离线演示。

## 7. 遗留风险

1. **E3 最优组合未落地**：见 §3。要真正定论需先补阶段 5 rerank 报告、把 E3 的 512 维投影迁到 1024 维或改表结构，再重跑矩阵。
2. **`use_rerank` 目前不可用**：生产服务未接 `lab/rerank.py`，置 true 会 503。
3. **两套 mode 开关需保持一致**：Java 用 `clauseguard.retrieval.mode`，Python 用 `CLAUSEGUARD_RETRIEVAL_MODE`。配置不一致时会出现「Java live 转发、Python 却走 mock」之类的错配，部署脚本应统一注入。
4. **DSN 是本机验收集群**：`127.0.0.1:55432` 属于 `_pg_tmp/`，生产必须用 `CLAUSEGUARD_RETRIEVAL_PG_DSN` 覆盖；且需按阶段 2 验收脚本灌库，遵守「生效策略独占向量」，否则 HNSW 退化成 Seq Scan。
5. **Java MCP 降级只给空 hits**：`unverified` 标记是 Python 审查链路工具降级时打的。若某条链路只经 Java MCP 而不经过 Python 工具，空引用不会自动带 unverified，需在调用方补标记（本阶段按铁律未改 events 契约）。
6. **`policy.search` 仍是 mock**：本阶段任务只覆盖 `law.search`。
7. **历史数据**：旧存档/前端若硬编码 `sim-` 前缀 ID 做展示或比对，需要清理；本阶段未改前端。
8. **全仓格式**：`ruff format --check` 对全仓仍有 62 个历史文件不合规，本次只格式化阶段 6 涉及的 11 个文件，未做全仓格式化以免污染无关改动。
9. **阶段 4 / 5 验收报告未落盘**：`docs/retrieval-stage4-acceptance.md`、`docs/retrieval-stage5-acceptance.md` 仍缺，本报告引用的组号来自 `lab/out/report.md` 索引与 `lab/out/v1/report.md` 原始数据。
