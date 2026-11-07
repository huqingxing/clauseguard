"""law_chunk 的 Postgres 存取层；只用 psycopg，不引 pgvector 的 Python 包。

为什么把向量写成字符串再 ::vector cast：pgvector 的 Python 适配器属于新依赖，
而 psycopg 直接绑定 list[float] 并不会自动变成 vector 类型。字符串字面量 + 显式
cast 既零依赖，又让参数化查询仍然生效（不会拼 SQL 注入）。
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from queue import Empty, Full, LifoQueue
from typing import Any

import psycopg
from psycopg.rows import dict_row

SCHEMA_FILE = Path(__file__).resolve().parent / "schema.sql"

# HNSW 内层候选池下限。实验台当前只有默认策略带向量（其余策略仅入库文本），
# 200 足够覆盖 candidate_k=50；阶段 3 若全策略向量化，需按策略命中率上调此值，
# 否则外层过滤后可能凑不满 k 条。
HNSW_MIN_CANDIDATES = 200

# 提为模块常量：验收脚本要用 EXPLAIN 包同一份 SQL，避免「验收的是另一条查询」。
# 参数顺序：(query_vec, query_vec, pool, strategy, strategy, k)。
DENSE_SEARCH_SQL = """
    WITH candidates AS MATERIALIZED (
        SELECT chunk_id, law, article, chapter, text, strategy,
               embedding <=> %s::vector AS distance
        FROM law_chunk
        WHERE embedding IS NOT NULL
        ORDER BY embedding <=> %s::vector
        LIMIT %s
    )
    SELECT chunk_id, law, article, chapter, text, strategy,
           1 - distance AS dense_score
    FROM candidates
    -- 显式 ::text：psycopg 绑定 Python None 时 PG 无法确定参数类型，
    -- 会抛 IndeterminateDatatype（内存假 store 的单测覆盖不到这层）。
    WHERE (%s::text IS NULL OR strategy = %s::text)
    ORDER BY distance
    LIMIT %s
