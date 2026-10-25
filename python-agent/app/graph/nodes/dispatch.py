"""dispatch 节点：按专家范畴把条款切分为 ≤SHARD_SIZE 条的分片，Send 并行fan-out。

为什么按专家分片而非按条款类型硬编码：每个专家是独立的 clause-type 专家
（registry 定义范畴关键词），dispatch 只做「范围过滤 + 定长分片」，不理解条款
语义——新增专家无需改本节点（开闭原则，也为 Stage 7 的 6 专家并行做准备）。

预算规划门：token 预判不足以养低优先级专家（ip/data/compliance）时，直接不派发
它们的分片并置 degraded=true——在派发前决策而不是等跑到一半再熔断，保证降级
路径确定可复现（不依赖分支完成时序）。high 优先级专家为必审，任何预算下都派发。

Stage 11：增量模式下派发池由 diff 节点算好的 review_targets 收窄——本节点不重新
判断「哪些条款变了」，只按目标集切片，保证「只重审变更」的成本语义单点可审计。
"""

from __future__ import annotations

import logging
import time
from collections.abc import Sequence
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END
from langgraph.types import Command, Send

from app.agents.registry import EXPERTS
from app.agents.spec import ExpertSpec
from app.events import EventType, get_emitter
from app.graph.state import (
    INCREMENTAL_MODE,
    Clause,
    ReviewState,
    ReviewTask,
    budget_exhausted,
)

logger = logging.getLogger("app.graph.dispatch")

NODE_NAME = "dispatch"
SHARD_SIZE = 8

# 规划期每条条款的 token 预估值（两段式模型调用的经验值）：只用于「预算养不养得起
# 低优先级专家」的前置决策，不是计费口径；真实消耗按节点实测经 merge_budget 累加。
PER_CLAUSE_TOKEN_ESTIMATE = 500

def _chunks[T](items: Sequence[T], size: int) -> list[list[T]]:
    return [list(items[i : i + size]) for i in range(0, len(items), size)]


def incremental_targets(state: ReviewState) -> dict[str, list[str]] | None:
    """增量模式的派发目标（专家 -> 条款 ID 列表）；全量模式返回 None（审全部）。

    返回 None 而不返回空字典：空字典在语义上是「这轮什么都不审」，与「没有增量信息、
    按全量处理」必须区分开——否则一条字段缺失就会把整轮审查静默变成空跑。
    """
    info = state.get("incremental")
    if info is not None and info.mode == INCREMENTAL_MODE:
        return dict(state.get("review_targets") or {})
    return None


def planning_pool(state: ReviewState, targets: dict[str, list[str]] | None) -> list[Clause]:
    """预算规划用的条款池：增量模式取各专家目标的并集，全量取全部条款。"""
    if targets is None:
        return list(state["clauses"])
    wanted = {clause_id for ids in targets.values() for clause_id in ids}
    return [clause for clause in state["clauses"] if clause.clause_id in wanted]


def scoped_clauses(
    state: ReviewState, spec: ExpertSpec, targets: dict[str, list[str]] | None
) -> list[Clause]:
    """该专家本轮要审的条款：增量按目标集取（已含范畴过滤），全量按范畴过滤全部。"""
    if targets is not None:
        by_id = {clause.clause_id: clause for clause in state["clauses"]}
        return [by_id[clause_id] for clause_id in targets.get(spec.name, []) if clause_id in by_id]
    return [clause for clause in state["clauses"] if spec.in_scope(clause.text)]


def build_sends(state: ReviewState) -> tuple[list[Send], list[str]]:
    """构造全部专家分片的 Send 列表；返回（sends, 被预算跳过的低优先级专家名）。

    纯函数：只读 state，不碰 emitter/IO——分片策略可单测（tests/agents/test_dispatch.py）。

    载荷必须自带专家分片执行所需的全部通道（本版本 LangGraph 的 Send 节点只见载荷键，
    不见父状态）：review_task 分片本体 + contract_id/version 合同元信息 + budget
    入口预算门控。budget 是 reducer 通道，但载荷写入按任务隔离、不并入终态
    （实测：3 个分片各写父值 100，终态仍是 100 + 各节点增量），故可安全携带。
    """
    budget = state["budget"]
    exhausted = budget_exhausted(budget)
    targets = incremental_targets(state)
    pool = planning_pool(state, targets)
    low_clause_count = 0
    for spec in EXPERTS:
        if spec.priority == "low":
            low_clause_count += sum(1 for clause in pool if spec.in_scope(clause.text))
    # 规划门：剩余预算连低优先级专家的预估成本都覆盖不起 -> 整体跳过（确定性降级）。
    low_affordable = (
        budget.spent_tokens + PER_CLAUSE_TOKEN_ESTIMATE * low_clause_count
        <= budget.max_tokens
    )

    sends: list[Send] = []
    skipped_low: list[str] = []
    for spec in EXPERTS:
        if spec.priority == "low" and (exhausted or not low_affordable):
            skipped_low.append(spec.name)
            continue
        scoped = scoped_clauses(state, spec, targets)
        shards = _chunks(scoped, SHARD_SIZE)
        for shard_index, shard in enumerate(shards):
            sends.append(
                Send(
                    "expert_review",
                    {
                        "review_task": ReviewTask(
                            expert=spec.name,
                            priority=spec.priority,
                            shard_index=shard_index,
                            shard_total=len(shards),
                            clauses=shard,
                        ),
                        "contract_id": state["contract_id"],
                        "version": state["version"],
                        "budget": budget,
                    },
                )
            )
    return sends, skipped_low


async def dispatch(state: ReviewState, config: RunnableConfig) -> Command[Any]:
    """图节点：分片并派发。无任何分片（空合同/全范围外）时干净走 END。"""
    emitter = get_emitter(config)
    await emitter.emit(EventType.NODE_STARTED, node=NODE_NAME, agent=NODE_NAME)
    started = time.monotonic()
    sends, skipped_low = build_sends(state)
    duration_ms = int((time.monotonic() - started) * 1000)
    await emitter.emit(
        EventType.NODE_FINISHED, node=NODE_NAME, duration_ms=duration_ms, tokens=0
    )
    incremental = incremental_targets(state) is not None
    # 增量模式下「零目标」（合同一字未改）不能直接 END：复用的历史 findings 还要过
    # critic 校验并出报告，故汇入 critic；只有全量模式真的没有条款可审才 END。
    goto: list[Send] | str = sends if sends else ("critic" if incremental else END)
    if skipped_low:
        logger.warning(
            "预算不足以覆盖低优先级专家（max_tokens=%s spent=%s），跳过: %s；degraded=true",
            state["budget"].max_tokens,
            state["budget"].spent_tokens,
            skipped_low,
        )
        return Command(goto=goto, update={"degraded": True})
    logger.info(
        "%s: %d 个分片待审（budget spent_tokens=%d）",
        NODE_NAME,
        len(sends),
        state["budget"].spent_tokens,
    )
    return Command(goto=goto)
