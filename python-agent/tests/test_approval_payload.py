"""interrupt payload 的聚合语义：一条条款一条待审项。

为什么单独测这个：审批的语义单位是「条款」，而 findings 的去重键是
(clause_id, risk_type)。一条条款被两个风险维度同时命中就会产生两条 finding，
若原样下发，待审列表里同一条款出现两次，而落库唯一键是 (review_id, clause_id)
——待审 21 条、生效 17 条，对不上。聚合这一层必须钉死。
"""

from __future__ import annotations

from app.graph.nodes.human_approval import build_interrupt_payload
from app.graph.state import Clause, Finding


def _finding(clause_id: str, risk_type: str, risk_level: str, suggestion: str) -> Finding:
    return Finding(
        clause_id=clause_id,
        agent=f"expert-{risk_type}",
        risk_type=risk_type,
        risk_level=risk_level,
        summary=f"{risk_type} 风险",
        suggestion=suggestion,
    )


def _clause(clause_id: str, text: str) -> Clause:
    return Clause(clause_id=clause_id, index=1, text=text)


def test_same_clause_is_merged_into_one_item() -> None:
    findings = [
        _finding("c-4.3", "payment_risk", "high", "明确付款期限为 30 日"),
        _finding("c-4.3", "ip_risk", "high", "澄清知识产权归属"),
    ]
    payload = build_interrupt_payload(
        findings, {"c-4.3": _clause("c-4.3", "4.3 付款与知识产权条款")}, 3, "hash-v3"
    )

    items = payload["items"]
    assert len(items) == 1, "同一条款两个风险维度必须聚合成一条待审项"
    item = items[0]
    # 被合并的 risk_type 一个都不能丢：否则事后没人知道这条决定覆盖了两个性质不同的风险。
    assert item["risk_types"] == ["payment_risk", "ip_risk"]
    assert item["findings_merged"] == 2
    assert item["risk"] == "high"
    assert item["clause_text"] == "4.3 付款与知识产权条款"
    # 建议按顺序编号拼接，①② 与 risk_types 的顺序一一对应。
    assert item["suggestion"] == "① 明确付款期限为 30 日\n② 澄清知识产权归属"


def test_single_finding_keeps_suggestion_verbatim() -> None:
    payload = build_interrupt_payload(
        [_finding("c-1", "payment_risk", "high", "缩短账期")],
        {"c-1": _clause("c-1", "原文")},
        1,
        "h",
    )
    item = payload["items"][0]
    # 单段建议不加编号：卡片上孤零零一个「①」会让人以为漏了后半段。
    assert item["suggestion"] == "缩短账期"
    assert item["risk_types"] == ["payment_risk"]
    assert item["findings_merged"] == 1


def test_merged_risk_is_the_highest_of_the_group() -> None:
    findings = [
        _finding("c-2", "payment_risk", "medium", "a"),
        _finding("c-2", "breach_risk", "high", "b"),
        _finding("c-2", "ip_risk", "low", "c"),
    ]
    payload = build_interrupt_payload(findings, {"c-2": _clause("c-2", "原文")}, 1, "h")
    # 取最高档而不是「第一条」：聚合后这条决定的风险档必须反映最严重的那个发现。
    assert payload["items"][0]["risk"] == "high"


def test_different_clauses_stay_separate_and_keep_order() -> None:
    findings = [
        _finding("c-1", "payment_risk", "high", "a"),
        _finding("c-2", "delivery_risk", "high", "b"),
        _finding("c-1", "breach_risk", "high", "c"),
    ]
    payload = build_interrupt_payload(
        findings,
        {"c-1": _clause("c-1", "一"), "c-2": _clause("c-2", "二")},
        1,
        "h",
    )
    items = payload["items"]
    # 聚合不改变条款出现顺序：c-1 先出现就先列出，c-2 不会被挪到后面。
    assert [i["clause_id"] for i in items] == ["c-1", "c-2"]
    assert items[0]["risk_types"] == ["payment_risk", "breach_risk"]
    assert items[0]["suggestion"] == "① a\n② c"


def test_missing_clause_text_falls_back_to_empty() -> None:
    payload = build_interrupt_payload(
        [_finding("c-9", "payment_risk", "high", "a")], {}, 1, "h"
    )
    # 拿不到原文不是崩溃理由：审批记录的前措辞留空，比整轮跑挂掉好。
    assert payload["items"][0]["clause_text"] == ""
