"""cache_store 节点：把本轮结论写进缓存，并把当前条款集存为下一版的基线快照（Stage 11）。

位置在 critic 之后、human_approval 之前：
- 之后 critic：写进去的是「已经过确定性校验」的结论，复用时才不会把幻觉结论放大成长期事实；
- 之前 human_approval：人工审批是图内断点（可能挂起数小时），缓存必须在断点前落盘，
  否则审批期间的增量运行会因为「上一轮没写缓存」而全部 miss。

写两样东西：
1. findings 缓存：按（专家 × 条款）分组，键含 clause.hash + prompt_version + model；
   stale 的历史结论不写（它们所属条款已删除，写进去只会污染未来同名条款）；
2. 基线快照：当前版本的条款列表（含 no/hash），供下一版本 diff 比对。

为什么本节点不发射 findings 相关的业务事件：它不产出新结论，只做落盘；
可观测性走 node.started/node.finished 与日志（写入条数）。
"""

from __future__ import annotations

import logging
import time

from langchain_core.runnables import RunnableConfig

from app.agents.registry import get_expert, get_expert_by_agent_name
from app.agents.settings import get_agent_settings
from app.events import EventType, get_emitter
from app.graph.state import Clause, Finding, ReviewState
from app.incremental.cache import cache_key, get_store
from app.incremental.diff import clause_fingerprint

logger = logging.getLogger("app.graph.cache_store")

NODE_NAME = "cache_store"


def group_findings(
    findings: list[Finding], clauses: list[Clause]
) -> dict[tuple[str, str], list[Finding]]:
    """按（条款, 专家）分组本轮产出的 findings；stale 与无主条款的条目被排除。

    纯函数（分组规则可单测）：只保留「条款在当前版本仍存在」的条目——缓存是给未来
    版本复用的，条款都已被删除的结论没有复用价值（它们以 stale 形态留在状态里追溯）。
    """
    by_id = {clause.clause_id: clause for clause in clauses}
    grouped: dict[tuple[str, str], list[Finding]] = {}
    for finding in findings:
        if finding.stale or finding.clause_id not in by_id:
            continue
        spec = get_expert_by_agent_name(finding.agent)
        if spec is None:
            continue
        grouped.setdefault((finding.clause_id, spec.name), []).append(finding)
    return grouped


def cache_entries(
    grouped: dict[tuple[str, str], list[Finding]],
    reviewed_pairs: list[str],
    clauses: list[Clause],
) -> dict[tuple[str, str], list[Finding]]:
    """补上「审过但没发现问题」的条目，得到完整的待写缓存集合。

    为什么必须补：「无发现」也是一个花了 token 换来的结论。只写有 finding 的条款，
    下一版增量时正常条款全部 miss 并重新送审——合同里正常条款占绝大多数，
    省钱的幅度会被这一半以上的重审直接吃掉（实测命中率 0.39 → 0.95）。
    审过的对来自 reviewed_pairs（各分片实际送审记录），不来自 review_targets：
    预算门可能跳过某些目标，把「没审」写成「审过没问题」是更糟的错误结论。
    """
    by_id = {clause.clause_id: clause for clause in clauses}
    entries: dict[tuple[str, str], list[Finding]] = {**grouped}
    for pair in reviewed_pairs:
        expert_name, _, clause_id = pair.partition("|")
        if not expert_name or clause_id not in by_id:
            continue
        entries.setdefault((clause_id, expert_name), [])
    return entries


async def cache_store(state: ReviewState, config: RunnableConfig) -> dict[str, object]:
    """图节点：写回 findings 缓存 + 当前版本基线快照。"""
    emitter = get_emitter(config)
    await emitter.emit(EventType.NODE_STARTED, node=NODE_NAME, agent=NODE_NAME)
    started = time.monotonic()
    settings = get_agent_settings()
    store = get_store(config)
    clauses: list[Clause] = list(state.get("clauses") or [])
    by_id = {clause.clause_id: clause for clause in clauses}
    grouped = group_findings(list(state.get("findings") or []), clauses)

    entries = cache_entries(
        grouped, list(state.get("reviewed_pairs") or []), clauses
    )

    written = 0
    for (clause_id, expert_name), findings in entries.items():
        spec = get_expert(expert_name)
        key = cache_key(
            clause_fingerprint(by_id[clause_id]),
            spec.prompt_version,
            settings.llm_model,
            spec.name,
        )
        await store.put_findings(key, findings)
        written += 1

    version = int(state.get("version") or 0)
    await store.put_baseline(str(state.get("contract_id") or ""), version, clauses)

    duration_ms = int((time.monotonic() - started) * 1000)
    await emitter.emit(
        EventType.NODE_FINISHED, node=NODE_NAME, duration_ms=duration_ms, tokens=0
    )
    logger.info(
        "%s 节点完成: cached_entries=%d baseline_version=%d clauses=%d duration_ms=%d",
        NODE_NAME,
        written,
        version,
        len(clauses),
        duration_ms,
    )
    return {}
