"""serial_review 节点：串行审查全部专家（execution_mode=serial 的对照与兜底路径）。

与并行拓扑（dispatch + Send fan-out）跑完全相同的引擎与事件形状，区别只在
执行序：专家按注册序逐个审、条款逐条审，无 gather、无信号量——用于
1. 验收对照：同一 50 条款合同，并行 vs 串行的 P95 下降幅度；
2. 降级兜底：并行拓扑异常时可用开关整体切回串行，行为不变。

预算控制在节点内本地跟踪（state 在单次调用内只读一次）：每审完一个专家就把
消耗并进本地预算副本，后续专家据此判断是否跳过低优先级；最终只把总增量写回
state（merge_budget 累加），避免与并发分支互相覆盖。

Stage 8 起重审通道：state["rework"] 非空表示这是 Critic 打回后的重审轮——
只审查指令中的（专家，条款）并注入修正指令，其余逻辑（事件/记账/降级）与
常规轮完全一致，保证串行模式下打回-重审-再汇合的形状不退化。

Stage 11：增量模式下待审条款同样由 review_targets 收窄（复用 dispatch 的同口径函数），
保证串行与并行两条拓扑在「只花 5% 的钱」上行为一致，不出现「串行偷偷全量审」。
"""

from __future__ import annotations

import logging
import time

from langchain_core.runnables import RunnableConfig

from app.agents.registry import EXPERTS, get_expert
from app.agents.settings import get_agent_settings
from app.events import EventType, get_emitter
from app.graph.nodes.critic import build_rework_instruction
from app.graph.nodes.dispatch import incremental_targets, scoped_clauses
from app.graph.nodes.expert_review import get_agent_factory
from app.graph.nodes.review import review_clause
from app.graph.state import (
    Budget,
    Finding,
    ReviewState,
    ReworkInstruction,
    budget_delta,
    budget_exhausted,
    merge_budget,
)

logger = logging.getLogger("app.graph.serial_review")

NODE_NAME = "serial_review"


async def _rework_pass(
    state: ReviewState, config: RunnableConfig, instructions: list[ReworkInstruction]
) -> dict[str, object]:
    """Critic 打回后的串行重审轮：只审指令中的（专家，条款），注入修正指令。"""
    emitter = get_emitter(config)
    factory = get_agent_factory(config)
    contract_meta = {"contract_id": state["contract_id"], "version": state["version"]}
    clauses_by_id = {clause.clause_id: clause for clause in state["clauses"]}
    findings: list[Finding] = []
    total_in = total_out = 0
    total_cost = 0.0

    # 按专家聚合：同一专家的多条指令合并为一次节点起止（事件形状与常规轮一致）。
    by_expert: dict[str, list[ReworkInstruction]] = {}
    for instruction in instructions:
        by_expert.setdefault(instruction.expert, []).append(instruction)

    for expert_name, expert_instructions in by_expert.items():
        spec = get_expert(expert_name)
        retry_hint = spec.load_retry_hint()
        await emitter.emit(
            EventType.NODE_STARTED, node=f"review.{spec.name}", agent=spec.agent_name
        )
        expert_started = time.monotonic()
        expert_in = expert_out = 0
        for instruction in expert_instructions:
            clause = clauses_by_id.get(instruction.clause_id)
            if clause is None:
                # 条款文本缺失：无法重审也无法校验，跳过即该 finding 保持已删除状态。
                logger.warning(
                    "重审指令指向的条款 %s 不在 state.clauses，跳过", instruction.clause_id
                )
                continue
            outcome = await review_clause(
                spec,
                clause,
                contract_meta,
                agent=factory(spec),
                retry_hint=retry_hint,
                rework_hint=build_rework_instruction(instruction.clause_id, instruction.reasons),
            )
            findings.extend(outcome.findings)
            expert_in += outcome.metrics.input_tokens
            expert_out += outcome.metrics.output_tokens
        settings = get_agent_settings()
        expert_cost = settings.cost_cny(expert_in, expert_out)
        await emitter.emit(
            EventType.NODE_FINISHED,
            node=f"review.{spec.name}",
            duration_ms=int((time.monotonic() - expert_started) * 1000),
            tokens=expert_in + expert_out,
        )
        total_in += expert_in
        total_out += expert_out
        total_cost += expert_cost

    logger.info(
        "%s 重审轮完成: instructions=%d findings=%d tokens=%d cost_cny=%.4f",
        NODE_NAME,
        len(instructions),
        len(findings),
        total_in + total_out,
        total_cost,
    )
    return {
        "findings": findings,
        "budget": budget_delta(total_in, total_out, total_cost),
    }


