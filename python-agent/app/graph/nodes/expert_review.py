"""expert_review 节点：执行单个专家分片（LangGraph Send fan-out 的汇聚单元）。

同一超步内多个分片并发调用本节点，每次只读自己的 review_task 载荷（Send 隔离）。
三层机制在此叠加：
1. 并发控制——调用方注入的 asyncio.Semaphore（config.configurable["limiter"]）在
   模型调用期间持锁，跨分片共享，把整轮 run 的在途模型调用压到 CONCURRENCY_LIMIT；
2. 预算控制——执行前检查：低优先级分片在预算超限时跳过（high 必审），执行后把
   实测 tokens/cost 经 merge_budget reducer 累加进 budget；
3. 失败语义——单分片异常不抛给 LangGraph（会炸掉整轮 run），而是捕获后发
   node.failed、置 degraded=true，其余分片继续（任务禁止吞异常但禁止扩散）。

节点级耗时埋点：node.started/node.finished 成对发射，duration_ms + tokens 落在
node.finished 上，供并行/串行对照与可观测性消费。
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from langchain_core.runnables import RunnableConfig

from app.agents.registry import get_expert
from app.agents.settings import get_agent_settings
from app.agents.spec import build_expert_agent
from app.events import EventType, get_emitter
from app.graph.nodes.review import review_expert_shard
from app.graph.state import (
    ReviewCounters,
    ReviewState,
    ReviewTask,
    budget_delta,
    budget_exhausted,
)

logger = logging.getLogger("app.graph.expert_review")

# 单 run 内在途模型调用上限；运行时限流器由编排层按 run 创建并注入 config
#（不用模块级全局变量，铁律 4：运行态只走 config/ReviewState）。
CONCURRENCY_LIMIT = 5

AgentFactory = Any  # (ExpertSpec) -> agent；测试注入 scripted 工厂，生产注入缓存工厂


def get_limiter(config: RunnableConfig) -> asyncio.Semaphore:
    """从 config 取本 run 的并发信号量；缺失即显式失败，不静默退化为不限流。"""
    configurable = (config or {}).get("configurable") or {}
    limiter = configurable.get("limiter")
    if not isinstance(limiter, asyncio.Semaphore):
        raise RuntimeError(
            "asyncio.Semaphore 未注入 config.configurable['limiter']，"
            "并行分片无法限流（编排层应按 run 创建并注入）"
        )
    return limiter


def get_agent_factory(config: RunnableConfig) -> AgentFactory:
    """取 agent 工厂：缺省为按 spec 现建（单测/轻量场景），生产注入带缓存的工厂。"""
    configurable = (config or {}).get("configurable") or {}
    factory = configurable.get("agent_factory")
    return factory if factory is not None else build_expert_agent


def _counters_of(clauses: Any, outcomes: list[Any], metrics: Any) -> ReviewCounters:
    """把本分片的条款级成败折成 ReviewCounters。

    三类结果必须分开，否则「未审」会被算错：
    - clauses_ok > 0：审成（ok_clause_ids）；
    - clauses_reviewed = 1 且 clauses_ok = 0：有调用但没拿到可用结论（failed_clause_ids）；
    - clauses_skipped = 1：范畴外跳过，既不算审成也不算失败（不记 ID）。
    """
    ok_ids: list[str] = []
    failed_ids: list[str] = []
    for clause, outcome in zip(clauses, outcomes):
        clause_metrics = outcome.metrics
        if clause_metrics.clauses_ok:
            ok_ids.append(clause.clause_id)
        elif clause_metrics.clauses_reviewed:
            failed_ids.append(clause.clause_id)
    return ReviewCounters(
        clauses_attempted=metrics.clauses_reviewed,
        clauses_ok=metrics.clauses_ok,
        llm_errors=metrics.llm_errors,
        parse_errors=metrics.parse_errors,
        ok_clause_ids=ok_ids,
        failed_clause_ids=failed_ids,
    )


async def expert_review(state: ReviewState, config: RunnableConfig) -> dict[str, object]:
    """图节点：审查一个专家分片；返回 findings/budget 增量/degraded。"""
    task: ReviewTask = state["review_task"]
    spec = get_expert(task.expert)
    emitter = get_emitter(config)
    # 装配期依赖（信号量/工厂）在 try 外获取：缺失即显式失败——这是编排层接线错误，
    # 不是专家失败，不能吞成 node.failed（否则并发限制被静默绕过）。
    limiter = get_limiter(config)
    factory = get_agent_factory(config)
    node_name = f"review.{spec.name}"

    # 前置预算门控：仅低优先级受约束——预算在执行过程中被高优先级分支耗尽时，
    # 尚未开工的低优先级分片在此跳过，保证 degraded 语义不依赖分支完成时序。
    if task.priority == "low" and budget_exhausted(state["budget"]):
        logger.info(
            "预算超限，跳过低优先级分片 %s#%d（spent_tokens=%d）",
            spec.name,
            task.shard_index,
            state["budget"].spent_tokens,
        )
        return {"degraded": True}

    await emitter.emit(EventType.NODE_STARTED, node=node_name, agent=spec.agent_name)
    started = time.monotonic()
    outcomes: list[Any] = []
    try:
        agent = factory(spec)
        contract_meta = {"contract_id": state["contract_id"], "version": state["version"]}
        findings, metrics = await review_expert_shard(
            spec,
            task.clauses,
            contract_meta,
            agent=agent,
            limiter=limiter,
            # 打回轮载荷携带重审指令（Send 节点不见父状态，只能随载荷传入）；
            # 普通派发该字典为空，行为与 Stage 7 完全一致。
            rework_hints=task.rework_hints,
            # 旁路收集每条的成败：降级提示要按「条款」而不是「单元」报数。
            clause_outcomes=outcomes,
        )
    except Exception as exc:
        # 捕获而不扩散：发 node.failed（分支中止语义）、置 degraded，其余分片继续。
        # 异常文本截断入事件，完整堆栈进日志——事件流禁止携带敏感信息。
        await emitter.emit(
            EventType.NODE_FAILED,
            node=node_name,
            agent=spec.agent_name,
            code="EXPERT_REVIEW_FAILED",
            message=str(exc)[:200],
        )
        logger.exception("分片 %s#%d 执行失败，其余分支继续", spec.name, task.shard_index)
        # 整片没跑起来：本片条款一律算未审成（不记 llm_errors —— 那不是模型调用失败，
        # 记进去会把「装配错误」说成「模型不可用」）。全部分片都如此时 model_gate 判死。
        return {
            "degraded": True,
            "review_counters": ReviewCounters(
                clauses_attempted=len(task.clauses),
                failed_clause_ids=[clause.clause_id for clause in task.clauses],
            ),
        }

    duration_ms = int((time.monotonic() - started) * 1000)
    total_tokens = metrics.input_tokens + metrics.output_tokens
    for finding in findings:
        await emitter.emit(
            EventType.MESSAGE_DELTA, agent=spec.agent_name, text=finding.summary
        )
    await emitter.emit(
        EventType.NODE_FINISHED,
        node=node_name,
        duration_ms=duration_ms,
        tokens=total_tokens,
    )
    settings = get_agent_settings()
    cost = settings.cost_cny(metrics.input_tokens, metrics.output_tokens)
    logger.info(
        "分片 %s#%d/%d 完成: clauses=%d findings=%d tokens=%d cost_cny=%.4f duration_ms=%d",
        spec.name,
        task.shard_index,
        task.shard_total,
        metrics.clauses_reviewed,
        len(findings),
        total_tokens,
        cost,
        duration_ms,
    )
    # 后置记账：只写自己的消耗增量，累加由 merge_budget 完成（不读-改-写整个预算）。
    # reviewed_pairs 只记「审成」的 (专家, 条款)：缓存要靠它把「审过且无问题」也记成结论，
    # 否则正常条款每次增量都重审、成本降不下来（Stage 11）。但 llm/parse 失败的条款
    # 绝不能记进来——把「没审」写成「审过没问题」比漏记更糟（下次增量命中空缓存直接漏检）。
    counters = _counters_of(task.clauses, outcomes, metrics)
    return {
        "findings": findings,
        "budget": budget_delta(metrics.input_tokens, metrics.output_tokens, cost),
        "reviewed_pairs": [
            f"{spec.name}|{clause_id}" for clause_id in counters.ok_clause_ids
        ],
        "review_counters": counters,
    }
