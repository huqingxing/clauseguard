"""Stage 11 节点接入的验收：diff / cache_store / 收窄 fan-out / stale 的端到端语义。

为什么测节点而不是只测纯函数：增量重审的风险点全在「接线」——缓存命中后有没有真的
少派发、removed 的历史结论有没有标 stale 而不是被丢掉、stale 有没有被排除出报告。
这些只有过一遍节点才验证得到。
"""

from __future__ import annotations

import asyncio
import dataclasses
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END

from app.agents.registry import EXPERTS
from app.agents.settings import AgentSettings
from app.events import EventEmitter
from app.graph.nodes.cache_store import cache_store, group_findings
from app.graph.nodes.diff import diff_node, full_targets
from app.graph.nodes.dispatch import (
    build_sends,
    dispatch,
    incremental_targets,
    planning_pool,
    scoped_clauses,
)
from app.graph.nodes.human_approval import high_risk_findings
from app.graph.nodes.serial_review import serial_review
from app.graph.state import (
    INCREMENTAL_MODE,
    Budget,
    Citation,
    Clause,
    Finding,
    IncrementalInfo,
    ReviewState,
    ReviewTask,
    active_findings,
)
from app.incremental.cache import (
    STORE_CONFIG_KEY,
    CacheStats,
    InMemoryIncrementalStore,
    cache_key,
)
from app.incremental.diff import clause_fingerprint
from app.render.service import active_findings as render_active_findings

# 缓存键的 model 维度取自配置（diff.py 用 settings.llm_model），不是硬编码常量：
# 这里必须跟着配置走，否则 .env 换了模型，预置的缓存键与节点算出来的键不一致，
# 测试会「因为本机配了 qwen-plus」而红——那是测试耦合环境，不是缓存失效。
MODEL = AgentSettings().llm_model


def clause(clause_id: str, no: str, text: str, index: int = 0) -> Clause:
    return Clause(clause_id=clause_id, index=index, text=text, no=no)


def finding(
    clause_id: str,
    agent: str = "commercial_expert",
    risk_level: str = "high",
    risk_type: str = "payment_risk",
    *,
    stale: bool = False,
) -> Finding:
    return Finding(
        clause_id=clause_id,
        agent=agent,
        risk_type=risk_type,
        risk_level=risk_level,
        summary="摘要",
        suggestion="建议",
        evidence="证据",
        citations=[
            Citation(
                source_id="LAW-001",
                title="中华人民共和国民法典",
                excerpt="定金不得超过主合同标的额的百分之二十。",
                article="第五百八十六条",
            )
        ],
        citation_whitelist=("LAW-001",),
        stale=stale,
    )


def config(store: Any) -> RunnableConfig:
    return {"configurable": {"emitter": EventEmitter(), STORE_CONFIG_KEY: store}}


def state(
    clauses: list[Clause],
    *,
    mode: str = "full",
    version: int = 2,
    base_version: int = 1,
    baseline: list[Clause] | None = None,
    findings: list[Finding] | None = None,
    incremental: Any = None,
    targets: dict[str, list[str]] | None = None,
) -> ReviewState:
    payload: dict[str, Any] = {
        "contract_id": "contract-1",
        "version": version,
        "clauses": clauses,
        "findings": findings or [],
        "budget": Budget(max_tokens=2_000_000, max_cost_cny=50.0, deadline_ts=0),
        "critic_failures": 0,
        "approved": False,
        "degraded": False,
        "review_task": ReviewTask(expert="", priority="high", shard_index=0, shard_total=0),
        "rework": [],
        "event_seq": 0,
        "content_hash": "hash",
        "render_result": "",
        "review_mode": mode,
        "base_version": base_version,
    }
    if baseline is not None:
        payload["baseline"] = baseline
    if incremental is not None:
        payload["incremental"] = incremental
    if targets is not None:
        payload["review_targets"] = targets
    return payload  # type: ignore[return-value]


def _agent_cache() -> dict[str, Any]:
    """串行路径的 agent 工厂缓存；本文件只验证「收窄」，用空 agent 让 review 走降级不炸。"""
    return {}


# ---- diff 节点：全量模式 ----


