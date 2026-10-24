"""切分结果持久化层：落 Postgres（agent 自有库 clauseguard_agent，非业务 MySQL，符合铁律 1）。

为什么放 Postgres 而非业务库：条款是审查节点的运行态输入，供 LangGraph 节点查询，属于 Agent 侧
数据；Python 写 Postgres 不违反「业务库只走 Java」红线。协议化设计便于单测用内存实现覆盖，
无需真实数据库即可达成覆盖率。
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from app.parse.models import Clause

CLAUSES_DDL = """
CREATE TABLE IF NOT EXISTS clauses (
    review_id  TEXT    NOT NULL,
    doc_id     TEXT    NOT NULL,
    clause_id  TEXT    NOT NULL,
    parent_id  TEXT,
    no         TEXT    NOT NULL DEFAULT '',
    title      TEXT    NOT NULL DEFAULT '',
    text       TEXT    NOT NULL,
    level      INTEGER NOT NULL CHECK (level BETWEEN 1 AND 4),
    page       INTEGER NOT NULL,
    start      INTEGER NOT NULL,
    "end"      INTEGER NOT NULL,
    hash       TEXT    NOT NULL,
    type       TEXT    NOT NULL,
    PRIMARY KEY (review_id, clause_id)
);
"""

_INSERT = (
    'INSERT INTO clauses (review_id, doc_id, clause_id, parent_id, no, title, text, '
    'level, page, start, "end", hash, type) '
    'VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)'
)
_DELETE = "DELETE FROM clauses WHERE review_id = %s AND doc_id = %s"
_SELECT = (
    'SELECT review_id, doc_id, clause_id, parent_id, no, title, text, level, page, '
    'start, "end", hash, type FROM clauses WHERE review_id = %s AND doc_id = %s ORDER BY start'
)


@runtime_checkable
class ClauseStore(Protocol):
    """切分结果存储契约；后续审查节点通过该接口查询条款。"""

    # pragma: no cover - 协议桩：Protocol 方法体永不直接调用，计入覆盖率分母不合理
    async def save(self, review_id: str, doc_id: str, clauses: list[Clause]) -> None: ...

    # pragma: no cover - 协议桩
    async def load(self, review_id: str, doc_id: str) -> list[Clause]: ...


class InMemoryClauseStore:
    """内存实现：单测与本地联调用，无需数据库。"""

    def __init__(self) -> None:
        self._data: dict[tuple[str, str], list[Clause]] = {}

    async def save(self, review_id: str, doc_id: str, clauses: list[Clause]) -> None:
        self._data[(review_id, doc_id)] = [c.model_copy() for c in clauses]

    async def load(self, review_id: str, doc_id: str) -> list[Clause]:
        return [c.model_copy() for c in self._data.get((review_id, doc_id), [])]


class PostgresClauseStore:
    """Postgres 实现：使用 psycopg 异步连接（agent 自有库）。"""

    def __init__(self, conn: Any) -> None:
        self._conn = conn

    async def save(self, review_id: str, doc_id: str, clauses: list[Clause]) -> None:
        rows = [
            (
                review_id,
                doc_id,
                c.id,
                c.parent_id,
                c.no,
                c.title,
                c.text,
                c.level,
                c.page,
                c.start,
                c.end,
                c.hash,
                c.type.value,
            )
            for c in clauses
        ]
        await self._conn.execute(_DELETE, (review_id, doc_id))
        if rows:
            await self._conn.executemany(_INSERT, rows)
        await self._conn.commit()

    async def load(self, review_id: str, doc_id: str) -> list[Clause]:
        cur = await self._conn.execute(_SELECT, (review_id, doc_id))
        rows = await cur.fetchall()
        return [
            Clause(
                id=r[2],
                parent_id=r[3],
                no=r[4],
                title=r[5],
                text=r[6],
                level=r[7],
                page=r[8],
                start=r[9],
                end=r[10],
                hash=r[11],
                type=r[12],
            )
            for r in rows
        ]


async def create_clauses_table(conn: Any) -> None:
    """建表（部署/迁移时调用一次）。"""
    await conn.execute(CLAUSES_DDL)
    await conn.commit()
