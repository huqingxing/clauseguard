"""repository 层测试：内存实现往返 + Postgres 实现（用假连接，无需真实库）。"""

from __future__ import annotations

from pathlib import Path

from app.parse import parse_contract
from app.parse.models import Clause
from app.parse.repository import (
    CLAUSES_DDL,
    InMemoryClauseStore,
    PostgresClauseStore,
    create_clauses_table,
)
from tests.parse.fixtures import pdf_standard


class FakeAsyncConn:
    """最小异步连接替身：记录执行的 SQL/参数，load 时返回预设行。"""

    def __init__(self, fetch_rows: list[tuple] | None = None) -> None:
        self.calls: list[tuple] = []
        self.commits = 0
        self._fetch = fetch_rows or []

    async def execute(self, sql: str, params=None) -> FakeAsyncConn:
        self.calls.append((sql, params))
        return self

    async def executemany(self, sql: str, params) -> FakeAsyncConn:
        self.calls.append((sql, params))
        return self

    async def commit(self) -> None:
        self.commits += 1

    async def fetchall(self) -> list[tuple]:
        return self._fetch


def test_inmemory_roundtrip(tmp_path: Path) -> None:
    p = tmp_path / "std.pdf"
    pdf_standard(p)
    clauses = parse_contract(p).clauses
    store = InMemoryClauseStore()
    import asyncio

    asyncio.run(store.save("r1", "std", clauses))
    loaded = asyncio.run(store.load("r1", "std"))
    assert len(loaded) == len(clauses)
    assert loaded[0].id == clauses[0].id
    assert loaded[0].text == clauses[0].text


def test_postgres_save_builds_correct_sql(tmp_path: Path) -> None:
    p = tmp_path / "std.pdf"
    pdf_standard(p)
    clauses = parse_contract(p).clauses
    conn = FakeAsyncConn()
    store = PostgresClauseStore(conn)
    import asyncio

    asyncio.run(store.save("r1", "std", clauses))
    sqls = [c[0] for c in conn.calls]
    assert any("DELETE FROM clauses" in s for s in sqls)
    assert any("INSERT INTO clauses" in s for s in sqls)
    assert conn.commits == 1
    # INSERT 参数形状与列数一致。
    insert_params = next(c[1] for c in conn.calls if "INSERT INTO clauses" in c[0])
    assert len(insert_params[0]) == 13


def test_postgres_load_maps_rows(tmp_path: Path) -> None:
    p = tmp_path / "std.pdf"
    pdf_standard(p)
    ref = parse_contract(p).clauses[0]
    row = (
        "r1",
        "std",
        ref.id,
        ref.parent_id,
        ref.no,
        ref.title,
        ref.text,
        ref.level,
        ref.page,
        ref.start,
        ref.end,
        ref.hash,
        ref.type.value,
    )
    conn = FakeAsyncConn(fetch_rows=[row])
    store = PostgresClauseStore(conn)
    import asyncio

    loaded = asyncio.run(store.load("r1", "std"))
    assert len(loaded) == 1
    assert isinstance(loaded[0], Clause)
    assert loaded[0].id == ref.id
    assert loaded[0].type.value == ref.type.value


def test_create_clauses_table_executes_ddl() -> None:
    conn = FakeAsyncConn()
    import asyncio

    asyncio.run(create_clauses_table(conn))
    assert any(CLAUSES_DDL.strip() in c[0] for c in conn.calls)
    assert conn.commits == 1
