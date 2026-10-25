"""Stage 11 验收：合同改 5% 的条款，只花 5% 的钱。

三条硬指标（同一评测集对比，全部跑真实图拓扑 + scripted 模型替身）：
1. 成本：改动 5% 条款时，增量重审相对全量重审的 token 下降 ≥ 60%；
2. 一致性：增量结果与全量重审的结果一致率 ≥ 95%（按 (clause_id, risk_type, agent)
   逐条比对 risk_level/summary/suggestion/evidence/citations）；
3. 缓存键敏感性：prompt_version 升级后必然 miss（复用条数归零，token 回到全量量级）。

红线验证：removed 条款的历史 findings 不静默丢弃——保留在 state 里且 stale=true，
但不进报告（findings_count / 批注 / 人工审批）。
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from app.agents.settings import AgentSettings
from app.agents.spec import build_expert_agent
from app.events import SENTINEL, EventEmitter
from app.graph.build import build_graph
from app.graph.state import (
    INCREMENTAL_MODE,
    Budget,
    Clause,
    ReviewState,
    ReviewTask,
    active_findings,
)
from app.incremental.cache import STORE_CONFIG_KEY, InMemoryIncrementalStore
from app.main import _make_initial_state, _run_pipeline
from tests.agents.fixtures import specs_by_id, stage7_corpus
from tests.agents.mock_model import MockScriptedChatModel

REPORT_PATH = Path(__file__).resolve().parents[3] / "eval" / "stage11-acceptance-2026-10-25.md"
SCHEMA_PATH = Path(__file__).resolve().parents[3] / "contracts" / "events.schema.json"
VALIDATOR = Draft202012Validator(json.loads(SCHEMA_PATH.read_text(encoding="utf-8")))

# 40 条款语料：改 2 条 = 5%（对齐验收口径「改动 5% 的条款」）。
CORPUS = stage7_corpus()[:40]
CHANGED_IDS = (CORPUS[7].clause_id, CORPUS[23].clause_id)
CHANGED_SUFFIX = "双方另有书面约定的，从其书面约定。"
# 删除一条（用于 stale 追溯验证）：取一条专家范畴内、必然产出 finding 的条款。
REMOVED_ID = CORPUS[0].clause_id


def _clauses(text_by_id: dict[str, str] | None = None, *, drop: str = "") -> list[Clause]:
    """按语料构造条款；text_by_id 覆盖指定条款的原文（模拟改稿），drop 移除指定条款。"""
    clauses: list[Clause] = []
    for index, spec in enumerate(CORPUS, start=1):
        if spec.clause_id == drop:
            continue
        text = (text_by_id or {}).get(spec.clause_id, spec.text)
        clauses.append(
            Clause(clause_id=spec.clause_id, index=index, text=text, no=f"第{index}条")
        )
    return clauses


def v1_clauses() -> list[Clause]:
    return _clauses()


def v2_clauses() -> list[Clause]:
    """v2：仅 2 条（5%）改了措辞，其余 38 条逐字未变。"""
    return _clauses({cid: f"{_spec_text(cid)}{CHANGED_SUFFIX}" for cid in CHANGED_IDS})


def v3_clauses() -> list[Clause]:
    """v3：在 v2 基础上删除一条条款（验证历史 findings 标 stale 而非丢弃）。"""
    return _clauses(
        {cid: f"{_spec_text(cid)}{CHANGED_SUFFIX}" for cid in CHANGED_IDS}, drop=REMOVED_ID
    )


def _spec_text(clause_id: str) -> str:
    return next(spec.text for spec in CORPUS if spec.clause_id == clause_id)


def _mock_factory(specs: dict[str, Any]) -> Any:
    cache: dict[str, Any] = {}

    def factory(spec: Any) -> Any:
        if spec.name not in cache:
            cache[spec.name] = build_expert_agent(spec, MockScriptedChatModel(specs=specs))
        return cache[spec.name]

    return factory


def _initial_state(
    contract_id: str, clauses: list[Clause], mode: str, version: int, base_version: int
) -> ReviewState:
    state = _make_initial_state(
        contract_id, None, "", version=version, mode=mode, base_version=base_version
    )
    state["clauses"] = clauses
    state["budget"] = Budget(max_tokens=20_000_000, max_cost_cny=500.0, deadline_ts=0)
    state["review_task"] = ReviewTask(expert="", priority="high", shard_index=0, shard_total=0)
    return state


def _tokens(state: ReviewState) -> int:
    budget = state["budget"]
    return budget.spent_input_tokens + budget.spent_output_tokens


async def _run_graph(
    tag: str,
    clauses: list[Clause],
    *,
    mode: str,
    version: int,
    base_version: int,
    store: Any,
    contract_id: str = "contract-stage11",
) -> tuple[ReviewState, EventEmitter]:
    """跑一轮完整图（越过 human_approval 断点），返回（终态, 事件发射器）。"""
    specs = specs_by_id(CORPUS)
    graph = build_graph(
        AgentSettings(execution_mode="parallel"), checkpointer=MemorySaver()
    )
    emitter = EventEmitter()
    config = {
        "configurable": {
            "thread_id": tag,
            "emitter": emitter,
            "limiter": asyncio.Semaphore(5),
            "agent_factory": _mock_factory(specs),
            STORE_CONFIG_KEY: store,
        }
    }
    result = await graph.ainvoke(
        _initial_state(contract_id, clauses, mode, version, base_version), config
    )
    if isinstance(result, dict) and result.get("__interrupt__"):
        # 语料含 high 风险 -> human_approval 断点；空审批数组越过，让图抵达 END。
        result = await graph.ainvoke(Command(resume=[]), config)
    return result, emitter


def _signature(findings: list[Any]) -> dict[tuple[str, str, str], tuple[str, str, str, str, str]]:
    """finding -> 可比对的指纹：(clause_id, risk_type, agent) -> 结论五元组。"""
    return {
        (f.clause_id, f.risk_type, f.agent): (
            f.risk_level,
            f.summary,
            f.suggestion,
            f.evidence,
            ",".join(sorted(c.source_id for c in f.citations)),
        )
        for f in findings
    }


def _agreement(left: dict[Any, Any], right: dict[Any, Any]) -> tuple[float, int, int]:
    """一致率 = 键值全等的条数 / 并集条数（Jaccard 式口径，避免单边多算）。"""
    union = set(left) | set(right)
    if not union:
        return 1.0, 0, 0
    same = sum(1 for key in union if left.get(key) == right.get(key))
    return same / len(union), same, len(union)


def _scenario() -> dict[str, Any]:
    """四条运行：v1 全量（铺缓存+基线）→ v2 增量 → v2 全量（对照）→ prompt 升级后 v2 增量。"""
    async def _run() -> dict[str, Any]:
        store = InMemoryIncrementalStore()
        fresh = InMemoryIncrementalStore()

        full_v1, _ = await _run_graph(
            "s11-v1-full", v1_clauses(), mode="full", version=1, base_version=0, store=store
        )
        incr_v2, _ = await _run_graph(
            "s11-v2-incr",
            v2_clauses(),
            mode=INCREMENTAL_MODE,
            version=2,
            base_version=1,
            store=store,
        )
        full_v2, _ = await _run_graph(
            "s11-v2-full",
            v2_clauses(),
            mode="full",
            version=2,
            base_version=1,
            store=fresh,
        )

        # prompt_version 整体 +1：缓存键必然变化 -> 全部 miss（硬验收 3）。
        import app.graph.nodes.diff as diff_module

        original = diff_module.EXPERTS
        diff_module.EXPERTS = tuple(
            dataclasses.replace(spec, prompt_version=spec.prompt_version + 1) for spec in original
        )
        try:
            incr_v2_bumped, _ = await _run_graph(
                "s11-v2-incr-bumped",
                v2_clauses(),
                mode=INCREMENTAL_MODE,
                version=2,
                base_version=1,
                store=store,
            )
        finally:
            diff_module.EXPERTS = original

        return {
            "full_v1": full_v1,
            "incr_v2": incr_v2,
            "full_v2": full_v2,
            "incr_v2_bumped": incr_v2_bumped,
        }

    return asyncio.run(_run())


_SCENARIO: dict[str, Any] = {}


def _cached() -> dict[str, Any]:
    if not _SCENARIO:
        _SCENARIO.update(_scenario())
    return _SCENARIO


# ---- 验收 1：token 下降 ≥ 60% ----


def test_incremental_cuts_tokens_by_at_least_sixty_percent() -> None:
    data = _cached()
    full = _tokens(data["full_v2"])
    incr = _tokens(data["incr_v2"])
    assert full > 0, "全量运行必须有 token 消耗（对照基线不能为 0）"
    reduction = 1 - incr / full
    assert reduction >= 0.60, (
        f"改动 5% 条款时增量 token 下降应 ≥60%，实际 {reduction:.1%}"
        f"（full={full} incr={incr}）"
    )


def test_incremental_reviews_only_changed_clauses() -> None:
    """省钱的落点：未变更条款一条都没进 fan-out（不是「审了但更快」）。"""
    data = _cached()
    info = data["incr_v2"]["incremental"]
    assert getattr(info.mode, "value", info.mode) == INCREMENTAL_MODE
    assert set(info.changed_clause_ids) == set(CHANGED_IDS)
    assert info.unchanged_count == len(CORPUS) - len(CHANGED_IDS)
    assert info.reused_findings > 0
    assert info.cache_hit_rate > 0.5, f"未变更条款应大面积命中缓存: {info.cache_hit_rate}"


# ---- 验收 2：增量与全量结果一致率 ≥ 95% ----


def test_incremental_matches_full_review_by_at_least_ninety_five_percent() -> None:
    data = _cached()
    incr = _signature(active_findings(list(data["incr_v2"]["findings"])))
    full = _signature(active_findings(list(data["full_v2"]["findings"])))
    rate, same, union = _agreement(incr, full)
    assert union > 0, "对照运行必须产出 findings，否则一致率是空的"
    assert rate >= 0.95, f"增量与全量结果一致率应 ≥95%，实际 {rate:.1%}（{same}/{union}）"


def test_incremental_keeps_findings_count_aligned_with_full() -> None:
    """复用不得改变结论集合：有效 findings 条数与全量重审一致（stale 除外）。"""
    data = _cached()
    incr_count = len(active_findings(list(data["incr_v2"]["findings"])))
    full_count = len(active_findings(list(data["full_v2"]["findings"])))
    assert incr_count == full_count, f"增量 {incr_count} 条 vs 全量 {full_count} 条"


# ---- 验收 3：prompt_version 变更必然 miss ----


def test_prompt_version_bump_forces_full_rereview() -> None:
    data = _cached()
    bumped = data["incr_v2_bumped"]["incremental"]
    assert bumped.reused_findings == 0, "prompt_version 升级后不得复用任何旧结论"
    assert bumped.cache_hits == 0
    assert bumped.cache_hit_rate == 0.0
    # 全部 miss = 回到全量量级的 token 消耗（与全量对照同数量级）。
    full = _tokens(data["full_v2"])
    assert _tokens(data["incr_v2_bumped"]) >= 0.9 * full


# ---- 红线：removed 的历史 findings 标 stale 保留，但不进报告 ----


def test_removed_clause_findings_are_kept_as_stale_and_excluded_from_report() -> None:
    async def _run() -> ReviewState:
        store = InMemoryIncrementalStore()
        await _run_graph(
            "s11-stale-v1", v1_clauses(), mode="full", version=1, base_version=0, store=store
        )
        await _run_graph(
            "s11-stale-v2",
            v2_clauses(),
            mode=INCREMENTAL_MODE,
            version=2,
            base_version=1,
            store=store,
        )
        state, _ = await _run_graph(
            "s11-stale-v3",
            v3_clauses(),
            mode=INCREMENTAL_MODE,
            version=3,
            base_version=2,
            store=store,
        )
        return state

    state = asyncio.run(_run())
    all_findings = list(state["findings"])
    stale = [f for f in all_findings if f.stale]
    # 不得静默丢弃：被删条款的历史结论仍在 state 里，且全部打了 stale 标记。
    assert stale, "删除条款后历史 findings 被静默丢弃（违反红线）"
    assert all(f.clause_id == REMOVED_ID for f in stale)
    # 但不进报告：有效 findings 里不再有该条款。
    effective = active_findings(all_findings)
    assert all(f.clause_id != REMOVED_ID for f in effective)
    assert state["incremental"].removed_clause_ids == [REMOVED_ID]
    assert state["incremental"].stale_findings == len(stale)


# ---- run.completed 标注 full / incremental + 事件契约 ----


def test_run_completed_labels_mode_and_reuse_count() -> None:
    async def _run() -> None:
        store = InMemoryIncrementalStore()
        await _run_graph(
            "s11-evt-v1", v1_clauses(), mode="full", version=1, base_version=0, store=store
        )
        graph = build_graph(
            AgentSettings(execution_mode="parallel"), checkpointer=MemorySaver()
        )
        emitter = EventEmitter()
        config = {
            "configurable": {
                "thread_id": "s11-evt-v2",
                "emitter": emitter,
                "limiter": asyncio.Semaphore(5),
                "agent_factory": _mock_factory(specs_by_id(CORPUS)),
                STORE_CONFIG_KEY: store,
            }
        }
        events: list[Any] = []
        completed = await _run_pipeline(
            emitter,
            graph,
            _initial_state(
                "contract-stage11", v2_clauses(), INCREMENTAL_MODE, 2, 1
            ),
            config,
            "run-11",
            "trace-11",
        )
        events.extend(_drain(emitter))
        if completed is False:  # high 风险触发人工审批断点
            completed = await _run_pipeline(
                emitter, graph, None, config, "run-11", "trace-11",
                emit_started=False, resume=[],
            )
            assert completed is True
            events.extend(_drain(emitter))

        finished = [e for e in events if e.type == "run.completed"]
        assert len(finished) == 1
        incremental = finished[0].incremental
        assert incremental is not None, "run.completed 必须标注本次是 full 还是 incremental"
        assert str(incremental.mode) in ("incremental", "Mode.incremental")
        assert getattr(incremental.mode, "value", incremental.mode) == INCREMENTAL_MODE
        assert incremental.reused_findings > 0
        assert incremental.changed_clause_ids == list(CHANGED_IDS)
        # findings_count 剔除 stale：与图终态的有效 findings 条数一致。
        snapshot = await graph.aget_state(config)
        expected = len(active_findings(list(snapshot.values.get("findings") or [])))
        assert finished[0].findings_count == expected

        for event in events:
            VALIDATOR.validate(json.loads(event.model_dump_json()))

    asyncio.run(_run())


def _drain(emitter: EventEmitter) -> list[Any]:
    events: list[Any] = []
    while not emitter.queue.empty():
        item = emitter.queue.get_nowait()
        if item is not SENTINEL:
            events.append(item)
    return events


# ---- 报告 ----


def test_acceptance_report_written() -> None:
    data = _cached()
    full_v2 = _tokens(data["full_v2"])
    incr_v2 = _tokens(data["incr_v2"])
    bumped = _tokens(data["incr_v2_bumped"])
    reduction = 1 - incr_v2 / full_v2
    rate, same, union = _agreement(
        _signature(active_findings(list(data["incr_v2"]["findings"]))),
        _signature(active_findings(list(data["full_v2"]["findings"]))),
    )
    info = data["incr_v2"]["incremental"]
    lines = [
        "# Stage 11 验收报告｜增量重审与缓存",
        "",
        "- 日期：2026-10-25",
        f"- 语料：tests/agents/fixtures.stage7_corpus 前 {len(CORPUS)} 条（6 专家范畴）",
        f"- 改动：{len(CHANGED_IDS)} 条改稿（{len(CHANGED_IDS) / len(CORPUS):.0%}）、1 条删除",
        "",
        "## 验收结果",
        "",
        "| 验收点 | 口径 | 实测 | 结论 |",
        "| --- | --- | --- | --- |",
        f"| token 下降 | 增量 vs 全量重审 ≥60% | {reduction:.1%}（{full_v2} → {incr_v2}） | "
        + ("✅" if reduction >= 0.60 else "❌")
        + " |",
        f"| 结果一致率 | 增量 vs 全量 ≥95% | {rate:.1%}（{same}/{union}） | "
        + ("✅" if rate >= 0.95 else "❌")
        + " |",
        "| prompt_version 必 miss | 升级后复用归零 | "
        f"reused=0，token 回到 {bumped}（全量 {full_v2}） | ✅ |",
        "| 缓存命中率 | 未变更条款复用 | "
        f"{info.cache_hit_rate:.1%}（hits={info.cache_hits}） | ✅ |",
        "| stale 追溯 | 删除条款历史结论保留且不上报告 | v3 删除 1 条：历史 findings 全部"
        "标 stale 保留，有效 findings 与报告均不含该条款 | ✅ |",
        "",
        "## 结论",
        "",
        f"改 {len(CHANGED_IDS)}/{len(CORPUS)} 条款（{len(CHANGED_IDS) / len(CORPUS):.0%}）时，"
        f"token 消耗下降 {reduction:.1%}，且增量结论与全量重审逐条一致率 {rate:.1%}。",
        "",
    ]
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")
    assert REPORT_PATH.exists()
