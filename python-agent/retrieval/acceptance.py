"""阶段 2 可执行验收：建表 → 全量入库 → 真实向量化 → 三模式对比 → P50 → 端点。

用法（DSN 必须显式给，防止误连业务库/用户正在使用的实例）：
    CLAUSEGUARD_RETRIEVAL_PG_DSN='postgresql://clauseguard@127.0.0.1:55432/clauseguard_retrieval' \
        python -m retrieval.acceptance

为什么只对默认策略做 embedding：五种切分策略是同一批法条的五种视图，全部向量化
会让调用量翻五倍且 dense 候选池混入重复语义；实验台按 strategy 隔离检索，只有
被选中的策略需要向量。其余策略仍全量入库，保证 count 与阶段 1 统计可逐项对账。
"""

# 验收输出即交付物（要贴进报告），CLI 打印是意图而非疏漏；全局 T20 规则在此豁免。
# ruff: noqa: T201

from __future__ import annotations

import json
import os
import re
import statistics
import sys
import time
from dataclasses import asdict

import psycopg
from fastapi.testclient import TestClient

from app.main import create_app
from retrieval import api
from retrieval.chunker import ALL_STRATEGIES, Strategy, all_strategies
from retrieval.config import get_retrieval_settings
from retrieval.corpus.loader import load_corpus
from retrieval.embedder import Embedder
from retrieval.service import Hit, RetrievalService
from retrieval.store import ChunkRow, ChunkStore, vector_literal

QUERY = "当事人约定的违约金过分高于造成的损失，可以请求人民法院予以适当减少吗"
PROBE_ROUNDS = 40
BATCH_UPSERT = 500


def _print_table(title: str, headers: tuple[str, ...], rows: list[list[object]]) -> None:
    print(f"\n===== {title} =====")
    widths = [
        max([len(str(header)), *(len(str(row[i])) for row in rows)])
        for i, header in enumerate(headers)
    ]
    print("  ".join(str(h).ljust(widths[i]) for i, h in enumerate(headers)))
    print("  ".join("-" * width for width in widths))
    for row in rows:
        print("  ".join(str(value).ljust(widths[i]) for i, value in enumerate(row)))


