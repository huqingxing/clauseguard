"""Stage 7 验收：merge_findings reducer 的幂等性（并行合并的 correctness 基础）。

任务要求：按 (clause_id, risk_type) 去重；重复时保留 risk_level 更高的一条，
同级保留 evidence 更长的一条；citations 合并去重；纯函数；覆盖重复/冲突/空三种情况。
幂等是本测试的核心断言——并行分支的到达顺序不确定，reducer 必须与顺序无关。
"""

from __future__ import annotations

import copy

from app.graph.state import Citation, Finding, merge_findings


def _finding(
    clause_id: str = "c-001",
    risk_type: str = "payment_risk",
    risk_level: str = "medium",
    evidence: str = "支付合同总价的100%作为预付款",
    citations: list[Citation] | None = None,
    agent: str = "commercial_expert",
) -> Finding:
    return Finding(
        clause_id=clause_id,
        agent=agent,
        risk_type=risk_type,
        risk_level=risk_level,
        summary=f"{clause_id}/{risk_type} 的风险结论",
        evidence=evidence,
        suggestion="改分期",
        citations=citations or [],
    )


def _citation(source_id: str) -> Citation:
    return Citation(source_id=source_id, title=source_id, excerpt="摘录")


# ---- 空输入 ----


def test_merge_empty_inputs() -> None:
    assert merge_findings([], []) == []
    assert merge_findings([], [_finding()]) == [_finding()]
    assert merge_findings([_finding()], []) == [_finding()]


# ---- 重复（同键同级）----


def test_duplicate_key_keeps_single_with_union_citations() -> None:
    left = _finding(citations=[_citation("law-001")], evidence="短证据")
    right = _finding(
        citations=[_citation("policy-001"), _citation("law-001")], evidence="长一些的证据片段"
    )
    merged = merge_findings([left], [right])
    assert len(merged) == 1, "同 (clause_id, risk_type) 必须收敛为一条"
    # 同级 evidence 长者胜。
    assert merged[0].evidence == "长一些的证据片段"
    # citations 并集去重（law-001 两边都有，只保留一份）。
    assert sorted(c.source_id for c in merged[0].citations) == ["law-001", "policy-001"]


# ---- 冲突（同键不同级）----


def test_conflicting_levels_keep_higher_risk() -> None:
    low = _finding(
        risk_level="low", evidence="更长的证据片段在这里", citations=[_citation("law-001")]
    )
    high = _finding(risk_level="high", evidence="短", citations=[_citation("policy-001")])
    merged = merge_findings([low], [high])
    assert len(merged) == 1
    assert merged[0].risk_level == "high", "risk_level 高者胜，与 evidence 长度无关"
    # 败者的 citations 仍并入胜者（检索依据不丢）。
    assert sorted(c.source_id for c in merged[0].citations) == ["law-001", "policy-001"]


def test_unverified_ranks_lowest() -> None:
    unverified = _finding(risk_level="unverified", evidence="很长的证据片段xxxxxxxx")
    low = _finding(risk_level="low", evidence="短")
    assert merge_findings([unverified], [low])[0].risk_level == "low"
    assert merge_findings([low], [unverified])[0].risk_level == "low"


# ---- 不同键互不干扰 ----


def test_distinct_keys_all_kept() -> None:
    a = _finding(clause_id="c-001", risk_type="payment_risk")
    b = _finding(clause_id="c-001", risk_type="breach_risk")
    c = _finding(clause_id="c-002", risk_type="payment_risk")
    merged = merge_findings([a, b], [c])
    assert len(merged) == 3


# ---- 幂等性 ----


def test_merge_is_idempotent_regardless_of_order() -> None:
    a = _finding(risk_level="high", evidence="短", citations=[_citation("law-001")])
    b = _finding(risk_level="medium", evidence="中等长度证据", citations=[_citation("policy-001")])
    c = _finding(
        risk_level="medium", evidence="最长的证据片段abcdefg", citations=[_citation("sim-001")]
    )
    ab = merge_findings(merge_findings([a], [b]), [c])
    ba = merge_findings(merge_findings([b], [a]), [c])
    cb = merge_findings([c], merge_findings([b], [a]))
    assert ab == ba == cb, "合并结果必须与分支到达顺序无关（并行正确性）"


def test_merge_does_not_mutate_inputs() -> None:
    left = _finding(citations=[_citation("law-001")])
    right = _finding(citations=[_citation("policy-001")])
    left_snapshot = copy.deepcopy(left)
    right_snapshot = copy.deepcopy(right)
    merge_findings([left], [right])
    assert left == left_snapshot and right == right_snapshot, "reducer 必须是纯函数"


def test_same_finding_merged_with_itself_is_stable() -> None:
    finding = _finding(citations=[_citation("law-001")])
    merged = merge_findings([finding], [finding])
    assert len(merged) == 1
    assert merged[0].citations == [_citation("law-001")], "自身合并不应复制 citations"
