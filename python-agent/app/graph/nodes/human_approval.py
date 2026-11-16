"""human_approval 节点：图内的人工审批断点（Stage 9）——审批是图的一部分，不是一次 HTTP 调用。

为什么本节点不发射节点事件：LangGraph 在 resume 时会从本节点顶部重跑（interrupt 语义），
若在本节点内发射 NODE_STARTED/INTERRUPT 会重复。故「是否暂停 / 恢复后放行」的逻辑留在节点，
而对应的 NODE_STARTED / INTERRUPT / NODE_FINISHED 由编排层 app.main._run_pipeline 在
检测到 interrupt / 完成时发射（编排层每次调用只跑一次，不会被 resume 重跑）。

行为：
  - 终态 findings 若存在 risk_level=high → 调 langgraph interrupt() 挂起，运行态留在 ReviewState
    由 Postgres checkpointer 持久化（铁律 4：断点必须在图里、可恢复）；
  - 无 high 风险 → 直接放行（返回空更新），进入 render；
  - resume（Python /v1/runs/{run_id}/resume 以审批结果数组作为 interrupt 返回值）后，本节点
    从 interrupt() 处续跑，置 approved=True 并进入 render。

不修改 finding、不在此处写审批记录（审批落库是 Java 职责，见 Java approveReview）；
本节点只决定「是否暂停」与「恢复后放行」。
"""

from __future__ import annotations

import logging

from langchain_core.runnables import RunnableConfig
from langgraph.types import interrupt

from app.graph.state import Clause, Finding, ReviewState

logger = logging.getLogger("app.graph.human_approval")

NODE_NAME = "human_approval"
HIGH_LEVEL = "high"
REASON = "high_risk_pending_approval"


def high_risk_findings(findings: list[Finding]) -> list[Finding]:
    """挑出需要人工审批的高风险发现（仅 risk_level=high；unverified 已降级不阻断）。

    Stage 11：stale 的历史结论不进审批——它所属条款已被删除，让人审批一条不存在的
    条款既无意义又会阻塞整轮（审批列表里出现无法定位的条目）。stale 仍留在 state 里
    供追溯（红线：不得静默丢弃），只是不参与任何面向本版本的决策。
    """
    return [
        finding
        for finding in findings
        if finding.risk_level == HIGH_LEVEL and not finding.stale
    ]


def build_interrupt_payload(
    findings: list[Finding],
    clauses_by_id: dict[str, Clause],
    contract_version: int,
    content_hash: str,
) -> dict[str, object]:
    """构造 interrupt 事件的 payload：逐**条款**列出待审项 + 乐观锁基线（版本/哈希）。

    payload 为开放对象（events.schema.json 中 Interrupt.payload 为 additionalProperties:true），
    前端与 Java 据此展示待审条款并做合同变更校验（contract_version/content_hash 不匹配即 409）。
    每项携带 clause_text（条款原文）作为「前措辞」基线，供 Java 审批记录的前后措辞留痕。

    为什么必须按 clause_id 聚合：findings 的去重键是 (clause_id, risk_type)，一条条款被
    付款风险 + 免责风险同时命中就是两条 finding。而审批人签的是「这一条条款我同意」，
    不是「payment_risk 同意、disclaimer_risk 不同意」——审批的语义单位是条款。逐 finding
    生成 items 会让同一条款在待审列表里出现两次，落库时（唯一键 review_id+clause_id）
    被去重掉一条，造成「待审 N 条、生效 N-1 条」。

    聚合不丢信息：被合并的 risk_type 全部保留在 risk_types 里，各 finding 的建议按顺序
    编号拼成一段（① ② …），findings_merged 记录原始条数。审计时能看出「这一条决定
    覆盖了两个不同性质的风险」，只是换了个承载方式。
    """
    grouped: dict[str, list[Finding]] = {}
    for finding in findings:
        # dict 保插入序：待审顺序与 findings 顺序一致，前端不用再排。
        grouped.setdefault(finding.clause_id, []).append(finding)

    items = [build_item(clause_id, group, clauses_by_id) for clause_id, group in grouped.items()]
    return {
        "reason": REASON,
        "contract_version": contract_version,
        "content_hash": content_hash,
        "items": items,
    }


# 风险档从低到高；求「取最高」时用它比大小，不依赖字符串顺序。
# unverified 是引用校验降级值，低于 low：它不该抬高整条条款的档位。
RISK_RANK = {"unverified": 0, "low": 1, "medium": 2, "high": 3}

# 合并多段建议时的编号；超出 20 段退化为 (n)，不为了凑符号去引第三方库。
_CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳"


def build_item(
    clause_id: str, group: list[Finding], clauses_by_id: dict[str, Clause]
) -> dict[str, object]:
    """把同一 clause_id 的若干 finding 压成一条待审项。"""
    risk = max(group, key=lambda f: RISK_RANK.get(f.risk_level, 0)).risk_level
    # 保序去重：两个专家报同一个 risk_type 时只列一次，但不打乱首个出现的位置。
    risk_types = list(dict.fromkeys(f.risk_type for f in group))

    suggestions = [f.suggestion.strip() for f in group if f.suggestion and f.suggestion.strip()]
    if len(suggestions) <= 1:
        # 单段建议不加编号：一张卡片上孤零零一个「①」会让审阅者以为漏了后半段。
        suggestion = suggestions[0] if suggestions else ""
    else:
        suggestion = "\n".join(
            f"{_CIRCLED[i] if i < len(_CIRCLED) else f'({i + 1})'} {text}"
            for i, text in enumerate(suggestions)
        )

    return {
        "clause_id": clause_id,
        "risk": risk,
        "risk_types": risk_types,
        "suggestion": suggestion,
        "findings_merged": len(group),
        "clause_text": clauses_by_id[clause_id].text if clause_id in clauses_by_id else "",
    }


async def human_approval(state: ReviewState, config: RunnableConfig) -> dict[str, object]:
    """图节点：高风险条款人工审批断点；pause 经 interrupt，resume 后放行。

    本节点不发射节点事件（见模块 docstring）：NODE_STARTED/INTERRUPT/NODE_FINISHED 由
    编排层 _run_pipeline 在检测到 interrupt / 完成时发射，避免 resume 重跑导致的重复事件。
    """
    findings = state["findings"]
    pending = high_risk_findings(findings)
    if not pending:
        # 无高风险：直接放行，不暂停。
        return {}

    # 存在高风险：挂起图等待人工审批；payload 携带待审条款 + 乐观锁基线。
    clauses_by_id = {clause.clause_id: clause for clause in state["clauses"]}
    payload = build_interrupt_payload(
        pending, clauses_by_id, state["version"], state["content_hash"]
    )
    logger.info("%s 暂停：%d 条高风险条款待审批", NODE_NAME, len(pending))
    # 挂起；resume 重入时本函数从这一行之后续跑，interrupt 直接返回 Java 转发来的审批结果数组
    # （此阶段 render 占位，仅放行）。
    _ = interrupt(payload)  # noqa: F841
    logger.info("%s 恢复：审批结果已接收，放行进入 render", NODE_NAME)
    return {"approved": True}
