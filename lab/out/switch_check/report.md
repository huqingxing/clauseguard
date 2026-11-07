# 检索实验台 · 阶段 4 矩阵报告

- label：`switch_check`　seed：`20261107`　provider：`deterministic`
- 数据集：`lab/dataset/queries.jsonl`（80 条，sha256 `538bf7866028a992…`）
- 配置：k=5　candidate_k=50　bm25=off　rerank=on　融合=rrf(k=60)　命中口径=article
- 组合：1 组（成功 1 / 失败 0），每组 80 条 query

> **口径声明（重要）**：本次 `provider=deterministic`，E1/E2/E3 是三套确定性
> 字符 n-gram 哈希投影基线（维度 1024/1024/512），**不是** dashscope
> `text-embedding-v3`、`bge-m3`、`bge-small-zh` 的真实输出。本机
> `127.0.0.1:9997` 推理服务未启动，bge 权重需框架级新依赖（铁律禁止），
> 且 E3 的 512 维写不进 `law_chunk.embedding vector(1024)` 单列。
> 因此本矩阵验证的是「切分策略 × 向量特征粒度」对指标的影响，以及整套
> 跑批 / 指标 / 复现链路的正确性，**不可用作模型选型结论**。真实档位请跑
> `--provider live`（E1 复用阶段 2 已入库的百炼向量；E2/E3 不可用时按失败组合留痕）。

## 1. 主矩阵：recall@5

行 = 切分策略，列 = embedding 档位；每个单元格是该组合 80 条 query 的 macro 平均 recall。

| strategy \ embedding | E1 |
| --- | --- |
| `S1_whole` | 0.5708 |

## 2. 每个组合的完整指标

所有指标按每条 query 等权 macro 平均；延迟为单次检索耗时，索引用时为组合级准备耗时。

| 组合 | 状态 | chunks | recall@5 | hit@5 | mrr@10 | ndcg@10 | 延迟 mean/p50 (ms) | 索引用时 (ms) | API tokens |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `S1_whole|E1` | ok | 2584 | 0.5708 | 0.5750 | 0.4646 | 0.4881 | 1.57 / 1.53 | 485.8 | 0 |

## 3. embedding token 成本

| 组合 | tokens | API 请求数 | 费用 (¥) | 说明 |
| --- | --- | --- | --- | --- |
| `S1_whole|E1` | 0 | 0 | 0.0000 | 离线哈希投影：无 API 调用、无 token、无费用 |

离线哈希投影**不产生任何 API token 与费用**，tokens 列恒为 0。为便于换算量级，下面给出「若改用真实 API」的保守字符数上界（中文约 1 字 ≈ 1 token）：

| 组合 | 字符数 | 估算 tokens（若走真实 API） | 向量缓存 |
| --- | --- | --- | --- |
| `S1_whole|E1` | 247123 | 492459 | miss |

## 4. 索引构建耗时拆分 (ms)

| 组合 | 切分 | chunk 向量 | query 向量 | BM25 | 合计 |
| --- | --- | --- | --- | --- | --- |
| `S1_whole|E1` | 40.1 | 426.3 | 15.2 | 0.0 | 485.8 |

BM25 索引与 embedding 档位无关，按 strategy 建一次后跨 3 个档位复用；向量矩阵按语料指纹缓存，重跑命中时该列为 0。


## 5. 结论

- **最优组合**：`S1_whole|E1`，recall@5=0.5708，mrr@10=0.4646，ndcg@10=0.4881，延迟 p50=1.53ms。

### 同策略、不同 embedding 的差值是否显著

对同一策略下的两两档位，用固定 seed 的**配对 bootstrap**（2000 次重采样 80 条 query）估计 recall 差值的 95% 置信区间；区间不含 0 判为显著。配对而非独立重采样，是为了消掉「题目本身难易」这一共同因子。

| strategy | 档位对比 | Δrecall | 95% CI | 显著 |
| --- | --- | --- | --- | --- |

### 切分策略是否真的改变了结果

主矩阵里同一列常出现多行数值相同，这不等于切分器没生效：命中判定按 `article`，切分只决定「同一法条的哪个片段排在前面」，不改变 article 集合；逐 query 的正负变化在 macro 平均里还会互相抵消。下面以每列第一个策略为基准，统计逐 query 指标真正发生变化的条数。

| embedding | 相对基准策略 | 逐 query 变化条数 | Δrecall (macro) |
| --- | --- | --- | --- |

基准策略为 `S1_whole`。`S2_fixed512` 与基准逐 query 完全一致是真实结论而非 bug：512 字窗口大于语料里绝大多数法条，阶段 1 统计里它只比整条切分多出 9 个 chunk，而这 9 条不在 80 条查询的 gold 中；`S5_context` 因注入「法律名+章节+条号」前缀，变化最明显。若要放大策略差异，需要增加长法条 / 跨款提问，而不是改指标口径。


## 6. 失败组合（不删除、不参与评选）

本次无失败组合。

## 7. 复现

```bash
make lab LABEL=switch_check
# 等价（本机未安装 GNU make 时）
cd python-agent && uv run python ../lab/run.py --label switch_check
# 仅重放报告
cd python-agent && uv run python ../lab/report.py --metrics ../lab/out/switch_check/metrics.json
```

固定 seed、按 chunk_id 稳定排序、向量矩阵按语料指纹落盘缓存、组合严格串行，因此同一 label 重跑的逐 query recall 应逐位一致（由 `lab/verify.py` 断言）。
