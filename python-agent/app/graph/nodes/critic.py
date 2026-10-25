"""critic 节点：并行专家的汇合校验点（Stage 8）——用可执行代码拦幻觉，不让模型自评。

为什么存在：引用白名单在引擎层（validation.enforce_citation_whitelist）已强制一次，
但「单点强制」意味着任何绕过该函数的路径都会把伪造引用/篡改证据带进终态。本节点是
第二道确定性防线，三类校验全部是纯代码判定，绝不调用 LLM（任务禁止）：
  a) 引用白名单：citation.source_id 必须存在于该 finding 的本次检索返回集合快照
     （finding.citation_whitelist，由引擎在产出时快照）；
  b) 证据落地：evidence 必须是 clause.text 的子串；归一化（去空白标点、统一大小写）
     后仍不命中时，允许最长公共连续片段覆盖率 ≥ 0.9（模型改标点/空白容错）；
  c) 完整性：risk_level=high 的 finding 必须有非空 suggestion。

失败处理（只标记与打回，不修改 finding 内容）：
  - 生成 rework 指令（clause_id + 失败原因 + 期望修正点，模板来自
    prompts/critic/v1.rework.md，版本化文件，红线：prompt 不硬编码在节点里）；
  - 条件边回退到产出该 finding 的专家节点（并行模式 Send 载荷带重审指令；
    串行模式经 state["rework"] 路由到 serial_review），critic_failures += 1；
  - critic_failures ≥ MAX_CRITIC_RETRIES（2，硬编码在 state）→ 该 finding 标
    unverified 放行（risk_level 置最低信任档，与引擎层降级同语义），指标经
    run.completed.quality 输出；
  - 预算已耗尽或找不到产出专家/条款文本（无法路由重审）→ 直接标 unverified，
    不做无意义的打回。

拓扑：critic 是汇合点——pass 走 cache_store（Stage 11 起，校验通过才写缓存），
fail 走 rework 重新汇入 critic。计数器单调递增且上限硬编码，图必然收敛，不会死循环。

Stage 11：stale=true 的 finding（所属条款在新版本已被删除）不参与校验——它们的条款
文本已不在 state.clauses，校验必然判「证据缺失」进而被标 unverified 或触发无效打回，
把纯历史记录污染成本轮质量问题；它们以 stale 形态原样留存供追溯，但不上报告。

为什么不置 degraded：unverified 是质量指标而非运行降级；仅预算耗尽导致的放弃
打回同时置 degraded（与 Stage 7 的降级语义一致）。
"""

from __future__ import annotations

import difflib
import logging
import re
import time
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.types import Command, Send

from app.agents.registry import get_expert, get_expert_by_agent_name
from app.agents.spec import ExpertSpec
from app.events import EventType, get_emitter
from app.graph.state import (
    MAX_CRITIC_RETRIES,
    Clause,
    Finding,
    FindingTombstone,
    ReviewState,
    ReviewTask,
    ReworkInstruction,
    budget_exhausted,
)
from app.prompts import load_prompt

logger = logging.getLogger("app.graph.critic")

NODE_NAME = "critic"
HIGH_LEVEL = "high"
UNVERIFIED = "unverified"

# 证据落地容差：归一化后非子串时，最长公共连续片段占 evidence 的比例下限。
EVIDENCE_SIMILARITY_MIN = 0.9

# 重审指令模板：版本化文件，由本节点与 serial_review 共用（同一来源，避免两条
# 执行路径的修正文案漂移）。
REWORK_TEMPLATE = load_prompt("critic", 1, suffix="rework")

# 归一化剔除的字符：空白 + 中英文常见标点。模型常在标点/空白上与原文有出入，
# 剔除后比对可在容忍排版差异的同时挡住「改写式证据」。
_NORMALIZE_RE = re.compile(r"[\s\u3000，。；：、！？（）()【】《》〈〉“”‘’\"'`.,;:!?\-—–…·~]")


def build_rework_instruction(clause_id: str, reasons: list[str]) -> str:
    """把失败原因填进版本化模板，生成给专家模型的重审指令（并行/串行共用）。"""
    joined = "\n".join(f"- {reason}" for reason in reasons)
    return (
        REWORK_TEMPLATE.replace("{clause_id}", clause_id).replace("{reasons}", joined).strip()
    )


def _normalize(text: str) -> str:
    return _NORMALIZE_RE.sub("", text).lower()


def _containment(evidence: str, clause_text: str) -> float:
    """evidence 被 clause_text 以连续片段覆盖的比例（0~1）。

    用最长公共连续片段而非全串 ratio：evidence 通常只是条款的一小段，
    全串 ratio 会被条款总长度稀释而误杀合法引用；连续覆盖率才对应
    「这段文字几乎原样出现在条款里」的语义。
    """
    if not evidence:
        return 0.0
    matcher = difflib.SequenceMatcher(None, evidence, clause_text, autojunk=False)
    return matcher.find_longest_match(0, len(evidence), 0, len(clause_text)).size / len(evidence)