def test_diff_node_full_mode_targets_everything_and_reads_no_cache() -> None:
    store = InMemoryIncrementalStore()
    clauses = [clause("c-1", "第一条", "买方应在验收合格后30日内支付货款")]

    async def _run() -> dict[str, Any]:
        key = cache_key(clause_fingerprint(clauses[0]), 1, MODEL, EXPERTS[0].name)
        await store.put_findings(key, [finding("c-1")])
        return await diff_node(state(clauses, mode="full"), config(store))

    out = asyncio.run(_run())
    # 全量是成本对比基线：刻意不查缓存（否则增量 vs 全量的 token 对比失去意义）。
    assert out["incremental"].mode == "full"
    assert out["incremental"].reused_findings == 0
    assert out["incremental"].cache_hits == 0
    assert out["findings"] == []
    assert full_targets(clauses) == out["review_targets"]


def test_diff_node_without_baseline_falls_back_to_full() -> None:
    """请求了增量但拿不到基线（首版）：退化为全量并重审，不静默空跑。"""
    clauses = [clause("c-1", "第一条", "买方应在验收合格后30日内支付货款")]

    async def _run() -> dict[str, Any]:
        return await diff_node(
            state(clauses, mode=INCREMENTAL_MODE), config(InMemoryIncrementalStore())
        )

    out = asyncio.run(_run())
    assert out["incremental"].mode == "full"
    assert out["review_targets"] == full_targets(clauses)


# ---- diff 节点：增量模式 ----


def test_diff_node_incremental_reuses_cache_and_narrows_targets() -> None:
    unchanged = clause("c-1", "第一条", "买方应在验收合格后30日内支付货款")
    changed_v1 = clause("c-2", "第二条", "卖方逾期交付的，按日支付违约金")
    changed_v2 = clause("c-2", "第二条", "卖方逾期交付的，按日支付合同总价0.05%的违约金")
    baseline = [unchanged, changed_v1]
    current = [unchanged, changed_v2]

    async def _run() -> tuple[dict[str, Any], InMemoryIncrementalStore]:
        store = InMemoryIncrementalStore()
        # 预置缓存：未变更条款命中 -> 直接复用；变更条款的键不同 -> 必然 miss -> 重审。
        commercial = EXPERTS[0]
        await store.put_findings(
            cache_key(
                clause_fingerprint(unchanged), commercial.prompt_version, MODEL, commercial.name
            ),
            [finding("c-1", agent=commercial.agent_name)],
        )
        out = await diff_node(
            state(current, mode=INCREMENTAL_MODE, baseline=baseline), config(store)
        )
        return out, store

    out, _ = asyncio.run(_run())
    info = out["incremental"]
    assert info.mode == INCREMENTAL_MODE
    assert info.changed_clause_ids == ["c-2"]
    assert info.unchanged_count == 1
    assert info.reused_findings == 1
    # 未变更条款不进任何专家的目标集（省钱落在这一行上）。
    all_targets = {cid for ids in out["review_targets"].values() for cid in ids}
    assert "c-1" not in all_targets
    assert "c-2" in all_targets


def test_diff_node_repointed_reused_findings_to_current_clause_id() -> None:
    """缓存里的 ID 是旧版本的（切分序号会错位），复用时必须改指当前版本 ID。"""
    old = clause("v1-2", "第二条", "卖方逾期交付的，按日支付违约金")
    new = clause("v2-5", "第二条", "卖方逾期交付的，按日支付违约金")
    store = InMemoryIncrementalStore()

    async def _run() -> dict[str, Any]:
        commercial = EXPERTS[0]
        await store.put_findings(
            cache_key(
                clause_fingerprint(old), commercial.prompt_version, MODEL, commercial.name
            ),
            [finding("v1-2", agent=commercial.agent_name)],
        )
        return await diff_node(
            state([new], mode=INCREMENTAL_MODE, baseline=[old]), config(store)
        )

    out = asyncio.run(_run())
    reused = out["findings"]
    assert [f.clause_id for f in reused] == ["v2-5"], "复用结论必须指向当前版本的条款 ID"


