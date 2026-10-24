"""引用白名单强制与审查指标（红线「引用只能来自检索结果」的代码化）。

两条硬约束在这里落地：
1. citation 的 source_id（或其 article 精确别名）不在本次检索白名单内 -> 丢弃该 citation
   并累加 dropped_citations；
2. 整条 finding 的 citations 被丢空 -> risk_level 降级为 unverified。
引用内容一律以白名单条目重建（title/excerpt/url 取检索结果），不采信模型自带的文本，
保证前端与审计看到的引用可回溯到真实检索记录。
"""

from __future__ import annotations

import json
from collections.abc import Sequence

from langchain_core.messages import BaseMessage, ToolMessage
from pydantic import BaseModel

from app.agents.schemas import RawFinding
from app.graph.state import Citation, Finding

UNVERIFIED = "unverified"


class ReviewMetrics(BaseModel):
    """单次 review 运行的累计指标；随节点日志输出，供验收与可观测性消费。"""

    clauses_reviewed: int = 0
    clauses_skipped: int = 0
    findings_emitted: int = 0
    dropped_citations: int = 0
    unverified_findings: int = 0
    parse_errors: int = 0
    llm_errors: int = 0
    evidence_mismatches: int = 0
    input_tokens: int = 0
    output_tokens: int = 0

    def merged_with(self, other: ReviewMetrics) -> ReviewMetrics:
        """返回与 other 逐字段相加的新指标（不可变语义，便于并发分片汇总）。"""
        return ReviewMetrics(
            **{
                name: getattr(self, name) + getattr(other, name)
                for name in ReviewMetrics.model_fields
            }
        )


class WhitelistEntry(BaseModel):
    """检索白名单条目：引用只能由这些字段重建。"""

    source_id: str
    article: str = ""
    title: str = ""
    excerpt: str = ""
    url: str | None = None


def extract_whitelist(messages: Sequence[BaseMessage]) -> dict[str, WhitelistEntry]:
    """从 Agent 运行的 ToolMessage 中解析本次检索白名单（source_id -> 条目）。

    白名单只来自本次运行真实发生的工具返回：模型若引用未经检索的 id，这里必然查不到。
    """
    whitelist: dict[str, WhitelistEntry] = {}
    for message in messages:
        if not isinstance(message, ToolMessage):
            continue
        try:
            payload = json.loads(message.text)
        except (json.JSONDecodeError, ValueError):
            continue
        candidates = payload.get("candidates") if isinstance(payload, dict) else None
        if not isinstance(candidates, list):
            continue
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            source_id = candidate.get("id")
            if not isinstance(source_id, str) or not source_id:
                continue
            whitelist[source_id] = WhitelistEntry(
                source_id=source_id,
                article=str(candidate.get("article") or ""),
                title=str(candidate.get("title") or ""),
                excerpt=str(candidate.get("text") or ""),
                url=candidate.get("url") if isinstance(candidate.get("url"), str) else None,
            )
    return whitelist


def enforce_citation_whitelist(
    raw: RawFinding, whitelist: dict[str, WhitelistEntry], agent: str
) -> tuple[Finding, int]:
    """把 RawFinding 收敛为 Finding：引用过白名单、空引用降级 unverified。

    返回 (finding, dropped_count)；dropped_count 是本条被丢弃的 citation 数。
    """
    kept: list[Citation] = []
    dropped = 0
    for citation in raw.citations:
        entry = whitelist.get(citation.source_id)
        if entry is None and citation.article:
            # 兼容模型只回显 article 编号的场景：编号须与白名单条目精确一致才认。
            entry = next(
                (item for item in whitelist.values() if item.article == citation.article),
                None,
            )
        if entry is None:
            dropped += 1
            continue
        kept.append(
            Citation(
                source_id=entry.source_id,
                title=entry.title,
                excerpt=entry.excerpt,
                url=entry.url,
            )
        )
    risk_level = UNVERIFIED if not kept else raw.risk_level
    finding = Finding(
        clause_id=raw.clause_id,
        agent=agent,
        risk_type=raw.risk_type,
        risk_level=risk_level,
        summary=raw.summary,
        evidence=raw.evidence,
        suggestion=raw.suggestion,
        citations=kept,
        # 快照本次检索白名单：Critic 据此独立复核引用是否来自真实检索
        #（引擎强制之外的第二道防线，防止绕过本函数的路径把伪造引用带进终态）。
        citation_whitelist=tuple(sorted(whitelist)),
    )
    return finding, dropped


def evidence_is_verbatim(clause_text: str, evidence: str) -> bool:
    """evidence 必须是条款原文的逐字 substring（prompt 硬性规则的代码化校验）。"""
    return bool(evidence) and evidence in clause_text