def evidence_grounded(clause_text: str, evidence: str) -> bool:
    """evidence 是否落地：归一化后为条款子串，或连续覆盖率 ≥ EVIDENCE_SIMILARITY_MIN。"""
    if not evidence or not evidence.strip():
        return False
    normalized_evidence = _normalize(evidence)
    normalized_clause = _normalize(clause_text)
    if normalized_evidence and normalized_evidence in normalized_clause:
        return True
    return _containment(normalized_evidence, normalized_clause) >= EVIDENCE_SIMILARITY_MIN


def validate_finding(finding: Finding, clause: Clause | None) -> list[str]:
    """对单条 finding 执行三类确定性校验，返回失败原因列表（空 = 通过）。

    纯函数：只读 finding 与 clause，无 IO、无模型调用——校验逻辑可单测、
    可复现，且与「让模型自评是否有幻觉」有本质区别（任务禁止后者）。
    """
    reasons: list[str] = []
    whitelist = set(finding.citation_whitelist)
    for citation in finding.citations:
        if citation.source_id not in whitelist:
            reasons.append(f"引用 {citation.source_id} 不在本次检索返回集合内")
    if clause is None:
        reasons.append(f"条款 {finding.clause_id} 文本缺失，无法校验 evidence 落地")
    elif not evidence_grounded(clause.text, finding.evidence):
        ratio = _containment(_normalize(finding.evidence), _normalize(clause.text))
        reasons.append(
            f"evidence 非条款原文片段（归一化后连续覆盖率 {ratio:.2f} "
            f"< {EVIDENCE_SIMILARITY_MIN}）"
        )
    if finding.risk_level == HIGH_LEVEL and not finding.suggestion.strip():
        reasons.append(f"risk_level={HIGH_LEVEL} 的发现缺少 suggestion")
    return reasons


def mark_unverified(finding: Finding) -> Finding:
    """把 finding 标记为 unverified 放行：只改信任档，不动内容（任务禁止改内容）。

    与引擎层（validation.UNVERIFIED）同一语义：引用丢空降级的最低信任档，
    驱动人工审批阈值；原 summary/evidence/citations/suggestion 原样保留供审计。
    """
    return finding.model_copy(update={"risk_level": UNVERIFIED})


def _tombstone(finding: Finding) -> FindingTombstone:
    return FindingTombstone(clause_id=finding.clause_id, risk_type=finding.risk_type)


def _group_by_expert_clause(
    failing: list[tuple[Finding, list[str]]],
) -> dict[tuple[str, str], tuple[Finding, list[str]]]:
    """按 (专家 Agent 名, clause_id) 聚合失败 finding：同键合并原因列表。"""
    grouped: dict[tuple[str, str], tuple[Finding, list[str]]] = {}
    for finding, reasons in failing:
        key = (finding.agent, finding.clause_id)
        if key in grouped:
            grouped[key][1].extend(reasons)
        else:
            grouped[key] = (finding, list(reasons))
    return grouped


def _build_rework_sends(
    state: ReviewState,
    instructions: list[ReworkInstruction],
    clauses_by_id: dict[str, Clause],
) -> list[Send]:
    """按专家把打回指令打包成 Send 载荷（并行拓扑专用）。

    Send 派发的节点只见载荷键：载荷必须自带执行所需全部通道 + rework_hints
    （重审指令随载荷走，不回读父状态）——与 dispatch 的载荷约束一致。
    """
    by_expert: dict[str, list[ReworkInstruction]] = {}
    for instruction in instructions:
        by_expert.setdefault(instruction.expert, []).append(instruction)
    sends: list[Send] = []
    for expert_name, expert_instructions in by_expert.items():
        spec = get_expert(expert_name)
        sends.append(
            Send(
                "expert_review",
                {
                    "review_task": ReviewTask(
                        expert=spec.name,
                        priority=spec.priority,
                        shard_index=0,
                        shard_total=1,
                        clauses=[
                            clauses_by_id[instruction.clause_id]
                            for instruction in expert_instructions
                        ],
                        rework_hints={
                            instruction.clause_id: build_rework_instruction(
                                instruction.clause_id, instruction.reasons
                            )
                            for instruction in expert_instructions
                        },
                    ),
                    "contract_id": state["contract_id"],
                    "version": state["version"],
                    "budget": state["budget"],
                },
            )
        )
    return sends