def test_diff_node_marks_removed_clause_findings_stale() -> None:
    """removed 的历史结论标 stale 保留（红线：不得静默丢弃），且不进重审目标。"""
    kept = clause("c-1", "第一条", "买方应在验收合格后30日内支付货款")
    dropped = clause("c-2", "第二条", "卖方逾期交付的，按日支付违约金")

    async def _run() -> dict[str, Any]:
        store = InMemoryIncrementalStore()
        commercial = EXPERTS[0]
        await store.put_findings(
            cache_key(
                clause_fingerprint(dropped), commercial.prompt_version, MODEL, commercial.name
            ),
            [
                finding("c-2", agent=commercial.agent_name, risk_type="delivery_risk"),
                finding("c-2", agent=commercial.agent_name, risk_type="penalty_risk"),
            ],
        )
        return await diff_node(
            state([kept], mode=INCREMENTAL_MODE, baseline=[kept, dropped]), config(store)
        )

    out = asyncio.run(_run())
    stale = [f for f in out["findings"] if f.stale]
    assert len(stale) == 2, "被删条款的历史 findings 必须全部保留为 stale"
    assert {f.risk_type for f in stale} == {"delivery_risk", "penalty_risk"}
    assert out["incremental"].removed_clause_ids == ["c-2"]
    assert out["incremental"].stale_findings == 2


def test_diff_node_prompt_version_bump_forces_miss() -> None:
    """prompt_version 升级后旧缓存必然 miss（硬验收）：旧口径结论不得被复用。"""
    target = clause("c-1", "第一条", "买方应在验收合格后30日内支付货款")
    store = InMemoryIncrementalStore()
    import app.graph.nodes.diff as diff_module

    original = diff_module.EXPERTS
    bumped = tuple(
        dataclasses.replace(spec, prompt_version=spec.prompt_version + 1)
        if spec.name == EXPERTS[0].name
        else spec
        for spec in original
    )

    async def _run() -> dict[str, Any]:
        commercial = EXPERTS[0]
        # 用「当前」prompt_version 预置缓存，再让节点以 prompt_version+1 去查。
        await store.put_findings(
            cache_key(
                clause_fingerprint(target), commercial.prompt_version, MODEL, commercial.name
            ),
            [finding("c-1", agent=commercial.agent_name)],
        )
        diff_module.EXPERTS = bumped
        try:
            return await diff_node(
                state([target], mode=INCREMENTAL_MODE, baseline=[target]), config(store)
            )
        finally:
            diff_module.EXPERTS = original

    out = asyncio.run(_run())
    info = out["incremental"]
    assert info.reused_findings == 0, "prompt_version 变更后必须 miss，不得复用旧结论"
    assert info.cache_misses >= 1
    assert info.cache_hit_rate == 0.0


# ---- fan-out 收窄 ----


def test_dispatch_and_serial_narrow_by_review_targets() -> None:
    clauses = [
        clause("c-1", "第一条", "买方应在验收合格后30日内支付货款"),
        clause("c-2", "第二条", "卖方逾期交付的，按日支付违约金"),
    ]
    targets = {EXPERTS[0].name: ["c-2"]}
    payload = state(
        clauses,
        mode=INCREMENTAL_MODE,
        incremental=IncrementalInfo(mode=INCREMENTAL_MODE),
        targets=targets,
    )
    assert incremental_targets(payload) == targets
    # 预算规划只看目标池（不在预算上为未审条款预留）。
    assert [c.clause_id for c in planning_pool(payload, targets)] == ["c-2"]
    assert [
        c.clause_id for c in scoped_clauses(payload, EXPERTS[0], targets)
    ] == ["c-2"]
    sends, _skipped = build_sends(payload)
    dispatched = {
        clause_.clause_id
        for send in sends
        for clause_ in send.arg["review_task"].clauses
    }
    assert dispatched == {"c-2"}, f"派发载荷不得包含未变更条款: {dispatched}"


def test_full_mode_dispatch_uses_all_clauses() -> None:
    clauses = [clause("c-1", "第一条", "买方应在验收合格后30日内支付货款")]
    payload = state(clauses, mode="full")
    assert incremental_targets(payload) is None, "全量模式必须返回 None（审全部），不是空集"
    assert [c.clause_id for c in planning_pool(payload, None)] == ["c-1"]