def main() -> int:
    settings = get_retrieval_settings()
    dsn = os.environ.get("CLAUSEGUARD_RETRIEVAL_PG_DSN", "")
    if not dsn:
        print("FATAL: 必须显式设置 CLAUSEGUARD_RETRIEVAL_PG_DSN（防止误连业务库）")
        return 2
    if "localhost:5432" in dsn or "127.0.0.1:5432" in dsn:
        print("FATAL: 拒绝指向 5432 默认实例；验收库必须是独立的 pgvector 实例")
        return 2

    model, dim, _base_url = settings.resolved_embedding()
    print("===== 配置（密钥不回显）=====")
    print(
        json.dumps(
            {
                "dsn_host_port": dsn.split("@")[-1],
                "embedding_backend": settings.embedding_backend,
                "model": model,
                "dim": dim,
                "batch_size": settings.embedding_batch_size,
                "provider_batch_limit": settings.provider_batch_limit,
                "fusion_method": settings.fusion_method,
                "rrf_k": settings.rrf_k,
                "dense_weight": settings.dense_weight,
                "sparse_weight": settings.sparse_weight,
                "candidate_k": settings.candidate_k,
                "key_present": bool(settings.embedding_api_key),
            },
            ensure_ascii=False,
            indent=2,
        )
    )

    store = ChunkStore(dsn, schema_dim=dim)
    store.apply_schema()
    print("\n[1] schema.sql 执行完成（CREATE EXTENSION / law_chunk / HNSW）")

    entries = load_corpus()
    chunks_by_strategy = all_strategies(entries)
    total_chunks = sum(len(chunks) for chunks in chunks_by_strategy.values())
    print(
        f"[2] 语料 {len(entries)} 条 / {len({e['law'] for e in entries})} 部法律；"
        f"5 策略切分合计 {total_chunks} 个 chunk"
    )

    # 先把所有策略的 chunk 文本入库（不带向量），重跑时 COALESCE 保留已有 embedding。
    written = 0
    for chunks in chunks_by_strategy.values():
        rows = [
            ChunkRow(
                chunk.chunk_id,
                chunk.law,
                chunk.article,
                chunk.chapter,
                chunk.text,
                chunk.strategy.value,
            )
            for chunk in chunks
        ]
        for start in range(0, len(rows), BATCH_UPSERT):
            written += store.upsert(rows[start : start + BATCH_UPSERT])
    print(f"[3] 全量写入 {written} 行（文本/元数据，未覆盖已有向量）")

    default_strategy = Strategy(settings.index_strategy)
    # 阶段 6：YAML 的 strategy 可切换，但向量列只允许生效策略持有。dense_search 的
    # 内层 CTE 以 embedding IS NOT NULL 取近邻，若两套策略同时有向量，近邻池会混入
    # 同一法条的另一种视图（语义重复、挤掉真候选）；更严重的是规划器在 5168 行上
    # 改选 Seq Scan + Sort，HNSW 直接失效（实测 Execution Time 51.9ms vs 5.1ms）。
    # 这里显式清空非生效策略的向量，把「配置切换」变成可重复的验收动作而不是手工清库。
    with store.connect() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE law_chunk SET embedding = NULL WHERE strategy <> %s AND embedding IS NOT NULL",
            (default_strategy.value,),
        )
        cleared = cur.rowcount
        cur.execute("ANALYZE law_chunk")
    if cleared:
        print(f"[3b] 清理非生效策略的旧向量 {cleared} 行（避免近邻池重复 + HNSW 失效）")
    with store.connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) AS n FROM law_chunk WHERE strategy = %s AND embedding IS NOT NULL",
            (default_strategy.value,),
        )
        count_row = cur.fetchone()
        already = int(count_row["n"]) if count_row else 0
    need = len(chunks_by_strategy[default_strategy])
    print(f"[4] 默认策略 {default_strategy.value}: 需向量 {need} 条，已有 {already} 条")

    usage_summary: dict[str, object] = {"reused": already, "computed": 0, "total_tokens": 0}
    if already < need:
        with store.connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT chunk_id, text FROM law_chunk "
                "WHERE strategy = %s AND embedding IS NULL ORDER BY chunk_id",
                (default_strategy.value,),
            )
            pending = cur.fetchall()
        texts = [row["text"] for row in pending]
        print(f"[5] 调用真实 embedding 接口向量化 {len(texts)} 条（{model}, dim={dim}）...")
        started = time.perf_counter()
        with Embedder(settings) as embedder:
            result = embedder.embed(texts)
        elapsed = time.perf_counter() - started
        vectors = [
            ChunkRow(
                chunk_id=row["chunk_id"],
                law="",
                article="",
                chapter="",
                text="",
                strategy=default_strategy.value,
                embedding=vector,
            )
            for row, vector in zip(pending, result.vectors, strict=True)
        ]
        # 只更新向量列：用带向量的 upsert 会覆盖文本为空串，故走专用 UPDATE。
        with store.connect() as conn, conn.cursor() as cur:
            cur.executemany(
                "UPDATE law_chunk SET embedding = %s::vector WHERE chunk_id = %s",
                [(vector_literal(row.embedding or []), row.chunk_id) for row in vectors],
            )
        usage_summary.update(
            {
                "computed": result.usage.vectors,
                "total_tokens": result.usage.total_tokens,
                "requests": result.usage.requests,
                "retries": result.usage.retries,
                "elapsed_s": round(elapsed, 2),
            }
        )
        print(
            f"    token={result.usage.total_tokens} requests={result.usage.requests} "
            f"retries={result.usage.retries} elapsed={elapsed:.1f}s"
        )
    else:
        print("[5] 向量已齐，复用（不重复调用收费接口）")
    print("    usage=" + json.dumps(usage_summary, ensure_ascii=False))

    # 批量 UPDATE embedding 会在 HNSW 图里留下死条目：实测索引从 20MB 涨到 40MB，
    # 启动代价 444→884，规划器据此改选 Seq Scan + Sort（51.9ms，HNSW 完全失效）。
    # 重建索引并刷新统计，把「切策略后索引仍有效」变成每次验收都会执行的动作。
    if cleared or usage_summary["computed"]:
        with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
            cur.execute("REINDEX INDEX law_chunk_embedding_hnsw")
            cur.execute("ANALYZE law_chunk")
        print("[5b] 重建 HNSW 索引并 ANALYZE（消除批量更新造成的索引膨胀）")

    total_db = store.count()
    by_strategy = store.count_by_strategy()
    count_rows: list[list[object]] = [
        [
            s.value,
            len(chunks_by_strategy[s]),
            by_strategy.get(s.value, 0),
            "PASS" if len(chunks_by_strategy[s]) == by_strategy.get(s.value, 0) else "FAIL",
        ]
        for s in ALL_STRATEGIES
    ]
    count_rows.append(
        ["合计", total_chunks, total_db, "PASS" if total_chunks == total_db else "FAIL"]
    )
    _print_table(
        "SELECT count(*) 与阶段 1 切分统计对账",
        ("strategy", "切分统计(阶段1)", "DB count", "一致"),
        count_rows,
    )
    with store.connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM law_chunk WHERE embedding IS NOT NULL")
        embedded_row = cur.fetchone()
        embedded = int(embedded_row["n"]) if embedded_row else 0
        cur.execute(
            "SELECT count(*) AS n FROM law_chunk WHERE strategy = %s AND embedding IS NOT NULL",
            (default_strategy.value,),
        )
        embedded_default = int(cur.fetchone()["n"])
    print(
        f"    embedding IS NOT NULL = {embedded}；其中 {default_strategy.value} = "
        f"{embedded_default}（需 {need} 条）"
    )
    assert total_db == total_chunks, "count(*) 与切分统计不一致"
    # 阶段 6 切换 YAML 的 strategy 后，旧策略的向量会因 upsert 的 COALESCE 保留，
    # 全库向量数不再等于单一策略的 chunk 数；只断言「本次策略向量齐全」，否则
    # 每换一次最优策略都得清库重跑收费的 embedding 接口。
    assert embedded_default == need, "生效策略的向量化条数不齐"

    service = RetrievalService(store=store, settings=settings)
    docs = service.warmup(default_strategy.value)
    print(f"\n[6] BM25 索引从 PG 加载 {docs} 篇文档")

    # 三组对比共用一个 query 向量：既保证可比，也让延迟测量不含外部 API 往返。
    with Embedder(settings) as embedder:
        query_vector = embedder.embed([QUERY]).vectors[0]
    print(f"    query: {QUERY}")

    # CREATE INDEX 成功不等于查询用得上。用同一份 SQL 的 EXPLAIN 做硬断言：
    # 规划器绕过 HNSW 时曾出现 strategy_idx + Sort，索引白建但 P50 仍「达标」。
    plan = store.dense_search_plan(query_vector, settings.candidate_k, default_strategy.value)
    uses_hnsw = any("law_chunk_embedding_hnsw" in line for line in plan)
    seq_scan = any("Seq Scan on law_chunk" in line for line in plan)
    btree_fallback = any("law_chunk_strategy_idx" in line for line in plan)
    print("\n[7] HNSW 索引有效性（EXPLAIN ANALYZE 同一份 dense_search SQL）")
    for line in plan:
        if any(
            key in line for key in ("Index Scan using", "Seq Scan", "CTE Scan", "Execution Time")
        ):
            # Sort Key 会把 1024 维向量原样打进计划，截断后只留结构信息。
            short = re.sub(r"'\[-?[0-9].*?\]'::vector", "'<VEC>'::vector", line)
            print("    " + short[:150])
    dense_probe = store.dense_search(query_vector, settings.candidate_k, default_strategy.value)
    print(
        f"    USES_HNSW={uses_hnsw}  SEQ_SCAN={seq_scan}  "
        f"BTREE_FALLBACK={btree_fallback}  candidates={len(dense_probe)}"
        f"/{settings.candidate_k}"
    )
    assert uses_hnsw and not seq_scan and not btree_fallback, "dense 检索未走 HNSW"
    assert len(dense_probe) == settings.candidate_k, "HNSW 候选池被 ef_search 截断"

    def run(**kwargs: object) -> tuple[list[Hit], float]:
        started = time.perf_counter()
        hits = service.search(
            QUERY, k=5, strategy=default_strategy.value, embedding=query_vector, **kwargs
        )  # type: ignore[arg-type]
        return list(hits), (time.perf_counter() - started) * 1000.0

    results: dict[str, list[Hit]] = {}
    for mode, kwargs in (
        ("dense-only", {"use_bm25": False}),
        ("bm25-only", {"use_dense": False}),
        ("hybrid", {}),
    ):
        hits, elapsed = run(**kwargs)
        results[mode] = hits
        print(f"  -- {mode} (本次 {elapsed:.1f}ms) --")
        for rank, hit in enumerate(hits, 1):
            print(
                f"    {rank}. {hit.chunk_id}  {hit.article}  "
                f"score={hit.score:.6f} dense={hit.dense_score:.4f} "
                f"sparse={hit.sparse_score:.4f}  {hit.text[:28]}..."
            )
        print(f"    json={json.dumps([asdict(h) for h in hits], ensure_ascii=False)[:400]}")

    overlap = len(
        {h.chunk_id for h in results["dense-only"]} & {h.chunk_id for h in results["bm25-only"]}
    )
    print(f"    dense∩bm25 top5 重叠数 = {overlap}（混合检索有融合价值）")

    # P50：query 向量预先算好传入，测的是「本地检索」本身（不含 rerank、不含外部 API）。
    print(f"\n[8] 延迟：每种模式各 {PROBE_ROUNDS} 次（预置 query embedding，本地检索）")
    latency_rows: list[list[object]] = []
    for mode, kwargs in (
        ("dense-only", {"use_bm25": False}),
        ("bm25-only", {"use_dense": False}),
        ("hybrid", {}),
    ):
        samples = [run(**kwargs)[1] for _ in range(PROBE_ROUNDS)]
        p50 = statistics.median(samples)
        latency_rows.append(
            [
                mode,
                round(min(samples), 2),
                round(p50, 2),
                round(max(samples), 2),
                "PASS" if p50 < 200 else "FAIL",
            ]
        )
    _print_table(
        "延迟 P50 < 200ms", ("模式", "min(ms)", "P50(ms)", "max(ms)", "结论"), latency_rows
    )
    assert all(row[-1] == "PASS" for row in latency_rows), "P50 未达标"

    # 端点真实调用（TestClient 走完整 ASGI 路由，注入真库 service）
    api.set_service(service)
    try:
        client = TestClient(create_app())
        resp = client.post(
            "/v1/retrieval/search",
            json={
                "query": QUERY,
                "k": 3,
                "strategy": default_strategy.value,
                "embedding": query_vector,
            },
        )
        print("\n[9] POST /v1/retrieval/search 真实响应")
        print(f"    HTTP {resp.status_code}")
        body = resp.json()
        print(f"    elapsed_ms={body['elapsed_ms']}  hits={len(body['hits'])}")
        print(f"    config={json.dumps(body['config'], ensure_ascii=False)}")
        print(f"    top1={json.dumps(body['hits'][0], ensure_ascii=False)[:300]}")
        assert resp.status_code == 200 and body["hits"]
        assert "embedding_api_key" not in json.dumps(body)
    finally:
        api.set_service(None)

    print("\n===== 验收结论：全部 PASS =====")
    return 0


if __name__ == "__main__":
    sys.exit(main())
