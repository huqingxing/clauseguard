"""Postgres checkpointer 与 run 注册表（Stage 9 断点恢复基础设施）。

为什么独立成模块：config.py 承载服务级配置，本模块只管 Postgres 连接池与 checkpointer
的生命周期（由 lifespan 创建/关闭），不触碰 config.py 以免牵动服务级配置。
run 注册表把 run_id 映射到断点元信息（thread_id/event_seq/trace_id/session），并持久化
到同一 Postgres——进程重启后 resume 仍能定位断点。铁律 4 要求运行态只在 ReviewState，
但 run→thread 的索引属于基础设施，必须落库而非内存全局变量（否则重启即丢）。

thread_id 的**构造**不在这里：唯一入口是 `app.graph.threads.review_thread_id`
（一次审查 = 一个 thread，见 ADR-0006）。本模块只负责把调用方算好的值落库/读出。
"""

from __future__ import annotations

import logging
from typing import Any, cast

from langgraph.checkpoint.base import BaseCheckpointSaver

logger = logging.getLogger("app.db")

try:
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    from psycopg_pool import AsyncConnectionPool

    _HAS_PG = True
except ImportError:  # 测试/本地未装 Postgres 依赖时回落 MemorySaver
    AsyncPostgresSaver = None  # type: ignore[misc,assignment]
    AsyncConnectionPool = None  # type: ignore[misc,assignment]
    _HAS_PG = False


class RunRegistry:
    """run_id -> 断点元信息（thread_id/contract_id/version/content_hash）。

    生产用 Postgres 持久化；测试可注入内存实现以模拟「重启后注册表仍在」。
    """

    async def put(self, run_id: str, info: dict[str, Any]) -> None:
        raise NotImplementedError

    async def get(self, run_id: str) -> dict[str, Any] | None:
        raise NotImplementedError

    async def delete(self, run_id: str) -> None:
        raise NotImplementedError


class InMemoryRunRegistry(RunRegistry):
    """测试/无 Postgres 时的内存实现；注入同一实例即可模拟重启后注册表存续。"""

    def __init__(self) -> None:
        self._store: dict[str, dict[str, Any]] = {}

    async def put(self, run_id: str, info: dict[str, Any]) -> None:
        self._store[run_id] = dict(info)

    async def get(self, run_id: str) -> dict[str, Any] | None:
        return self._store.get(run_id)

    async def delete(self, run_id: str) -> None:
        self._store.pop(run_id, None)


class PostgresRunRegistry(RunRegistry):
    """Postgres 持久化实现；run_registry 表由 deploy/postgres/init.sql 创建。"""

    def __init__(self, pool: Any) -> None:
        self._pool = pool

    async def put(self, run_id: str, info: dict[str, Any]) -> None:
        async with self._pool.connection() as conn:
            await conn.execute(
                "INSERT INTO run_registry(run_id, thread_id, contract_id, version, content_hash) "
                "VALUES(%s, %s, %s, %s, %s) "
                "ON CONFLICT (run_id) DO UPDATE SET "
                "thread_id=EXCLUDED.thread_id, contract_id=EXCLUDED.contract_id, "
                "version=EXCLUDED.version, content_hash=EXCLUDED.content_hash",
                (
                    run_id,
                    info["thread_id"],
                    info["contract_id"],
                    info["version"],
                    info["content_hash"],
                ),
            )
            await conn.commit()

    async def get(self, run_id: str) -> dict[str, Any] | None:
        async with self._pool.connection() as conn:
            rec = await conn.execute(
                "SELECT run_id, thread_id, contract_id, version, content_hash "
                "FROM run_registry WHERE run_id = %s",
                (run_id,),
            )
            row = await rec.fetchone()
        if row is None:
            return None
        return {
            "run_id": row[0],
            "thread_id": row[1],
            "contract_id": row[2],
            "version": row[3],
            "content_hash": row[4],
        }

    async def delete(self, run_id: str) -> None:
        async with self._pool.connection() as conn:
            await conn.execute("DELETE FROM run_registry WHERE run_id = %s", (run_id,))
            await conn.commit()


class DbPool:
    """lifespan 管理的 Postgres 异步连接池；同时暴露 checkpointer 与持久化注册表。"""

    def __init__(self, dsn: str) -> None:
        if not _HAS_PG:
            raise RuntimeError(
                "未安装 psycopg[pool]/langgraph-checkpoint-postgres，"
                "无法启用 Postgres checkpointer"
            )
        pool = AsyncConnectionPool(conninfo=dsn, open=False, max_size=10)
        # 泛型不匹配仅存于 stub 层面：AsyncPostgresSaver 要求池的 dict 行工厂参数，
        # 而它内部查询自带 dict_row（langgraph/checkpoint/postgres/aio.py），池的
        # 默认 tuple 行由 PostgresRunRegistry 按位置消费（row[0]），两者互不影响。
        # _saver 保持 Any：BaseCheckpointSaver stub 未声明 setup()（PostgresSaver 特有），
        # 而 checkpointer() 出口处再收敛为 BaseCheckpointSaver[Any]。
        self._saver: Any = AsyncPostgresSaver(cast(Any, pool))
        self._pool = pool

    async def startup(self) -> None:
        await self._pool.open()
        # 幂等建表：checkpointer 所需的 triples/checkpoints/blob 表。
        await self._saver.setup()

    async def shutdown(self) -> None:
        await self._pool.close()

    def checkpointer(self) -> BaseCheckpointSaver[Any]:
        return cast(BaseCheckpointSaver[Any], self._saver)

    def registry(self) -> RunRegistry:
        return PostgresRunRegistry(self._pool)


# `thread_id_for(contract_id, version)` 已移除（ADR-0006）：按合同版本复用 thread 会让
# 同版本的两笔审查串状态。构造 thread_id 请统一调用 app.graph.threads.review_thread_id。
