# 检索实验台 · 阶段 4 矩阵报告

- label：`live_check`　seed：`20261107`　provider：`live`
- 数据集：`lab/dataset/queries.jsonl`（80 条，sha256 `538bf7866028a992…`）
- 配置：k=5　candidate_k=50　bm25=on　rerank=off　融合=rrf(k=60)　命中口径=article
- 组合：6 组（成功 1 / 失败 5），每组 80 条 query

> **口径声明**：本次 `provider=live`，chunk 向量来自阶段 2 写入 pgvector 的
> 真实百炼结果，query 向量由真实 `/embeddings` 端点计算并缓存。E2/E3 因
> 本地推理服务缺失 / 维度不匹配会记为 failed 并保留，**不参与最优评选**。

## 1. 主矩阵：recall@5

行 = 切分策略，列 = embedding 档位；每个单元格是该组合 80 条 query 的 macro 平均 recall。

| strategy \ embedding | E1 | E2 | E3 |
| --- | --- | --- | --- |
| `S1_whole` | 0.7625 | FAIL | FAIL |
| `S2_fixed512` | FAIL | FAIL | FAIL |

## 2. 每个组合的完整指标

所有指标按每条 query 等权 macro 平均；延迟为单次检索耗时，索引用时为组合级准备耗时。

| 组合 | 状态 | chunks | recall@5 | hit@5 | mrr@10 | ndcg@10 | 延迟 mean/p50 (ms) | 索引用时 (ms) | API tokens |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `S1_whole|E1` | ok | 2584 | 0.7625 | 0.7750 | 0.6217 | 0.6524 | 5.74 / 5.90 | 4459.9 | 1095 |
| `S1_whole|E2` | FAILED | - | - | - | - | - | - | - | - |
| `S1_whole|E3` | FAILED | - | - | - | - | - | - | - | - |
| `S2_fixed512|E1` | FAILED | - | - | - | - | - | - | - | - |
| `S2_fixed512|E2` | FAILED | - | - | - | - | - | - | - | - |
| `S2_fixed512|E3` | FAILED | - | - | - | - | - | - | - | - |

## 3. embedding token 成本

| 组合 | tokens | API 请求数 | 费用 (¥) | 说明 |
| --- | --- | --- | --- | --- |
| `S1_whole|E1` | 1095 | 10 | 0.0000 | chunk 向量来自阶段 2 已入库的真实百炼结果 |
| `S1_whole|E2` | - | - | - | 组合失败，未产生向量化消耗 |
| `S1_whole|E3` | - | - | - | 组合失败，未产生向量化消耗 |
| `S2_fixed512|E1` | - | - | - | 组合失败，未产生向量化消耗 |
| `S2_fixed512|E2` | - | - | - | 组合失败，未产生向量化消耗 |
| `S2_fixed512|E3` | - | - | - | 组合失败，未产生向量化消耗 |

## 4. 索引构建耗时拆分 (ms)

| 组合 | 切分 | chunk 向量 | query 向量 | BM25 | 合计 |
| --- | --- | --- | --- | --- | --- |
| `S1_whole|E1` | 39.1 | 1006.0 | 2259.9 | 1150.5 | 4459.9 |

BM25 索引与 embedding 档位无关，按 strategy 建一次后跨 3 个档位复用；向量矩阵按语料指纹缓存，重跑命中时该列为 0。


## 5. 结论

- **最优组合**：`S1_whole|E1`，recall@5=0.7625，mrr@10=0.6217，ndcg@10=0.6524，延迟 p50=5.90ms。

### 同策略、不同 embedding 的差值是否显著

对同一策略下的两两档位，用固定 seed 的**配对 bootstrap**（2000 次重采样 80 条 query）估计 recall 差值的 95% 置信区间；区间不含 0 判为显著。配对而非独立重采样，是为了消掉「题目本身难易」这一共同因子。

| strategy | 档位对比 | Δrecall | 95% CI | 显著 |
| --- | --- | --- | --- | --- |
| `S1_whole` | E1→E2 | - | - | 组合失败，跳过 |
| `S1_whole` | E1→E3 | - | - | 组合失败，跳过 |
| `S1_whole` | E2→E3 | - | - | 组合失败，跳过 |
| `S2_fixed512` | E1→E2 | - | - | 组合失败，跳过 |
| `S2_fixed512` | E1→E3 | - | - | 组合失败，跳过 |
| `S2_fixed512` | E2→E3 | - | - | 组合失败，跳过 |

### 切分策略是否真的改变了结果

主矩阵里同一列常出现多行数值相同，这不等于切分器没生效：命中判定按 `article`，切分只决定「同一法条的哪个片段排在前面」，不改变 article 集合；逐 query 的正负变化在 macro 平均里还会互相抵消。下面以每列第一个策略为基准，统计逐 query 指标真正发生变化的条数。

| embedding | 相对基准策略 | 逐 query 变化条数 | Δrecall (macro) |
| --- | --- | --- | --- |

基准策略为 `S1_whole`。`S2_fixed512` 与基准逐 query 完全一致是真实结论而非 bug：512 字窗口大于语料里绝大多数法条，阶段 1 统计里它只比整条切分多出 9 个 chunk，而这 9 条不在 80 条查询的 gold 中；`S5_context` 因注入「法律名+章节+条号」前缀，变化最明显。若要放大策略差异，需要增加长法条 / 跨款提问，而不是改指标口径。


## 6. 失败组合（不删除、不参与评选）

| 组合 | 原因 |
| --- | --- |
| `S1_whole|E2` | ProviderUnavailable: E2 需要 http://127.0.0.1:9997/v1 的本地推理服务，本机未启动；且 1024 维无法写入 law_chunk.embedding vector(1024) |
| `S1_whole|E3` | ProviderUnavailable: E3 需要 http://127.0.0.1:9997/v1 的本地推理服务，本机未启动；且 512 维无法写入 law_chunk.embedding vector(1024) |
| `S2_fixed512|E1` | ProviderUnavailable: strategy=S2_fixed512 仅 0 条有真实向量，缺 2593 条（例：advertising_law:第一条:0:S2_fixed512）；需先跑阶段 2 全量向量化 |
| `S2_fixed512|E2` | ProviderUnavailable: E2 需要 http://127.0.0.1:9997/v1 的本地推理服务，本机未启动；且 1024 维无法写入 law_chunk.embedding vector(1024) |
| `S2_fixed512|E3` | ProviderUnavailable: E3 需要 http://127.0.0.1:9997/v1 的本地推理服务，本机未启动；且 512 维无法写入 law_chunk.embedding vector(1024) |

## 7. 复现

```bash
make lab LABEL=live_check
# 等价（本机未安装 GNU make 时）
cd python-agent && uv run python ../lab/run.py --label live_check
# 仅重放报告
cd python-agent && uv run python ../lab/report.py --metrics ../lab/out/live_check/metrics.json
```

固定 seed、按 chunk_id 稳定排序、向量矩阵按语料指纹落盘缓存、组合严格串行，因此同一 label 重跑的逐 query recall 应逐位一致（由 `lab/verify.py` 断言）。
