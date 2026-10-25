"""diff 节点：增量重审的计划点（Stage 11）——决定「这轮到底重审哪些条款」。

为什么单独一个节点而不是塞进 dispatch：派发只回答「怎么切片」，diff 回答「审谁」，
两者混在一起会让「只花 5% 的钱」这件事既无法单测也无法审计。本节点一次算定四件事：
1. 拉基线（注入优先，其次按 base_version 从缓存后端取快照）并算出 changed/added/removed；
2. 对未变更条款按（专家 × 条款）查缓存：命中即复用（不花 token），未命中降级为重审；
3. removed 条款的历史 findings 从缓存取回并标 stale=true——保留可追溯，但不进报告；
4. 产出按专家划分的 review_targets，供 dispatch/serial_review 收窄 fan-out。

全量模式（review_mode=full 或无基线）刻意不查缓存：全量是成本对比的基线语义，
若也复用缓存，增量与全量的 token 对比就失去意义（验收口径自相矛盾）。

纯 IO 只有缓存读写；判定逻辑（diff/键）全在 app/incremental，节点只做编排。
"""

from __future__ import annotations

import logging
import time

from langchain_core.runnables import RunnableConfig

from app.agents.registry import EXPERTS
from app.agents.settings import get_agent_settings
from app.events import EventType, get_emitter
from app.graph.state import (
    FULL_MODE,
    INCREMENTAL_MODE,
    Clause,
    Finding,
    IncrementalInfo,
    ReviewState,
)
from app.incremental.cache import CacheStats, cache_key, get_store
from app.incremental.diff import clause_fingerprint, diff_clauses

logger = logging.getLogger("app.graph.diff")

NODE_NAME = "diff"


def full_targets(clauses: list[Clause]) -> dict[str, list[str]]:
    """全量模式的目标集：每个专家各自范畴内的全部条款（与 Stage 7 的派发口径一致）。"""
    targets: dict[str, list[str]] = {}
    for spec in EXPERTS:
        selected = [clause.clause_id for clause in clauses if spec.in_scope(clause.text)]
        if selected:
            targets[spec.name] = selected
    return targets


async def diff_node(state: ReviewState, config: RunnableConfig) -> dict[str, object]:
    """图节点：算 diff、复用缓存、标记 stale，并给出按专家的待审目标。"""
    emitter = get_emitter(config)
    await emitter.emit(EventType.NODE_STARTED, node=NODE_NAME, agent=NODE_NAME)
    started = time.monotonic()
    store = get_store(config)
    clauses: list[Clause] = list(state.get("clauses") or [])
    mode = str(state.get("review_mode") or FULL_MODE) or FULL_MODE
    version = int(state.get("version") or 0)
    base_version = int(state.get("base_version") or 0) or max(version - 1, 0)

    baseline: list[Clause] = list(state.get("baseline") or [])
    if mode == INCREMENTAL_MODE and not baseline and base_version > 0:
        snapshot = await store.get_baseline(str(state.get("contract_id") or ""), base_version)
        baseline = list(snapshot or [])

    reused: list[Finding] = []
    stale: list[Finding] = []
    if mode != INCREMENTAL_MODE or not baseline:
        # 无基线可比（首版/未请求增量）：全量重审，不查缓存。
        info = IncrementalInfo(mode=FULL_MODE, unchanged_count=0)
        targets = full_targets(clauses)
    else:
        delta = diff_clauses(baseline, clauses)
        target_ids = {*delta.changed, *delta.added}
        stats = CacheStats()
        targets = await _incremental_targets(clauses, target_ids, stats, reused, config=config)
        await _mark_removed_stale(delta.removed, baseline, stale, config=config)
        info = IncrementalInfo(
            mode=INCREMENTAL_MODE,
            baseline_version=base_version,
            changed_count=delta.changed_count,
            added_count=delta.added_count,
            removed_count=delta.removed_count,
            unchanged_count=delta.unchanged_count,
            reused_findings=len(reused),
            stale_findings=len(stale),
            cache_hits=stats.hits,
            cache_misses=stats.misses,
            cache_hit_rate=round(stats.hit_rate, 4),
            changed_clause_ids=list(delta.changed),
            added_clause_ids=list(delta.added),
            removed_clause_ids=list(delta.removed),
        )

    duration_ms = int((time.monotonic() - started) * 1000)
    await emitter.emit(
        EventType.MESSAGE_DELTA,
        agent=NODE_NAME,
        text=(
            f"{NODE_NAME}: mode={info.mode} changed={info.changed_count} "
            f"added={info.added_count} removed={info.removed_count} "
            f"reused={info.reused_findings} stale={info.stale_findings} "
            f"cache_hit_rate={info.cache_hit_rate:.2f}"
        ),
    )
    await emitter.emit(
        EventType.NODE_FINISHED, node=NODE_NAME, duration_ms=duration_ms, tokens=0
    )
    logger.info(
        "%s 节点完成: mode=%s targets=%d reused=%d stale=%d duration_ms=%d",
        NODE_NAME,
        info.mode,
        sum(len(ids) for ids in targets.values()),
        len(reused),
        len(stale),
        duration_ms,
    )
    return {
        "findings": [*reused, *stale],
        "review_targets": targets,
        "incremental": info,
    }


async def _incremental_targets(
    clauses: list[Clause],
    target_ids: set[str],
    stats: CacheStats,
    reused: list[Finding],
    *,
    config: RunnableConfig,
) -> dict[str, list[str]]:
    """按专家收窄待审条款：未变更条款查缓存，命中即复用、未命中才重审。"""
    settings = get_agent_settings()
    store = get_store(config)
    targets: dict[str, list[str]] = {}
    for spec in EXPERTS:
        selected: list[str] = []
        for clause in clauses:
            if not spec.in_scope(clause.text):
                continue
            if clause.clause_id in target_ids:
                selected.append(clause.clause_id)
                continue
            cached = await store.get_findings(
                cache_key(
                    clause_fingerprint(clause), spec.prompt_version, settings.llm_model, spec.name
                )
            )
            if cached is None:
                stats.misses += 1
                selected.append(clause.clause_id)
            else:
                stats.hits += 1
                # 缓存里的 clause_id 是旧版本的 ID（切分序号会随增删错位），复用时必须
                # 改指当前版本 ID，否则批注锚点与人工审批都会打到错条款上。
                reused.extend(
                    finding.model_copy(update={"clause_id": clause.clause_id})
                    for finding in cached
                )
        if selected:
            targets[spec.name] = selected
    return targets


async def _mark_removed_stale(
    removed_ids: list[str],
    baseline: list[Clause],
    stale: list[Finding],
    *,
    config: RunnableConfig,
) -> None:
    """removed 条款的历史 findings 标 stale 保留：不静默丢弃，可追溯但不上报告。"""
    if not removed_ids:
        return
    settings = get_agent_settings()
    store = get_store(config)
    baseline_by_id = {clause.clause_id: clause for clause in baseline}
    for clause_id in removed_ids:
        base = baseline_by_id.get(clause_id)
        if base is None:
            continue
        digest = clause_fingerprint(base)
        for spec in EXPERTS:
            cached = await store.get_findings(
                cache_key(digest, spec.prompt_version, settings.llm_model, spec.name)
            )
            for finding in cached or []:
                stale.append(finding.model_copy(update={"stale": True}))
