# Stage 11 验收报告｜增量重审与缓存

- 日期：2026-10-25
- 目标：合同改 5% 的条款，只花 5% 的钱——未变更条款复用缓存结论，removed 标 stale 保留可追溯。

## 一、变更摘要

### 新增（Python 编排）

| 文件 | 职责 |
| --- | --- |
| `app/incremental/diff.py` | `ClauseDiff`/`IncrementalInfo` 模型 + `diff_clauses` 纯函数：按 `clause_no + hash` 比对得 changed/added/removed 三类；`clause_fingerprint`（hash 缺省时按文本 sha256 兜底） |
| `app/incremental/cache.py` | `cache_key = sha256(clause.hash + prompt_version + model + expert)`；`IncrementalStore` 协议（findings 缓存 + baseline 快照 + 命中统计）；`InMemoryIncrementalStore` 与 `JsonFileIncrementalStore` 两实现；`CacheSettings`/`build_incremental_store`/`get_store` |
| `app/graph/nodes/diff.py` | diff 节点：full 模式直通；incremental 模式加载基线 → diff → 缓存命中复用（miss 的进 `review_targets`）→ removed 的 findings 标 `stale=True` 保留 |
| `app/graph/nodes/cache_store.py` | cache_store 节点：写回本轮 findings 缓存 + 「审过且无发现」的 reviewed_pairs + baseline 快照 |
| `app/graph/state.py`（改） | `Clause.no/hash`、`Finding.stale`；`ReviewState` 增 `review_mode/base_version/review_targets/reviewed_pairs/incremental/baseline`；`incremental_targets()`/`scoped_clauses()` 收窄辅助 |
| `app/graph/build.py`（改） | 拓扑改为 ingest → **diff** → dispatch/serial_review；critic 通过后 → **cache_store** → human_approval → render |
| `app/graph/nodes/dispatch.py`/`serial_review.py`（改） | 派发/串行都按 `review_targets` 收窄（增量模式只审 changed+added）；增量零目标时仍汇入 critic 而非 END |
| `app/graph/nodes/expert_review.py`/`serial_review.py`（改） | 记账 `reviewed_pairs`（专家×条款实际审过对）——「无发现」也是可复用结论 |
| `app/graph/nodes/critic.py`/`human_approval.py`（改） | 校验与审批均跳过 stale 条目；critic 通过改指 cache_store |
| `app/render/service.py`（改） | `active_findings()`：报告口径剔除 stale（状态保留，红线遵守） |
| `app/graph/nodes/ingest.py`（改） | version 以 state 传入为准（mock meta 不再覆盖）——修复基线版本被冲掉的 bug |
| `app/main.py`（改） | `/v1/runs` 支持 `version/mode/base_version` 入参；`incremental_store` 可注入；`run.completed` 携带 `incremental` 摘要；findings_count/quality 均按剔除 stale 口径 |

### 契约与 Java

- `contracts/events.schema.json`：`RunCompleted` 增 `incremental` 对象（mode / changed_clause_ids / added_clause_ids / removed_clause_ids / unchanged_count / stale_findings / cache_hits / cache_hit_rate / reused_findings），重生成 `app/contracts/models.py`。
- `contracts/openapi.yaml`：`ReviewCreateRequest` 增可选 `version`(int) / `mode`(full|incremental) / `base_version`(int)，重生成 DTO。
- `PythonAgentClient.streamRun` 增版本/模式参数并透传至 `/v1/runs` 请求体；`ReviewService.createReview` 落库版本与透传一致（未指定时沿用 v1+full 旧语义）。

### 前端（web/，本阶段首次实装）

- `src/types/events.ts`：SSE 事件类型（含 incremental/artifact 字段）。
- `src/api/reviews.ts`：只走 Java `/api/v1/reviews`（不直连 Python）。
- `src/components/ClauseDiffList.vue`：变更条款高亮（changed=黄、added=绿、removed=红删除线）+ 「仅重审变更」按钮。
- `src/components/RunSummary.vue`：运行摘要（full/incremental、复用条数、命中率、token、artifact 下载）。
- `src/components/ReviewConsole.vue` + `App.vue`：接线。

### 测试