async def serial_review(state: ReviewState, config: RunnableConfig) -> dict[str, object]:
    """图节点：串行审查全部专家；返回 findings/budget 增量/degraded。

    state["rework"] 非空 = Critic 打回轮：只重审指令条款并尽早返回，不再全量审查。
    """
    instructions = list(state.get("rework") or [])
    if instructions:
        logger.info("%s 收到 Critic 打回指令 %d 条，进入重审轮", NODE_NAME, len(instructions))
        return await _rework_pass(state, config, instructions)

    emitter = get_emitter(config)
    factory = get_agent_factory(config)
    await emitter.emit(EventType.NODE_STARTED, node=NODE_NAME, agent=NODE_NAME)
    started = time.monotonic()
    contract_meta = {"contract_id": state["contract_id"], "version": state["version"]}

    running: Budget = state["budget"]
    findings: list[Finding] = []
    degraded = False
    total_in = total_out = 0
    total_cost = 0.0
    targets = incremental_targets(state)
    reviewed: list[str] = []

    for spec in EXPERTS:
        scoped = scoped_clauses(state, spec, targets)
        reviewed.extend(f"{spec.name}|{clause.clause_id}" for clause in scoped)
        if not scoped:
            continue
        if spec.priority == "low" and budget_exhausted(running):
            logger.warning(
                "预算超限，串行路径跳过低优先级专家 %s（spent_tokens=%d）",
                spec.name,
                running.spent_tokens,
            )
            degraded = True
            continue
        await emitter.emit(
            EventType.NODE_STARTED, node=f"review.{spec.name}", agent=spec.agent_name
        )
        expert_started = time.monotonic()
        retry_hint = spec.load_retry_hint()
        expert_findings: list[Finding] = []
        expert_in = expert_out = 0
        for clause in scoped:
            outcome = await review_clause(
                spec, clause, contract_meta, agent=factory(spec), retry_hint=retry_hint
            )
            expert_findings.extend(outcome.findings)
            expert_in += outcome.metrics.input_tokens
            expert_out += outcome.metrics.output_tokens
        settings = get_agent_settings()
        expert_cost = settings.cost_cny(expert_in, expert_out)
        await emitter.emit(
            EventType.MESSAGE_DELTA,
            agent=spec.agent_name,
            text=f"{spec.name} 审查完成，发现 {len(expert_findings)} 条",
        )
        await emitter.emit(
            EventType.NODE_FINISHED,
            node=f"review.{spec.name}",
            duration_ms=int((time.monotonic() - expert_started) * 1000),
            tokens=expert_in + expert_out,
        )
        findings.extend(expert_findings)
        total_in += expert_in
        total_out += expert_out
        total_cost += expert_cost
        running = merge_budget(
            running,
            budget_delta(expert_in, expert_out, expert_cost),
        )

    duration_ms = int((time.monotonic() - started) * 1000)
    await emitter.emit(
        EventType.NODE_FINISHED,
        node=NODE_NAME,
        duration_ms=duration_ms,
        tokens=total_in + total_out,
    )
    logger.info(
        "%s 节点完成: findings=%d tokens=%d cost_cny=%.4f degraded=%s duration_ms=%d",
        NODE_NAME,
        len(findings),
        total_in + total_out,
        total_cost,
        degraded,
        duration_ms,
    )
    # reviewed_pairs 与并行拓扑同口径：串行也记录实际审过的 (专家, 条款)，
    # 保证两条拓扑的缓存覆盖率一致（Stage 11，详见 expert_review 同名说明）。
    return {
        "findings": findings,
        "budget": budget_delta(total_in, total_out, total_cost),
        "degraded": degraded,
        "reviewed_pairs": reviewed,
    }
