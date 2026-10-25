"""结果缓存的验收：键的三维敏感性（clause.hash / prompt_version / model）+ 两个后端。

三维敏感性是硬验收：「改了 prompt_version 必然 miss」——否则旧口径的结论会被当成
新口径的结论交付，缓存就从加速手段变成事故源。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from app.graph.state import Citation, Clause, Finding
from app.incremental.cache import (
    CacheSettings,
    CacheStats,
    InMemoryIncrementalStore,
    JsonFileIncrementalStore,
    build_incremental_store,
    cache_key,
)

HASH = "abc123"
MODEL = "gpt-4o-mini"
EXPERT = "commercial"


def finding(clause_id: str, risk_type: str = "payment_risk") -> Finding:
    return Finding(
        clause_id=clause_id,
        agent="commercial_expert",
        risk_type=risk_type,
        risk_level="high",
        summary="预付比例过高",
        suggestion="改为分期支付",
        evidence="100%",
        citations=[
            Citation(
                source_id="LAW-001",
                title="中华人民共和国民法典",
                excerpt="定金不得超过主合同标的额的百分之二十。",
                article="第五百八十六条",
            )
        ],
        citation_whitelist=("LAW-001",),
    )


def clause(clause_id: str, text: str = "甲方应付款") -> Clause:
    return Clause(clause_id=clause_id, index=0, text=text, no="第一条")


# ---- 缓存键的三维敏感性 ----


def test_cache_key_is_stable_and_three_dimensional() -> None:
    assert cache_key(HASH, 1, MODEL, EXPERT) == cache_key(HASH, 1, MODEL, EXPERT)
    assert len(cache_key(HASH, 1, MODEL, EXPERT)) == 64  # sha256 十六进制


def test_cache_key_changes_with_prompt_version() -> None:
    """prompt 升级必然 miss（硬验收）：旧结论的判定口径已变，复用等于交付过期结论。"""
    assert cache_key(HASH, 1, MODEL, EXPERT) != cache_key(HASH, 2, MODEL, EXPERT)


def test_cache_key_changes_with_model_and_clause_hash() -> None:
    assert cache_key(HASH, 1, MODEL, EXPERT) != cache_key(HASH, 1, "qwen-plus", EXPERT)
    assert cache_key(HASH, 1, MODEL, EXPERT) != cache_key("other-hash", 1, MODEL, EXPERT)


def test_cache_key_changes_with_expert() -> None:
    """专家是键的必须项：六个专家当前同为 prompt_version=1 且共用模型，缺了 expert
    则键完全撞车——会把主体资格专家的结论当商业条款专家的结论复用（正确性硬伤）。"""
    assert cache_key(HASH, 1, MODEL, "commercial") != cache_key(HASH, 1, MODEL, "subject")


# ---- 命中率指标 ----


def test_cache_stats_hit_rate_without_queries_is_zero() -> None:
    """无查询时命中率为 0：不制造 100% 的假象。"""
    assert CacheStats().hit_rate == 0.0
    assert CacheStats(hits=3, misses=1).hit_rate == 0.75
    assert CacheStats(hits=3, misses=1).total == 4


# ---- 内存后端 ----


def test_in_memory_store_roundtrip_both_namespaces() -> None:
    async def _run() -> None:
        store = InMemoryIncrementalStore()
        assert await store.get_findings("missing") is None
        await store.put_findings("k1", [finding("c-1")])
        cached = await store.get_findings("k1")
        assert cached is not None and len(cached) == 1
        assert cached[0].clause_id == "c-1"

        assert await store.get_baseline("contract-1", 1) is None
        await store.put_baseline("contract-1", 1, [clause("c-1")])
        snapshot = await store.get_baseline("contract-1", 1)
        assert snapshot is not None and [c.clause_id for c in snapshot] == ["c-1"]
        # 版本是键的一部分：换版本读不到。
        assert await store.get_baseline("contract-1", 2) is None

    asyncio.run(_run())


def test_in_memory_store_returns_copies() -> None:
    """取出的列表是副本：调用方误改不会污染缓存（缓存是事实镜像，不是共享可变状态）。"""
    async def _run() -> None:
        store = InMemoryIncrementalStore()
        original = [finding("c-1")]
        await store.put_findings("k", original)
        retrieved = await store.get_findings("k")
        assert retrieved is not None
        retrieved.append(finding("c-2"))
        assert len((await store.get_findings("k")) or []) == 1

    asyncio.run(_run())


# ---- JSON 文件后端 ----


def test_json_file_store_roundtrip_and_survives_new_instance(tmp_path: Path) -> None:
    async def _run() -> None:
        first = JsonFileIncrementalStore(tmp_path)
        await first.put_findings("k" * 64, [finding("c-1"), finding("c-2", "delivery_risk")])
        await first.put_baseline("contract/1", 3, [clause("c-1"), clause("c-2")])

        # 新实例 = 模拟进程重启：缓存必须仍在（内存实现重启即空，这是两者的语义差别）。
        second = JsonFileIncrementalStore(tmp_path)
        cached = await second.get_findings("k" * 64)
        assert cached is not None and len(cached) == 2
        snapshot = await second.get_baseline("contract/1", 3)
        assert snapshot is not None and len(snapshot) == 2

    asyncio.run(_run())


def test_json_file_store_treats_corrupt_payload_as_miss(tmp_path: Path) -> None:
    """损坏的缓存文件按未命中处理：缓存是加速手段，不是事实源，绝不能炸掉整轮 run。"""
    async def _run() -> None:
        store = JsonFileIncrementalStore(tmp_path)
        path = store._findings_path("f" * 64)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{ not json", encoding="utf-8")
        assert await store.get_findings("f" * 64) is None

        baseline_path = store._baseline_path("contract-1", 1)
        baseline_path.parent.mkdir(parents=True, exist_ok=True)
        baseline_path.write_text(json.dumps({"unexpected": True}), encoding="utf-8")
        assert await store.get_baseline("contract-1", 1) is None

    asyncio.run(_run())


def test_json_file_store_rejects_path_traversal(tmp_path: Path) -> None:
    """外部标识（contract_id）参与路径拼接：必须收敛为安全文件名，杜绝穿越。"""
    async def _run() -> None:
        store = JsonFileIncrementalStore(tmp_path)
        await store.put_baseline("../../etc/passwd", 1, [clause("c-1")])
        written = list((tmp_path / "baseline").rglob("*.json"))
        assert written, "基线快照必须写盘"
        assert all(tmp_path in p.parents for p in written), f"写出目录逃逸: {written}"

    asyncio.run(_run())


# ---- 后端选择 ----


def test_build_store_defaults_to_memory_when_dir_empty() -> None:
    assert isinstance(build_incremental_store(CacheSettings(dir="")), InMemoryIncrementalStore)


def test_build_store_uses_json_file_when_dir_configured(tmp_path: Path) -> None:
    store = build_incremental_store(CacheSettings(dir=str(tmp_path)))
    assert isinstance(store, JsonFileIncrementalStore)