"""


@dataclass(frozen=True, slots=True)
class ChunkRow:
    chunk_id: str
    law: str
    article: str
    chapter: str
    text: str
    strategy: str
    embedding: Sequence[float] | None = None
    meta: dict[str, object] | None = None


@dataclass(frozen=True, slots=True)
class ScoredRow:
    """dense 召回的一行；dense_score 已转成余弦相似度（越大越像）。"""

    row: ChunkRow
    dense_score: float


def vector_literal(vector: Sequence[float]) -> str:
    """把浮点向量序列化成 pgvector 接受的字面量；长度由建表维度约束把关。"""
    if not vector:
        raise ValueError("空向量不能写入 embedding")
    return "[" + ",".join(repr(float(value)) for value in vector) + "]"


class ChunkStore:
    """同步连接：FastAPI 端点声明为 def，由框架丢进线程池，避免 async 里跑阻塞 IO。"""

    def __init__(self, dsn: str, *, schema_dim: int = 1024, pool_size: int = 8) -> None:
        self.dsn = dsn
        self.schema_dim = schema_dim
        self._pool: LifoQueue[psycopg.Connection[dict[str, Any]]] = LifoQueue(maxsize=pool_size)

    @contextmanager
    def connect(self) -> Iterator[psycopg.Connection[dict[str, Any]]]:
        """从进程内小池借连接，用完归还而不是关闭。

        为什么自己写池：本机实测 psycopg 新建一条 PG 连接 P50=125.84ms，而
        dense_search 的 SQL 本身仅 3.52ms；每次检索都建连会把 P50 推到 155ms，
        逼近 200ms 门槛，并发时还会放大。用标准库 LifoQueue 即可满足线程安全的
        借还，不必引入 psycopg_pool 这类框架级新依赖（铁律禁止新增）。
        池空就直接新建、池满就直接关闭，因此高并发下不会阻塞等待。

        为什么不用 `with conn`：psycopg3 与 psycopg2 不同，Connection.__exit__
        会**关闭连接**（实测退出后 conn.closed=True）。原来的 `with self.connect()`
        因此每次都是「新建 + 关闭」，连接池会永远为空。这里显式 commit/rollback
        保留连接，只把事务边界管好。
        """
        conn = self._acquire()
        try:
            yield conn
            conn.commit()
        except BaseException:
            # 回滚失败说明连接已损坏，直接丢弃而不是归还给池。
            try:
                conn.rollback()
            except psycopg.Error:
                conn.close()
            raise
        finally:
            self._release(conn)

    def _acquire(self) -> psycopg.Connection[dict[str, Any]]:
        while True:
            try:
                conn = self._pool.get_nowait()
            except Empty:
                return psycopg.connect(self.dsn, row_factory=dict_row)
            if conn.closed:
                continue
            # 池里的连接可能已被服务端超时断开；借出前探活，避免把坏连接交给业务。
            try:
                conn.execute("SELECT 1")
            except psycopg.Error:
                conn.close()
                continue
            return conn

    def _release(self, conn: psycopg.Connection[dict[str, Any]]) -> None:
        if conn.closed:
            return
        try:
            self._pool.put_nowait(conn)
        except Full:
            conn.close()

    def close(self) -> None:
        """显式排空连接池；应用退出时调用，避免遗留到服务端的空闲会话。"""
        while True:
            try:
                self._pool.get_nowait().close()
            except Empty:
                return

    def apply_schema(self) -> None:
        ddl = SCHEMA_FILE.read_text(encoding="utf-8")
        # 任务书给的 DDL 固定 1024 维；E3(512) 等档位必须跟着改列类型，否则写入即报错。
        # 只替换向量列这一处，不会误伤索引定义。
        if self.schema_dim != 1024:
            ddl = ddl.replace("vector(1024)", f"vector({self.schema_dim})")
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute(ddl)

    def upsert(self, rows: Sequence[ChunkRow]) -> int:
        """批量幂等写入；chunk_id 是主键，重跑索引不会制造重复行。"""
        if not rows:
            return 0
        sql = """
            INSERT INTO law_chunk
                (chunk_id, law, article, chapter, text, strategy, embedding, meta)
            VALUES (%s, %s, %s, %s, %s, %s, %s::vector, %s::jsonb)
            ON CONFLICT (chunk_id) DO UPDATE SET
                law = EXCLUDED.law,
                article = EXCLUDED.article,
                chapter = EXCLUDED.chapter,
                text = EXCLUDED.text,
                strategy = EXCLUDED.strategy,
                -- 只有默认索引策略会带向量；重跑时传 NULL 不应抹掉已算好的嵌入，
                -- 否则每次重建索引都要重新调用收费的 embedding 接口。
                embedding = COALESCE(EXCLUDED.embedding, law_chunk.embedding),
                meta = EXCLUDED.meta
        """
        payload = [
            (
                row.chunk_id,
                row.law,
                row.article,
                row.chapter,
                row.text,
                row.strategy,
                vector_literal(row.embedding) if row.embedding is not None else None,
                json.dumps(row.meta or {}, ensure_ascii=False),
            )
            for row in rows
        ]
        with self.connect() as conn, conn.cursor() as cur:
            cur.executemany(sql, payload)
        return len(payload)

    def count(self, strategy: str | None = None) -> int:
        sql = "SELECT count(*) AS n FROM law_chunk"
        params: tuple[str, ...] = ()
        if strategy is not None:
            sql += " WHERE strategy = %s"
            params = (strategy,)
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute(sql, params)
            found = cur.fetchone()
            return int(found["n"]) if found else 0

    def count_by_strategy(self) -> dict[str, int]:
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT strategy, count(*) AS n FROM law_chunk "
                "GROUP BY strategy ORDER BY strategy"
            )
            return {str(row["strategy"]): int(row["n"]) for row in cur.fetchall()}

    def dense_search(
        self, query_embedding: Sequence[float], k: int, strategy: str | None = None
    ) -> list[ScoredRow]:
        """HNSW 余弦近邻；1 - 距离 = 相似度，供融合与返回字段共用同一口径。

        为什么用 MATERIALIZED CTE 把「取近邻」和「过滤 strategy」拆成两层：
        strategy 谓词与 ORDER BY distance 同层时，规划器选 law_chunk_strategy_idx
        （B-tree）+ Sort，对约 500 命中行全量算 1024 维距离，HNSW 被完全绕过
        （EXPLAIN 实测 19.7ms，且随数据量线性劣化）。物化内层后内层无 strategy
        谓词可下推，HNSW 成为唯一可行路径，策略过滤留到外层，语义等价而索引生效。

        为什么用 hnsw.iterative_scan 而不是调大 hnsw.ef_search：HNSW 默认搜索
        宽度 40，内层 LIMIT 200 时也只吐 40 个候选，外层过滤后凑不满 k 条。但实测
        把 ef_search 抬到 200 会同时抬高 HNSW 的 cost 估算，规划器反而改走
        Seq Scan + Sort（24.7ms），索引依旧不生效。iterative_scan 是 pgvector 0.8
        为「带过滤 / 需要更多候选」提供的官方能力：让索引扫描持续迭代补候选，
        不抬高 cost；strict_order 保证结果仍严格按距离有序，不会像 relaxed_order
        那样牺牲 top-k 精度。事务级 set_config(is_local=true) 避免污染其他会话。
        """
        params = self._dense_params(vector_literal(query_embedding), k, strategy)
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT set_config('hnsw.iterative_scan', %s, true)", ("strict_order",))
            cur.execute(DENSE_SEARCH_SQL, params)
            return [
                ScoredRow(
                    row=ChunkRow(
                        chunk_id=row["chunk_id"],
                        law=row["law"],
                        article=row["article"],
                        chapter=row["chapter"],
                        text=row["text"],
                        strategy=row["strategy"],
                    ),
                    dense_score=float(row["dense_score"]),
                )
                for row in cur.fetchall()
            ]

    @staticmethod
    def _dense_params(
        literal: str, k: int, strategy: str | None
    ) -> tuple[str, str, int, str | None, str | None, int]:
        # service 传入的 k 是融合候选数（默认 50）；策略过滤会削掉一部分候选，
        # 故内层取 4 倍且不低于 200，给外层留足余量。
        pool = max(k * 4, HNSW_MIN_CANDIDATES)
        return (literal, literal, pool, strategy, strategy, k)

    def dense_search_plan(
        self, query_embedding: Sequence[float], k: int, strategy: str | None = None
    ) -> list[str]:
        """返回 dense_search 实际那条 SQL 的 EXPLAIN 计划。

        为什么需要它：CREATE INDEX 成功不等于查询用得上（本次就抓到规划器绕过
        HNSW 的问题）。验收脚本据此断言计划里出现 law_chunk_embedding_hnsw，
        把「索引有效」变成可执行、会失败的检查，而不是一句口头结论。
        """
        params = self._dense_params(vector_literal(query_embedding), k, strategy)
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT set_config('hnsw.iterative_scan', %s, true)", ("strict_order",))
            cur.execute("EXPLAIN (ANALYZE) " + DENSE_SEARCH_SQL, params)
            return [line["QUERY PLAN"] for line in cur.fetchall()]

    def load_all(self, strategy: str | None = None) -> list[ChunkRow]:
        """BM25 启动时一次性从 PG 载入；不复用 dense 召回结果，避免索引被召回集截断。"""
        sql = "SELECT chunk_id, law, article, chapter, text, strategy FROM law_chunk"
        params: tuple[str, ...] = ()
        if strategy is not None:
            sql += " WHERE strategy = %s"
            params = (strategy,)
        sql += " ORDER BY chunk_id"
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute(sql, params)
            return [
                ChunkRow(
                    chunk_id=row["chunk_id"],
                    law=row["law"],
                    article=row["article"],
                    chapter=row["chapter"],
                    text=row["text"],
                    strategy=row["strategy"],
                )
                for row in cur.fetchall()
            ]

    def fetch_by_ids(self, chunk_ids: Sequence[str]) -> dict[str, ChunkRow]:
        if not chunk_ids:
            return {}
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT chunk_id, law, article, chapter, text, strategy "
                "FROM law_chunk WHERE chunk_id = ANY(%s)",
                (list(chunk_ids),),
            )
            return {
                row["chunk_id"]: ChunkRow(
                    chunk_id=row["chunk_id"],
                    law=row["law"],
                    article=row["article"],
                    chapter=row["chapter"],
                    text=row["text"],
                    strategy=row["strategy"],
                )
                for row in cur.fetchall()
            }