- `tests/incremental/test_diff.py`（三类 diff / 无编号条款退化为内容指纹 / 幂等）
- `tests/incremental/test_cache.py`（键四要素敏感性 / 命中统计 / JsonFile 持久化往返 / 安全名）
- `tests/incremental/test_nodes.py`（diff 节点缓存复用与目标收窄 / removed 标 stale / 派发收窄 / 零目标仍汇 critic / cache_store 写回）
- `tests/agents/test_acceptance_stage11.py`（全量 vs 增量 token 对比、增量 vs 全量一致率、prompt_version 必 miss、stale 追溯、run.completed 带 incremental 且全事件 Schema 校验、产出 eval 报告）
- 存量修正：stage7 并发事件数 6→9、critic 路由目标 human_approval→cache_store、SSE 序列补 diff/cache_store 起止、Java ReviewServiceTest 增透传断言两例。

## 二、验收结果（真实输出）

评测集：`tests/agents/fixtures.stage7_corpus` 前 40 条；改动 2 条（5%）+ 删除 1 条。

| 验收点 | 口径 | 实测 | 结论 |
| --- | --- | --- | --- |
| token 下降 | 增量 vs 全量重审 ≥ 60% | **95.0%**（16800 → 840） | ✅ |
| 结果一致率 | 增量 vs 全量 ≥ 95% | **100.0%**（15/15） | ✅ |
| prompt_version 必 miss | 升级后复用归零 | reused=0，token 回到 16800 | ✅ |
| 缓存命中率 | 未变更条款复用 | 100.0%（hits=38） | ✅ |
| stale 追溯 | 删除条款历史结论保留且不上报告 | v3 删除 1 条：历史 findings 全部标 stale 保留，有效 findings 与报告均不含该条款 | ✅ |

门禁：

| 门禁 | 输出 |
| --- | --- |
| `uv run pytest` | exit=0，279 passed（Stage 11 新增 41 个） |
| `uv run ruff check app tests` | All checks passed! |
| `uv run mypy app` | Success: no issues found in 52 source files |
| `mvn -o test` | Tests run: 26, Failures: 0, Errors: 0 — BUILD SUCCESS |
| `npm run build`（web） | ✓ built in 8.80s（vue-tsc 类型检查通过） |

## 三、过程中发现并修复的问题

1. **缓存键专家间撞车（真 bug）**：六专家 `prompt_version` 均为 1、model 相同，`sha256(hash+version+model)` 会把 A 专家的结论复用给 B 专家。→ 键中加入 `expert` 要素，测试补跨专家不撞键断言。
2. **省钱幅度上不去（设计缺陷）**：缓存只记录「有 finding」的条款（15/38），正常条款每次增量仍全量重审。→ 新增 `reviewed_pairs`（专家×条款实际审过对），「审过且无发现」同样落缓存，命中率 15/38 → 38/38，token 降幅拉到 95%。
3. **基线版本被冲掉（真 bug）**：ingest 的 mock meta 恒返回 version=1，覆盖 state 传入版本，导致增量永远拿 v1 做基线。→ ingest 以 state 版本为准。

## 四、遗留风险

1. **存储为进程内/单文件**：`InMemoryIncrementalStore` 不跨重启、不跨副本；`JsonFileIncrementalStore` 无并发写保护。生产需换 Redis/Postgres 实现（`IncrementalStore` 协议已抽象，替换即可，节点不动）。
2. **diff 按 clause_no 对齐**：合同重排条款或编号整体改写会被判成 changed+added+removed 组合，未做内容相似度模糊匹配——极端改版下增量退化为近全量（正确性不受影响，只费钱）。
3. **一致率基于 mock 模型**：真实模型输出有采样抖动，100% 是确定性 mock 下的口径；真实环境应以「同一评测集多次采样」评估。
4. **前端未与真实后端联调**：仅 build + 类型层通过；SSE 断线重连、审批交互需联调验证。
5. **Java 侧 mode 语义未落库**：`reviews.mode` 未持久化（仅透传），审计追溯看不到本次是增量还是全量。

## 五、下一阶段建议

1. **Stage 12｜观测与成本面板**：按 run/专家/条款聚合 token 与缓存命中，落 Java 审计表，前端 RunSummary 扩成趋势图。
2. **存储生产化**：`IncrementalStore` 的 Redis 实现（缓存 + baseline 双 key，TTL 与版本号管理），compose 接线。
3. **增量 diff 增强**：对 removed+added 做文本相似度匹配，识别「条款被移动/改写编号」而非误判删除+新增。
