"""专家审查引擎：单条款审查闭环（6 个专家共用）+ review.commercial 图节点。

职责：按专家范畴筛选条款 -> 逐条调用专家 Agent -> 解析结构化输出（失败重试 1 次，
仍失败记 parse_error 并返回空列表，不抛异常）-> 引用白名单校验 -> 汇入 findings。

为什么解析失败不裸抛：单条款坏输出不应炸掉整轮 run；节点保持可中断/可恢复（铁律 4），
降级路径显式记数，由后续汇总阶段决策。token 用量按条款记入日志，满足成本可观测要求。

并行拓扑（Stage 7）下本模块是纯引擎：review_expert_shard 用 asyncio.gather 并发审查
一个分片内的条款，模型调用经调用方注入的 asyncio.Semaphore 限流；串行/单专家路径
传 limiter=None 即退化为顺序执行。引擎不持有任何全局状态（铁律 4）。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.runnables import RunnableConfig

from app.agents.commercial import (
    COMMERCIAL_AGENT_NAME,
    COMMERCIAL_SPEC,
    build_clause_message,
    build_commercial_agent,
)
from app.agents.schemas import RawAgentOutput
from app.agents.spec import ExpertSpec
from app.agents.validation import (
    ReviewMetrics,
    enforce_citation_whitelist,
    evidence_is_verbatim,
    extract_whitelist,
)
from app.events import EventType, get_emitter
from app.graph.state import Clause, Finding, ReviewState

logger = logging.getLogger("app.graph.review")

NODE_NAME = "review.commercial"
MAX_PARSE_ATTEMPTS = 2


class AgentOutputParseError(ValueError):
    """Agent 最终输出不符合结构化契约（非 JSON / schema 不符 / clause_id 不回显）。"""


class CommercialReviewOutcome:
    """审查闭环产出：findings 供状态合并，metrics 供日志与验收。"""

    def __init__(self, findings: list[Finding], metrics: ReviewMetrics) -> None:
        self.findings = findings
        self.metrics = metrics


@dataclass
class ClauseOutcome:
    """单条款审查结果：findings 与增量指标分开回传，避免并发写共享 metrics。"""

    findings: list[Finding] = field(default_factory=list)
    metrics: ReviewMetrics = field(default_factory=ReviewMetrics)


def parse_agent_output(content: str, clause_id: str) -> RawAgentOutput:
    """把 Agent 最终消息解析为 RawAgentOutput；任何不符都抛 AgentOutputParseError。

    容忍模型把 JSON 包在 Markdown 围栏里（剥掉再解析），但绝不容忍非 JSON 输出——
    这是「结构化输出解析成功率」指标的判定点。
    """
    text = content.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        lines = [line for line in lines[1:] if not line.strip().startswith("```")]
        text = "\n".join(lines).strip()
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise AgentOutputParseError(f"clause {clause_id}: 输出不是合法 JSON（{exc}）") from exc
    if not isinstance(payload, dict):
        raise AgentOutputParseError(f"clause {clause_id}: 输出不是 JSON 对象")
    try:
        output = RawAgentOutput.model_validate(payload)
    except Exception as exc:  # pydantic ValidationError 及任何结构不符
        raise AgentOutputParseError(f"clause {clause_id}: 输出不符合 schema（{exc}）") from exc
    if output.clause_id != clause_id:
        raise AgentOutputParseError(
            f"clause {clause_id}: 输出 clause_id={output.clause_id!r} 与输入不一致"
        )
    return output


def _final_content(result: Any) -> str:
    messages: list[BaseMessage] = list(result.get("messages") or [])
    for message in reversed(messages):
        if isinstance(message, AIMessage):
            return message.text
    return ""


def _usage_of(result: Any) -> tuple[int, int]:
    """汇总本次 Agent 运行全部 AIMessage 的 usage（输入/输出 token）。"""
    input_tokens = 0
    output_tokens = 0
    for message in result.get("messages") or []:
        if isinstance(message, AIMessage) and message.usage_metadata:
            input_tokens += int(message.usage_metadata.get("input_tokens") or 0)
            output_tokens += int(message.usage_metadata.get("output_tokens") or 0)
    return input_tokens, output_tokens


async def _invoke_agent(
    agent: Any,
    clause: Clause,
    contract_meta: dict[str, Any],
    spec: ExpertSpec,
    retry_hint: str | None,
    limiter: asyncio.Semaphore | None,
    rework_hint: str | None = None,
) -> tuple[str, list[BaseMessage], int, int]:
    """调用一次专家 Agent，返回（最终输出文本, 全量消息, 输入 token, 输出 token）。

    limiter 非空时在模型调用期间持锁——并行拓扑下跨分片共享同一个信号量，
    把在途模型调用压到 CONCURRENCY_LIMIT 以内；单专家/串行路径不传即无限流。

    rework_hint 与 retry_hint 的分工：rework_hint 是 Critic 打回时的修正指令，
    首次调用即注入（告诉模型上一次为什么没通过校验）；retry_hint 是解析失败的
    纠正指令，只在重试轮注入。两者可叠加。
    """
    message = build_clause_message(
        clause, contract_meta, agent_name=spec.agent_name, scope_note=spec.scope_note
    )
    if rework_hint:
        message = f"{message}\n\n{rework_hint}"
    if retry_hint:
        message = f"{message}\n\n{retry_hint}"
    if limiter is None:
        result = await agent.ainvoke({"messages": [message]})
    else:
        async with limiter:
            result = await agent.ainvoke({"messages": [message]})
    input_tokens, output_tokens = _usage_of(result)
    return _final_content(result), list(result.get("messages") or []), input_tokens, output_tokens


async def review_clause(
    spec: ExpertSpec,
    clause: Clause,
    contract_meta: dict[str, Any],
    *,
    agent: Any,
    retry_hint: str,
    limiter: asyncio.Semaphore | None = None,
    rework_hint: str | None = None,
) -> ClauseOutcome:
    """审查单条条款：范围过滤 -> 重试解析 -> 白名单校验，异常全部内部消化。

    永不抛异常是本函数的契约：调用方（并行 gather / 串行循环 / 图节点）不需要
    为单条款失败写 try；真正的意外（如 agent 组装失败）由上层节点捕获并走
    node.failed 语义。

    rework_hint 非空表示这是 Critic 打回后的重审：修正指令在首次调用即注入，
    模型输出的仍是完整 finding 集合——旧版本已由 Critic 用墓碑删除，此处产出
    即以干净替换语义进入终态。
    """
    outcome = ClauseOutcome()
    if not spec.in_scope(clause.text):
        outcome.metrics.clauses_skipped = 1
        logger.info("clause %s 非 %s 范畴，跳过", clause.clause_id, spec.name)
        return outcome
    outcome.metrics.clauses_reviewed = 1
    started = time.monotonic()
    try:
        parsed = None
        messages: list[BaseMessage] = []
        input_tokens = output_tokens = 0
        for attempt in range(1, MAX_PARSE_ATTEMPTS + 1):
            content, messages, in_tokens, out_tokens = await _invoke_agent(
                agent,
                clause,
                contract_meta,
                spec,
                retry_hint if attempt > 1 else None,
                limiter,
                rework_hint,
            )
            input_tokens += in_tokens
            output_tokens += out_tokens
            try:
                parsed = parse_agent_output(content, clause.clause_id)
                break
            except AgentOutputParseError:
                logger.warning(
                    "clause %s 第 %d/%d 次输出解析失败",
                    clause.clause_id,
                    attempt,
                    MAX_PARSE_ATTEMPTS,
                )
        if parsed is None:
            outcome.metrics.parse_errors = 1
            logger.error(
                "clause %s 连续 %d 次解析失败，记 parse_error 返回空列表",
                clause.clause_id,
                MAX_PARSE_ATTEMPTS,
            )
            return outcome
    except Exception:
        # Agent 调用本身失败（网络/超时/模型 5xx）：记 llm_errors 后跳过该条款，
        # 不让单条款失败炸掉整轮 run。
        outcome.metrics.llm_errors = 1
        logger.exception("clause %s Agent 调用失败，跳过该条款", clause.clause_id)
        return outcome

    outcome.metrics.input_tokens = input_tokens
    outcome.metrics.output_tokens = output_tokens
    # 只有解析出符合契约的结论才算「审成」：此处之前的任何 return 都是未审成
    # （llm_errors / parse_errors），model_gate 依据 clauses_ok 判定全灭。
    outcome.metrics.clauses_ok = 1
    whitelist = extract_whitelist(messages)
    clause_dropped = 0
    for raw_finding in parsed.findings:
        finding, dropped = enforce_citation_whitelist(
            raw_finding, whitelist, agent=spec.agent_name
        )
        clause_dropped += dropped
        outcome.metrics.dropped_citations += dropped
        if finding.risk_level == "unverified":
            outcome.metrics.unverified_findings += 1
        if not evidence_is_verbatim(clause.text, raw_finding.evidence):
            outcome.metrics.evidence_mismatches += 1
            logger.warning(
                "clause %s evidence 非条款原文逐字片段: %r",
                clause.clause_id,
                raw_finding.evidence,
            )
        outcome.metrics.findings_emitted += 1
        outcome.findings.append(finding)
    duration_ms = int((time.monotonic() - started) * 1000)
    logger.info(
        "clause %s 审查完成: findings=%d dropped_citations=%d "
        "input_tokens=%d output_tokens=%d duration_ms=%d",
        clause.clause_id,
        len(parsed.findings),
        clause_dropped,
        input_tokens,
        output_tokens,
        duration_ms,
    )
    return outcome


async def review_expert_shard(
    spec: ExpertSpec,
    clauses: Sequence[Clause],
    contract_meta: dict[str, Any],
    *,
    agent: Any,
    metrics: ReviewMetrics | None = None,
    limiter: asyncio.Semaphore | None = None,
    rework_hints: dict[str, str] | None = None,
    clause_outcomes: list[ClauseOutcome] | None = None,
) -> tuple[list[Finding], ReviewMetrics]:
    """并发审查一个分片内的条款（asyncio.gather，模型调用经 limiter 限流）。

    分片内条款并发、跨分片由 LangGraph 并行：两层并发叠加后，整轮 run 的在途
    模型调用上限 = limiter 的值（CONCURRENCY_LIMIT）。指标在 gather 结束后顺序
    合并，杜绝并发写同一 metrics 对象。

    rework_hints：clause_id -> Critic 重审指令；命中条款首次调用即注入修正指令
    （Stage 8 打回重审），未命中条款走常规重试指令。

    clause_outcomes：可选的旁路收集器，按入参 clauses 同序回填每条的 ClauseOutcome。
    为什么用旁路而不是改返回值：调用方需要「哪一条没审成」（降级提示按条款计），
    而返回二元组已被 6 处测试锁定；旁路保持既有契约不变。
    """
    accumulated = metrics if metrics is not None else ReviewMetrics()
    retry_hint = spec.load_retry_hint()
    hints = rework_hints or {}
    outcomes = await asyncio.gather(
        *(
            review_clause(
                spec,
                clause,
                contract_meta,
                agent=agent,
                retry_hint=retry_hint,
                limiter=limiter,
                rework_hint=hints.get(clause.clause_id),
            )
            for clause in clauses
        )
    )
    findings: list[Finding] = []
    for clause_outcome in outcomes:
        findings.extend(clause_outcome.findings)
        accumulated = accumulated.merged_with(clause_outcome.metrics)
    if clause_outcomes is not None:
        clause_outcomes.extend(outcomes)
    return findings, accumulated


async def run_commercial_review(
    clauses: Sequence[Clause],
    contract_meta: dict[str, Any],
    *,
    agent: Any,
    metrics: ReviewMetrics | None = None,
) -> CommercialReviewOutcome:
    """逐条审查商务范畴条款；范围外条款跳过（不调用 Agent，不花 token）。

    阶段 6 的公开入口，保留给单专家场景与既有验收；并行拓扑走
    review_expert_shard + app/graph/nodes/expert_review.py。
    传入 metrics 时按引用回写累计值（调用方持同一对象读指标）。
    """
    findings, accumulated = await review_expert_shard(
        COMMERCIAL_SPEC, clauses, contract_meta, agent=agent, metrics=metrics
    )
    if metrics is not None:
        for name in ReviewMetrics.model_fields:
            setattr(metrics, name, getattr(accumulated, name))
    return CommercialReviewOutcome(findings=findings, metrics=accumulated)


async def review_commercial(state: ReviewState, config: RunnableConfig) -> dict[str, object]:
    """图节点：商务专家审查。findings 经 reducer 合并进 ReviewState。"""
    emitter = get_emitter(config)
    configurable = (config or {}).get("configurable") or {}
    agent = configurable.get("commercial_agent") or build_commercial_agent()
    await emitter.emit(EventType.NODE_STARTED, node=NODE_NAME, agent=COMMERCIAL_AGENT_NAME)
    started = time.monotonic()
    contract_meta = {"contract_id": state["contract_id"], "version": state["version"]}
    outcome = await run_commercial_review(state["clauses"], contract_meta, agent=agent)
    for finding in outcome.findings:
        await emitter.emit(
            EventType.MESSAGE_DELTA, agent=COMMERCIAL_AGENT_NAME, text=finding.summary
        )
    duration_ms = int((time.monotonic() - started) * 1000)
    total_tokens = outcome.metrics.input_tokens + outcome.metrics.output_tokens
    await emitter.emit(
        EventType.NODE_FINISHED,
        node=NODE_NAME,
        duration_ms=duration_ms,
        tokens=total_tokens,
    )
    logger.info("%s 节点完成: %s", NODE_NAME, outcome.metrics.model_dump())
    return {"findings": outcome.findings}
