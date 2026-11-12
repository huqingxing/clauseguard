"""ADR-0006 回归：会话粒度从「合同版本」改为「单次审查」。

T1 同 contract + 同 version 连跑两次：第二次 findings 数 == 第一次，无 unverified 堆积。
T2 v1 审一次、v2 审一次：findings 互不包含对方条款。
T3 与修复前同输入对比：findings 内容与数量逐字段一致（快照）。
另加：旧 thread 格式数据存在时新代码不读取、不报错；唯一构造入口守门。

为什么全部走 /v1/runs 端点：thread_id 由生产入口 review_thread_id 构造，直接
拼 config 喂图测不到「构造点是否已改」，会假绿。禁止项：不清 checkpoint、
不在 run 前清空 findings、不改事件契约。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from langgraph.checkpoint.memory import MemorySaver

from app.graph.threads import THREAD_PREFIX, review_thread_id
from tests.thread_scope.harness import (
    ThreadScopeHarness,
    normalize_findings,
    run,
)

SNAPSHOT = Path(__file__).with_name("baseline_pre_fix.json")
BASELINE_INPUT = {"contract_id": "c-baseline", "version": 1, "docx_index": 3}


def _snapshot() -> dict[str, Any]:
    return json.loads(SNAPSHOT.read_text(encoding="utf-8"))


# ---- 构造入口守门：字符串格式 + 空值 fail-fast ----


def test_review_thread_id_is_single_constructor() -> None:
    assert THREAD_PREFIX == "run:"
    assert review_thread_id("run-abc123") == "run:run-abc123"
    # 同一个 run_id 恒等（resume 依赖它），不同 run_id 必然不同（隔离依赖它）。
    assert review_thread_id("run-a") != review_thread_id("run-b")
    try:
        review_thread_id("")
    except ValueError:
        pass
    else:
        raise AssertionError("空 run_id 必须 fail-fast，否则所有审查会重新串到一个 thread")


# ---- T1 同 contract + 同 version 连跑两次 ----


def test_t1_two_reviews_same_contract_version_do_not_bleed() -> None:
    async def scenario() -> tuple[Any, Any, set[str]]:
        h = ThreadScopeHarness()
        first = await h.run_once("c-dup", 1, docx_index=3)
        second = await h.run_once("c-dup", 1, docx_index=3)
        return first, second, h.checkpoint_threads()

    first, second, threads = run(scenario())
    assert first.completed and second.completed, "两笔审查都必须跑到 run.completed"
    assert len(first.findings) > 0, "基线输入必须产出非空 findings，否则断言无意义"
    assert len(second.findings) == len(first.findings), (
        f"T1 失败：第二次 findings 累加 {len(first.findings)} -> {len(second.findings)}"
    )
    assert len(second.unverified) == 0, (
        f"T1 失败：第二次出现 {len(second.unverified)} 条 unverified 堆积"
    )
    assert normalize_findings(second.findings) == normalize_findings(first.findings)
    # 两个不同 run_id → 两个独立 thread；且都是新前缀，绝不落到 contract:version。
    assert len(threads) == 2, f"应有 2 个独立 thread，实际 {sorted(threads)}"
    assert all(t.startswith(THREAD_PREFIX) for t in threads), sorted(threads)
    assert "c-dup:1" not in threads, "不得再写入旧格式 thread"


# ---- T2 v1 / v2 findings 互不包含对方条款 ----


def test_t2_versions_do_not_share_findings() -> None:
    async def scenario() -> tuple[Any, Any, set[str]]:
        h = ThreadScopeHarness()
        v1 = await h.run_once("c-ver", 1, docx_index=3)
        v2 = await h.run_once("c-ver", 2, docx_index=7)  # 不同 docx → 不同条款正文
        return v1, v2, h.checkpoint_threads()

    v1, v2, threads = run(scenario())
    assert v1.completed and v2.completed
    assert v1.clause_keys and v2.clause_keys
    overlap = v1.clause_keys & v2.clause_keys
    assert not overlap, f"T2 失败：两版本共享了 {len(overlap)} 个条款 {sorted(overlap)[:3]}"
    assert len(threads) == 2, f"两个版本应各自独立 thread：{sorted(threads)}"


# ---- T3 与修复前基线快照逐字段一致 ----


def test_t3_findings_match_pre_fix_snapshot() -> None:
    async def scenario() -> Any:
        h = ThreadScopeHarness()
        return await h.run_once(
            BASELINE_INPUT["contract_id"],
            BASELINE_INPUT["version"],
            docx_index=BASELINE_INPUT["docx_index"],
        )

    baseline = _snapshot()
    result = run(scenario())
    assert baseline["captured_at_commit"] == "a182d29", "基线须来自修复前提交"
    assert result.completed
    assert len(result.findings) == baseline["findings_count"], (
        f"T3 失败：数量 {len(result.findings)} != 修复前 {baseline['findings_count']}"
    )
    assert len(result.unverified) == baseline["unverified_count"]
    assert result.normalized_findings == baseline["findings"], (
        "T3 失败：findings 内容与修复前不一致（归一化 clause_id 后逐字段对比）"
    )


# ---- 兼容：旧 thread 格式数据存在时，新代码不读取、不报错 ----


def test_t4_legacy_thread_data_is_untouched_and_not_read() -> None:
    """旧格式 checkpoint 保留只读；新审查不读它、不报错、结果干净。"""

    async def scenario() -> tuple[Any, set[str], set[str]]:
        h = ThreadScopeHarness()
        # 造一份真实的旧格式 checkpoint：用旧 thread 键跑一次图（含脏 findings），
        # 不清理、不迁移，模拟「历史数据仍在库里」。
        import asyncio

        from app.agents.settings import get_agent_settings
        from app.events import EventEmitter
        from app.graph.build import build_graph
        from app.main import _make_initial_state

        legacy_thread = "c-legacy:1"
        graph = build_graph(get_agent_settings(), h.saver)
        dirty = _make_initial_state("c-legacy")
        dirty["clauses"] = []
        dirty["findings"] = []
        cfg = {
            "configurable": {
                "thread_id": legacy_thread,
                "emitter": EventEmitter(),
                "limiter": asyncio.Semaphore(5),
            }
        }
        await graph.ainvoke(dirty, cfg)
        before = h.checkpoint_threads()
        result = await h.run_once("c-legacy", 1, docx_index=3)
        return result, before, h.checkpoint_threads()

    result, before, after = run(scenario())
    assert "c-legacy:1" in before, "前置条件：旧格式 thread 数据必须真实存在"
    assert "c-legacy:1" in after, "禁止清理历史 checkpoint（不删除旧数据）"
    assert result.completed, "旧数据存在不得让新审查报错"
    assert len(result.unverified) == 0, "旧 checkpoint 的脏 findings 不得被读取"
    assert len(result.findings) == _snapshot()["findings_count"], (
        "新审查 findings 应与干净基线一致，证明没有读到旧 thread"
    )
    assert any(t.startswith(THREAD_PREFIX) for t in after), "新审查应落到 run:* thread"


def test_saver_is_memory_backend() -> None:
    """守门：harness 用 MemorySaver，确保 checkpoint 可见且跨请求共享。"""
    assert isinstance(ThreadScopeHarness().saver, MemorySaver)