def test_serial_review_skips_unchanged_clauses() -> None:
    """串行拓扑同样收窄：不允许「串行偷偷全量审」破坏成本语义。"""
    unchanged = clause("c-1", "第一条", "买方应在验收合格后30日内支付货款")
    changed = clause("c-2", "第二条", "卖方逾期交付的，按日支付违约金")
    payload = state(
        [unchanged, changed],
        mode=INCREMENTAL_MODE,
        incremental=IncrementalInfo(mode=INCREMENTAL_MODE),
        targets={EXPERTS[0].name: ["c-2"]},
    )
    cfg: RunnableConfig = {
        "configurable": {
            "emitter": EventEmitter(),
            "agent_factory": lambda spec: None,
        }
    }

    async def _run() -> dict[str, Any]:
        return await serial_review(payload, cfg)  # type: ignore[arg-type]

    out = asyncio.run(_run())
    reviewed = {f.clause_id for f in out["findings"]}
    assert "c-1" not in reviewed, "串行路径也不得重审未变更条款"


# ---- stale 的下游处置 ----


def test_stale_findings_are_excluded_from_report_and_approval() -> None:
    findings = [
        finding("c-1"),
        finding("c-2", risk_level="high", stale=True),
    ]
    assert [f.clause_id for f in active_findings(findings)] == ["c-1"]
    assert [f.clause_id for f in render_active_findings(findings)] == ["c-1"]
    # stale 的高风险不进人工审批：审批一条不存在的条款既无意义又会阻塞整轮。
    assert [f.clause_id for f in high_risk_findings(findings)] == ["c-1"]


def test_cache_store_writes_findings_and_baseline_but_skips_stale() -> None:
    clauses = [clause("c-1", "第一条", "买方应在验收合格后30日内支付货款")]
    store = InMemoryIncrementalStore()

    async def _run() -> None:
        payload = state(
            clauses,
            mode="full",
            version=3,
            findings=[
                finding("c-1", agent=EXPERTS[0].agent_name),
                finding("c-9", agent=EXPERTS[0].agent_name, stale=True),
            ],
        )
        await cache_store(payload, config(store))  # type: ignore[arg-type]

    asyncio.run(_run())
    key = cache_key(
        clause_fingerprint(clauses[0]), EXPERTS[0].prompt_version, MODEL, EXPERTS[0].name
    )
    cached = asyncio.run(store.get_findings(key))
    assert cached is not None and len(cached) == 1
    snapshot = asyncio.run(store.get_baseline("contract-1", 3))
    assert snapshot is not None and [c.clause_id for c in snapshot] == ["c-1"]


def test_group_findings_drops_stale_and_orphans() -> None:
    clauses = [clause("c-1", "第一条", "买方应在验收合格后30日内支付货款")]
    grouped = group_findings(
        [
            finding("c-1", agent=EXPERTS[0].agent_name),
            finding("c-1", agent="ghost_expert"),
            finding("c-9", agent=EXPERTS[0].agent_name, stale=True),
        ],
        clauses,
    )
    assert list(grouped.keys()) == [("c-1", EXPERTS[0].name)]


def test_zero_targets_in_incremental_mode_still_reaches_critic() -> None:
    """一字未改时不能 END：复用的历史 findings 还要过 critic 校验并出报告。"""
    payload = state(
        [clause("c-1", "第一条", "买方应在验收合格后30日内支付货款")],
        mode=INCREMENTAL_MODE,
        incremental=IncrementalInfo(mode=INCREMENTAL_MODE),
        targets={},
    )
    sends, _ = build_sends(payload)
    assert sends == []

    async def _run() -> Any:
        return await dispatch(payload, config(InMemoryIncrementalStore()))  # type: ignore[arg-type]

    # 增量零目标 -> 汇入 critic；全量零目标 -> END（两者的语义差异落在这里）。
    assert asyncio.run(_run()).goto == "critic"
    assert asyncio.run(_run_full_zero_target()).goto == END


async def _run_full_zero_target() -> Any:
    # 全量 + 无条款（空合同）：真的没有可审对象，才收尾 END。
    payload = state([], mode="full")
    return await dispatch(payload, config(InMemoryIncrementalStore()))  # type: ignore[arg-type]


def test_cache_stats_accumulates_per_expert_clause() -> None:
    stats = CacheStats()
    stats.hits += 2
    stats.misses += 2
    assert stats.hit_rate == 0.5
    assert AgentSettings().llm_model  # 键里的 model 维度取自配置，非硬编码
