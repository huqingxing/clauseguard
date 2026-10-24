"""Stage 6 验收：引用白名单强制（红线的代码化）。

覆盖：伪造 id 丢弃并计数、article 精确别名认账、引用丢空降级 unverified、
引用内容以白名单条目重建（不采信模型文本）、evidence 逐字校验、
ToolMessage 白名单抽取（非 JSON 内容忽略）。
"""

from __future__ import annotations

from langchain_core.messages import AIMessage, ToolMessage

from app.agents.schemas import RawFinding
from app.agents.validation import (
    enforce_citation_whitelist,
    evidence_is_verbatim,
    extract_whitelist,
)


def _whitelist() -> dict[str, object]:
    return extract_whitelist(
        [
            ToolMessage(
                content='{"candidates": [{"id": "law-001", "article": "《民法典》第五百七十七条", '
                '"title": "违约责任", "text": "当事人一方不履行合同义务……"}]}',
                tool_call_id="call_law",
            ),
            ToolMessage(
                content=(
                    '{"candidates": [{"id": "policy-001", '
                    '"article": "《采购合同管理规范》预付款比例条款", '
                    '"title": "预付款比例", "text": "预付款不得超过30%。"}]}'
                ),
                tool_call_id="call_policy",
            ),
            # 非 JSON 的工具输出必须被忽略，不能让白名单混入脏数据。
            ToolMessage(content="工具内部错误", tool_call_id="call_bad"),
        ]
    )


def _raw_finding(citations: list[dict[str, str]]) -> RawFinding:
    return RawFinding.model_validate(
        {
            "clause_id": "c-001",
            "risk_type": "payment_risk",
            "risk_level": "high",
            "summary": "预付100%无返还机制",
            "evidence": "支付合同总价的100%作为预付款",
            "suggestion": "改分期",
            "citations": citations,
        }
    )


def test_valid_citations_survive_and_are_rebuilt_from_whitelist() -> None:
    whitelist = _whitelist()
    finding, dropped = enforce_citation_whitelist(
        _raw_finding([{"source_id": "law-001", "article": "《民法典》第五百七十七条"}]),
        whitelist,
        agent="commercial_expert",
    )
    assert dropped == 0
    assert finding.risk_level == "high"
    assert [c.source_id for c in finding.citations] == ["law-001"]
    # 引用内容以检索结果重建，不采信模型自带文本。
    assert finding.citations[0].title == "违约责任"
    assert finding.citations[0].excerpt == "当事人一方不履行合同义务……"
    assert finding.agent == "commercial_expert"
    assert finding.evidence == "支付合同总价的100%作为预付款"
    # risk_type 原样透传：它是 (clause_id, risk_type) 合并去重键的一半。
    assert finding.risk_type == "payment_risk"


def test_fabricated_citation_dropped_and_counted() -> None:
    whitelist = _whitelist()
    finding, dropped = enforce_citation_whitelist(
        _raw_finding([{"source_id": "FAKE-001", "article": "《幻想法典》第一条"}]),
        whitelist,
        agent="commercial_expert",
    )
    assert dropped == 1
    assert finding.citations == []
    # citations 丢空 -> 降级 unverified。
    assert finding.risk_level == "unverified"


def test_mixed_citations_keep_valid_drop_fake() -> None:
    whitelist = _whitelist()
    finding, dropped = enforce_citation_whitelist(
        _raw_finding(
            [
                {"source_id": "FAKE-001", "article": "《幻想法典》第一条"},
                {"source_id": "policy-001", "article": "《采购合同管理规范》预付款比例条款"},
            ]
        ),
        whitelist,
        agent="commercial_expert",
    )
    assert dropped == 1
    assert [c.source_id for c in finding.citations] == ["policy-001"]
    # 仍有有效引用，不降级。
    assert finding.risk_level == "high"


def test_article_alias_match_accepted_when_id_missing() -> None:
    whitelist = _whitelist()
    finding, dropped = enforce_citation_whitelist(
        _raw_finding([{"source_id": "unknown-id", "article": "《民法典》第五百七十七条"}]),
        whitelist,
        agent="commercial_expert",
    )
    assert dropped == 0
    assert [c.source_id for c in finding.citations] == ["law-001"]


def test_article_alias_must_match_exactly() -> None:
    whitelist = _whitelist()
    _, dropped = enforce_citation_whitelist(
        _raw_finding([{"source_id": "unknown-id", "article": "《民法典》第五百七十八条"}]),
        whitelist,
        agent="commercial_expert",
    )
    assert dropped == 1


def test_evidence_must_be_verbatim_substring() -> None:
    clause_text = "买方应在合同签订后3个工作日内支付合同总价的100%作为预付款。"
    assert evidence_is_verbatim(clause_text, "100%作为预付款")
    assert not evidence_is_verbatim(clause_text, "支付全部价款")
    assert not evidence_is_verbatim(clause_text, "")


def test_extract_whitelist_ignores_non_tool_messages() -> None:
    whitelist = extract_whitelist([AIMessage(content="没有工具输出")])
    assert whitelist == {}
