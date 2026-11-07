# ruff: noqa: T201, E402
"""阶段 4 矩阵跑批：一条命令跑完 5×3 组合，产出 report.md 与 metrics.json。

## 可复现性的四个来源（缺一不可）

1. 固定 seed（--seed），report 里的配对 bootstrap 也用同一个 seed；
2. 语料/chunk/query 全部按稳定键排序，排序一律用 stable argsort；
3. 向量矩阵按「语料指纹 + 档位指纹 + 实现版本」落盘缓存，重跑不重算；
4. 组合严格串行、无并发、无随机采样，且检索传入预计算的 query 向量，
   因此任何一次运行都不依赖网络抖动或服务端状态。

失败组合不会被跳过也不会被删：捕获异常后写一条 status=failed 的完整记录，
report 里照样占一格。这样「E2/E3 档位不可用」是可见的实验事实，而不是被
悄悄过滤掉后一张看起来很漂亮的矩阵表。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from lab import index as ix  # noqa: E402
from lab import providers as pv  # noqa: E402
from lab import report as rp  # noqa: E402
from lab.metrics import Case, summarize  # noqa: E402

DATASET = _REPO / "lab" / "dataset" / "queries.jsonl"
DEFAULT_STRATEGIES = ",".join(ix.VALID_STRATEGIES)
DEFAULT_EMBEDDINGS = "E1,E2,E3"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="检索实验台矩阵跑批")
    parser.add_argument("--label", default="v1")
    parser.add_argument("--strategies", default=DEFAULT_STRATEGIES)
    parser.add_argument("--embeddings", default=DEFAULT_EMBEDDINGS)
    parser.add_argument("--rerank", choices=("on", "off"), default="off")
    parser.add_argument("--bm25", choices=("on", "off"), default="on")
    parser.add_argument("--k", type=int, default=5)
    parser.add_argument("--candidate-k", type=int, default=50)
    parser.add_argument("--out", default="")
    parser.add_argument("--provider", choices=("deterministic", "live"), default="deterministic")
    parser.add_argument("--pg-dsn", default=pv.DEFAULT_PG_DSN)
    parser.add_argument("--seed", type=int, default=pv.DEFAULT_SEED)
    return parser.parse_args(argv)


def _csv(value: str) -> list[str]:
    items = [item.strip() for item in value.split(",") if item.strip()]
    if not items:
        raise ValueError("--strategies / --embeddings 不能为空")
    return items


def load_queries(path: Path) -> list[dict[str, Any]]:
    rows = [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    # 按 qid 排序而不是信任文件顺序：重排数据集不该改变逐 query 明细的顺序，
    # 否则两次运行的 per_query 数组虽内容相同却无法逐位比对。
    return sorted(rows, key=lambda row: str(row["qid"]))


def _normalize(matrix: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    return matrix / np.where(norms > 0, norms, 1.0)


class MatrixContext:
    """持有跨组合复用的 chunk / BM25 / query 向量，避免 15 组重复构建。"""

    def __init__(self, args: argparse.Namespace, out: Path, queries: list[dict[str, Any]]) -> None:
        self.args = args
        self.queries = queries
        self.cache = pv.VectorCache(out / "cache")
        self.chunks: dict[str, list[Any]] = {}
        self.rows: dict[str, list[Any]] = {}
        self.bm25: dict[str, Any] = {}
        self._query_vectors: dict[str, np.ndarray] = {}

    def prepare_strategy(self, strategy: str) -> None:
        if strategy in self.chunks:
            return
        chunks = ix.build_chunks(strategy)
        self.chunks[strategy] = chunks
        self.rows[strategy] = ix.to_rows(chunks)

    def chunk_matrix(
        self, strategy: str, spec: pv.ProviderSpec, vectorizer: Any
    ) -> tuple[np.ndarray, bool, float]:
        chunks = self.chunks[strategy]
        fingerprint = pv.VectorCache.corpus_fingerprint(
            [chunk.chunk_id for chunk in chunks], [chunk.text for chunk in chunks]
        )
        path = self.cache.path_for(f"chunks__{strategy}", spec, fingerprint, len(chunks))
        started = time.perf_counter()
        cached = self.cache.load(path, len(chunks), spec.dim)
        if cached is not None:
            # 落盘的就是归一化后的矩阵；再归一化一次会引入 1e-7 级浮点漂移，
            # 足以让并列分数的 argsort 换序，破坏「重跑 recall 差异 = 0」。
            return cached, True, (time.perf_counter() - started) * 1000
        if spec.mode == "live":
            matrix = vectorizer.chunk_matrix(strategy, chunks)
        else:
            matrix = vectorizer.embed([chunk.text for chunk in chunks])
        matrix = _normalize(np.asarray(matrix, dtype=np.float32))
        self.cache.save(
            path, matrix, {"strategy": strategy, "embedding": spec.code, "mode": spec.mode}
        )
        return matrix, False, (time.perf_counter() - started) * 1000

    def query_matrix(self, spec: pv.ProviderSpec, vectorizer: Any) -> np.ndarray:
        if spec.code in self._query_vectors:
            return self._query_vectors[spec.code]
        texts = [str(row["query"]) for row in self.queries]
        fingerprint = hashlib.sha256("\n".join(texts).encode("utf-8")).hexdigest()[:16]
        path = self.cache.path_for("query", spec, fingerprint, len(texts))
        cached = self.cache.load(path, len(texts), spec.dim)
        if cached is not None:
            matrix = cached
        elif spec.mode == "live":
            matrix = _normalize(np.asarray(vectorizer.query_matrix(texts), dtype=np.float32))
        else:
            matrix = _normalize(np.asarray(vectorizer.embed(texts), dtype=np.float32))
        if not path.exists():
            self.cache.save(
                path, matrix, {"purpose": "query", "embedding": spec.code, "mode": spec.mode}
            )
        self._query_vectors[spec.code] = matrix
        return matrix

    def bm25_for(self, strategy: str) -> tuple[Any, float]:
        if self.args.bm25 != "on":
            return None, 0.0
        if strategy in self.bm25:
            return self.bm25[strategy], 0.0
        started = time.perf_counter()
        built = ix.build_bm25(self.rows[strategy])
        self.bm25[strategy] = built
        return built, (time.perf_counter() - started) * 1000

    def run_combination(self, strategy: str, code: str) -> dict[str, Any]:
        spec = pv.spec_for(code, self.args.provider)
        vectorizer = pv.make_vectorizer(spec, self.cache, self.args.pg_dsn)
        index_started = time.perf_counter()
        breakdown: dict[str, float] = {}

        t0 = time.perf_counter()
        self.prepare_strategy(strategy)
        breakdown["chunk_ms"] = (time.perf_counter() - t0) * 1000

        matrix, cache_hit, vectors_ms = self.chunk_matrix(strategy, spec, vectorizer)
        breakdown["vector_ms"] = vectors_ms
        t0 = time.perf_counter()
        query_vectors = self.query_matrix(spec, vectorizer)
        breakdown["query_vector_ms"] = (time.perf_counter() - t0) * 1000
        bm25, bm25_ms = self.bm25_for(strategy)
        breakdown["bm25_ms"] = bm25_ms

        store = ix.MatrixStore(self.rows[strategy], matrix)
        service = ix.make_service(
            store,
            strategy=strategy,
            embedding=code,
            use_bm25=self.args.bm25 == "on",
            candidate_k=self.args.candidate_k,
            bm25=bm25,
        )
        index_ms = (time.perf_counter() - index_started) * 1000

        cases: list[Case] = []
        for position, query in enumerate(self.queries):
            started = time.perf_counter()
            hits = ix.retrieve(
                service,
                query=str(query["query"]),
                query_vector=query_vectors[position],
                k=self.args.k,
                strategy=strategy,
                use_bm25=self.args.bm25 == "on",
                use_rerank=self.args.rerank == "on",
                candidate_k=self.args.candidate_k,
            )
            elapsed_ms = (time.perf_counter() - started) * 1000
            cases.append(
                Case(
                    qid=str(query["qid"]),
                    retrieved=hits,
                    gold=list(query["gold"]),
                    scene=str(query["scene"]),
                    qtype=str(query["type"]),
                    latency_ms=elapsed_ms,
                )
            )

        usage = vectorizer.usage
        usage.cache_hit = cache_hit
        return {
            "key": f"{strategy}|{code}",
            "strategy": strategy,
            "embedding": code,
            "status": "ok",
            "error": None,
            "n_queries": len(cases),
            "n_chunks": len(self.rows[strategy]),
            "index_ms": round(index_ms, 3),
            "index_breakdown": {key: round(value, 3) for key, value in breakdown.items()},
            "cache_hit": cache_hit,
            "embedding_usage": usage.as_dict(),
            "provider": spec.as_dict(),
            "summary": summarize(cases, k=self.args.k, index_ms=index_ms),
        }


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    random.seed(args.seed)
    np.random.seed(args.seed)

    strategies, embeddings = _csv(args.strategies), _csv(args.embeddings)
    for strategy in strategies:
        if strategy not in ix.VALID_STRATEGIES:
            raise ValueError(f"未知切分策略: {strategy}（可选 {ix.VALID_STRATEGIES}）")
    for code in embeddings:
        pv.spec_for(code, args.provider)  # 提前 fail-fast，别等跑到第 10 组才报错

    out = Path(args.out) if args.out else _REPO / "lab" / "out" / args.label
    out.mkdir(parents=True, exist_ok=True)
    queries = load_queries(DATASET)
    ctx = MatrixContext(args, out, queries)
    combinations: list[dict[str, Any]] = []

    print(
        f"label={args.label} provider={args.provider} seed={args.seed} "
        f"strategies={len(strategies)} embeddings={len(embeddings)} "
        f"k={args.k} bm25={args.bm25} rerank={args.rerank} queries={len(queries)}"
    )
    for strategy in strategies:
        for code in embeddings:
            key = f"{strategy}|{code}"
            try:
                record = ctx.run_combination(strategy, code)
                overall = record["summary"]["overall"]
                print(
                    f"  [{key:24s}] chunks={record['n_chunks']:5d} "
                    f"index={record['index_ms']:8.1f}ms "
                    f"cache={'hit' if record['cache_hit'] else 'miss':4s} "
                    f"recall@{args.k}={overall['recall']:.4f} mrr@10={overall['mrr']:.4f} "
                    f"ndcg@10={overall['ndcg']:.4f} p50={overall['latency_ms_p50']:6.2f}ms "
                    f"tokens={record['embedding_usage']['tokens']} status=ok"
                )
            except Exception as exc:  # noqa: BLE001 - 失败组合必须留痕，不能中断矩阵
                record = {
                    "key": key,
                    "strategy": strategy,
                    "embedding": code,
                    "status": "failed",
                    "error": f"{type(exc).__name__}: {exc}",
                    "n_queries": 0,
                    "n_chunks": 0,
                    "index_ms": 0.0,
                    "index_breakdown": {},
                    "cache_hit": False,
                    "embedding_usage": {},
                    "provider": {},
                    "summary": summarize([], k=args.k),
                }
                print(f"  [{key:24s}] status=failed | {record['error']}")
            combinations.append(record)

    metrics: dict[str, Any] = {
        "label": args.label,
        "seed": args.seed,
        "generated_by": "lab.run",
        "provider_mode": args.provider,
        "dataset": {
            "path": str(DATASET.relative_to(_REPO)).replace("\\", "/"),
            "sha256": hashlib.sha256(DATASET.read_bytes()).hexdigest(),
            "n": len(queries),
        },
        "config": {
            "strategies": strategies,
            "embeddings": embeddings,
            "rerank": args.rerank,
            "bm25": args.bm25,
            "k": args.k,
            "candidate_k": args.candidate_k,
            "mrr_k": 10,
            "ndcg_k": 10,
            "match_mode": "article",
            "fusion": "rrf(k=60)",
            "pg_dsn": args.pg_dsn if args.provider == "live" else "",
        },
        "combinations": combinations,
    }
    metrics_path = out / "metrics.json"
    metrics_path.write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    rp.generate(metrics, out)

    ok = [item for item in combinations if item["status"] == "ok"]
    failed = [item for item in combinations if item["status"] == "failed"]
    print(
        f"\n组合 {len(combinations)} 组（成功 {len(ok)} / 失败 {len(failed)}），"
        f"每组已跑 {len(queries)} 条 query"
    )
    print(f"metrics.json -> {metrics_path}")
    print(f"report.md    -> {out / 'report.md'}")
    if failed:
        for item in failed:
            print(f"  failed: {item['key']} | {item['error']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