async def critic(
    state: ReviewState, config: RunnableConfig, *, parallel: bool = True
) -> Command[Any]:
    """图节点：校验全部 findings；通过走 END，未通过且可路由的打回专家重审。

    parallel=True（并行拓扑）：打回经 Send("expert_review", 载荷) 动态派发；
    parallel=False（串行拓扑）：打回经 Command(goto="serial_review")，指令写
    state["rework"] 由 serial_review 消费。两种模式的校验与收敛语义完全一致。
    """
    emitter = get_emitter(config)
    await emitter.emit(EventType.NODE_STARTED, node=NODE_NAME, agent=NODE_NAME)
    started = time.monotonic()

    clauses_by_id: dict[str, Clause] = {clause.clause_id: clause for clause in state["clauses"]}
    failing: list[tuple[Finding, list[str]]] = []
    stale_count = 0
    for finding in state["findings"]:
        if finding.stale:
            stale_count += 1
            continue
        reasons = validate_finding(finding, clauses_by_id.get(finding.clause_id))
        if reasons:
            failing.append((finding, reasons))
    if stale_count:
        logger.info("critic 跳过 %d 条 stale finding（所属条款已删除，仅追溯不校验）", stale_count)

    grouped = _group_by_expert_clause(failing)
    unroutable: list[tuple[Finding, list[str]]] = []
    routable: list[tuple[Finding, list[str], ExpertSpec]] = []
    for (agent_name, clause_id), (finding, reasons) in grouped.items():
        spec = get_expert_by_agent_name(agent_name)
        if spec is None or clause_id not in clauses_by_id:
            # 找不到产出专家或条款文本：重审无从执行，归入不可路由。
            unroutable.append((finding, reasons))
        else:
            routable.append((finding, reasons, spec))

    updates: list[Finding | FindingTombstone] = []
    for finding, _ in unroutable:
        # 找不到产出专家或条款文本：重审无从执行，直接标记放行（不炸整轮 run）。
        logger.warning(
            "finding %s/%s 无法路由重审（agent=%r 未注册或条款文本缺失），标记 unverified",
            finding.clause_id,
            finding.risk_type,
            finding.agent,
        )
        updates.extend([_tombstone(finding), mark_unverified(finding)])
        await _round_delta(emitter, 1, "无法路由重审，标记 unverified 放行")

    exhausted = budget_exhausted(state["budget"])
    if routable and (exhausted or state["critic_failures"] >= MAX_CRITIC_RETRIES):
        # 预算耗尽（不打回——低优先级分片可能被预算门跳过，打回即丢 finding）或
        # 打回轮数用尽：剩余失败全部标记放行，图收敛到 END。
        stop_reason = "预算耗尽" if exhausted else f"重审 {MAX_CRITIC_RETRIES} 轮仍未通过"
        for finding, reasons, _spec in routable:
            logger.warning(
                "%s：finding %s/%s 标记 unverified 放行；未通过原因: %s",
                stop_reason,
                finding.clause_id,
                finding.risk_type,
                reasons,
            )
            updates.extend([_tombstone(finding), mark_unverified(finding)])
        await _round_delta(emitter, len(routable), f"{stop_reason}，标记 unverified 放行")
        if exhausted:
            # 预算耗尽导致的放弃属于运行降级，与 Stage 7 降级语义对齐。
            updates_degraded = {"degraded": True}
        else:
            updates_degraded = {}
        return await _finish(
            emitter,
            started,
            Command(
                goto="cache_store",
                update={"findings": updates, **updates_degraded},
            ),
        )

    if not routable:
        # 全部通过，或仅剩不可路由的失败（已在上面标记）：进入人工审批断点。
        return await _finish(
            emitter,
            started,
            Command(goto="cache_store", update={"findings": updates} if updates else None),
        )

    # ---- 打回轮：墓碑删旧版本 + 计数 + 1 + rework 指令，条件边回退专家节点 ----
    instructions: list[ReworkInstruction] = []
    for finding, reasons, spec in routable:
        updates.append(_tombstone(finding))
        instructions.append(
            ReworkInstruction(
                expert=spec.name,
                clause_id=finding.clause_id,
                agent=spec.agent_name,
                reasons=reasons,
            )
        )
    round_no = state["critic_failures"] + 1
    logger.warning(
        "critic 第 %d 轮打回: %d 条 finding 未通过校验（%s）",
        round_no,
        len(instructions),
        "; ".join(f"{i.clause_id}: {'; '.join(i.reasons)}" for i in instructions),
    )
    await _round_delta(
        emitter, len(instructions), f"第 {round_no} 轮打回重审（上限 {MAX_CRITIC_RETRIES} 轮）"
    )

    goto: list[Send] | str
    if parallel:
        goto = _build_rework_sends(state, instructions, clauses_by_id)
    else:
        goto = "serial_review"
    return await _finish(
        emitter,
        started,
        Command(
            goto=goto,
            update={
                "findings": updates,
                "critic_failures": round_no,
                "rework": instructions,
            },
        ),
    )


async def _round_delta(emitter: Any, count: int, detail: str) -> None:
    """把本轮校验结论推入事件流（前端实时可见打回/放行，不只落在日志里）。"""
    await emitter.emit(
        EventType.MESSAGE_DELTA,
        agent=NODE_NAME,
        text=f"{NODE_NAME}: {count} 条 finding 未通过校验，{detail}",
    )


async def _finish(emitter: Any, started: float, command: Command[Any]) -> Command[Any]:
    """发射 node.finished 并原样返回路由命令（单一出口，保证埋点成对）。

    埋点口径与 Stage 7 一致：critic 是确定性代码、无模型调用，tokens=0；
    duration_ms 覆盖全部校验与指令组装。
    """
    duration_ms = int((time.monotonic() - started) * 1000)
    await emitter.emit(
        EventType.NODE_FINISHED, node=NODE_NAME, duration_ms=duration_ms, tokens=0
    )
    logger.info("%s 节点完成: duration_ms=%d", NODE_NAME, duration_ms)
    return command
