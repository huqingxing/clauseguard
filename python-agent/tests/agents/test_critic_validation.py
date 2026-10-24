"""Stage 8 单测：critic 三类确定性校验（引用白名单 / 证据落地 / 完整性）。

为什么值得单测：这三类校验是「用可执行代码拦幻觉」的红线所在——校验逻辑是纯函数
（无 IO、无模型调用），行为必须完全可预测。本文件把每类校验的边界（通过 / 失败 /
容差）钉死，防止后续迭代把容差放宽到失去拦截意义，或把「模型自评」混进校验路径。
"""

from __future__ import annotations

from app.graph.nodes.critic import (
    EVIDENCE_SIMILARITY_MIN,
    _containment,
    _normalize,
    build_rework_instruction,
    evidence_grounded,
    mark_unverified,
    validate_finding,
)
from app.graph.state import Clause, Finding

_CLAUSE_TEXT = (
    "买方应在合同签订后3个工作日内支付合同总价的100%作为预付款，该款项在任何情况下不予退还。"
)


def _clause(text: str = _CLAUSE_TEXT) -> Clause:
    return Clause(clause_id="c-01", index=1, text=text)


def _finding(**overrides: object) -> Finding:
    """构造一条默认「三类校验全过」的 finding，按需覆盖字段。"""
    payload: dict[str, object] = {
        "clause_id": "c-01",
        "agent": "commercial_expert",
        "risk_type": "payment_risk",
        "risk_level": "high",
        "summary": "预付100%且无返还与扣回机制，买方资金风险极高。",
        "evidence": "支付合同总价的100%作为预付款",
        "suggestion": "改为「预付30%+验收合格后付尾款」的分期结构，并约定预付款扣回机制。",
        "citations": [
            {
                "source_id": "law-001",
                "title": "《民法典》第五百七十七条",
                "excerpt": "当事人一方不履行合同义务……",
            }
        ],
        "citation_whitelist": ("law-001", "policy-001"),
    }
    payload.update(overrides)
    return Finding.model_validate(payload)


# ---- b) 证据落地：evidence_grounded ----


def test_verbatim_evidence_passes() -> None:
    assert evidence_grounded(_CLAUSE_TEXT, "支付合同总价的100%作为预付款") is True


def test_punctuation_whitespace_case_tolerance_passes() -> None:
    """归一化容差：模型改标点/空白/大小写不算编造，仍应落地。"""
    clause = "甲方应于2027年3月31日前将全部货物一次性交付至买方指定地点，运输费用由卖方承担。"
    evidence = "甲方应于 2027 年 3 月 31 日前将全部货物一次性交付至买方指定地点，运输费用由卖方承担"
    assert evidence_grounded(clause, evidence) is True


def test_high_coverage_evidence_passes() -> None:
    """连续覆盖率 ≥ 0.9：逐字片段中个别字符偏差容忍，低于阈值即拒。"""
    evidence = "买方应在合同签订后3个工作日内支付合同总价"  # 原文前 20 字
    assert evidence_grounded(_CLAUSE_TEXT, evidence) is True
    near_miss = evidence[:-1] + "×"  # 末字改掉 -> 覆盖率 19/20 = 0.95，仍达标
    assert evidence_grounded(_CLAUSE_TEXT, near_miss) is True


def test_ungrounded_evidence_fails() -> None:
    assert evidence_grounded(_CLAUSE_TEXT, "卖方应在三十日内一次性全额退还预付款。") is False


def test_empty_evidence_fails() -> None:
    assert evidence_grounded(_CLAUSE_TEXT, "") is False
    assert evidence_grounded(_CLAUSE_TEXT, "   \n  ") is False


def test_containment_measures_evidence_coverage_not_full_ratio() -> None:
    """口径锁定：连续片段占 evidence 的比例，不被条款总长度稀释。"""
    short = "预付款"  # 3 字短证据完整出现在长条款中
    assert _containment(_normalize(short), _normalize(_CLAUSE_TEXT)) == 1.0
    unrelated = "不存在的证据片段"
    assert _containment(_normalize(unrelated), _normalize(_CLAUSE_TEXT)) < EVIDENCE_SIMILARITY_MIN


# ---- validate_finding：三类校验的汇总入口 ----


def test_valid_finding_has_no_reasons() -> None:
    assert validate_finding(_finding(), _clause()) == []


def test_citation_outside_whitelist_flagged() -> None:
    finding = _finding(
        citations=[
            {
                "source_id": "FAKE-001",
                "title": "《幻想法典》第一条",
                "excerpt": "……",
            }
        ]
    )
    reasons = validate_finding(finding, _clause())
    assert len(reasons) == 1
    assert "FAKE-001" in reasons[0]
    assert "不在本次检索返回集合内" in reasons[0]


def test_missing_clause_text_flagged() -> None:
    reasons = validate_finding(_finding(), None)
    assert any("文本缺失" in reason for reason in reasons)


def test_ungrounded_evidence_flagged_with_ratio() -> None:
    reasons = validate_finding(_finding(evidence="与条款毫无关系的另一段表述。"), _clause())
    assert len(reasons) == 1
    assert "覆盖率" in reasons[0]


def test_high_without_suggestion_flagged() -> None:
    reasons = validate_finding(_finding(suggestion="  "), _clause())
    assert reasons == ["risk_level=high 的发现缺少 suggestion"]


def test_medium_without_suggestion_allowed() -> None:
    """完整性校验只约束 high：中低风险缺 suggestion 不拦（阈值语义）。"""
    finding = _finding(risk_level="medium", suggestion="")
    assert validate_finding(finding, _clause()) == []


def test_all_failure_reasons_collected_together() -> None:
    finding = _finding(
        citations=[{"source_id": "FAKE-001", "title": "假", "excerpt": "假"}],
        evidence="凭空编造的证据。",
        suggestion="",
    )
    reasons = validate_finding(finding, _clause())
    assert len(reasons) == 3  # 引用 + 证据 + 完整性三类各一条


# ---- mark_unverified：只标记信任档，不动内容 ----


def test_mark_unverified_only_changes_trust_level() -> None:
    finding = _finding()
    marked = mark_unverified(finding)
    assert marked.risk_level == "unverified"
    assert marked.summary == finding.summary
    assert marked.evidence == finding.evidence
    assert marked.suggestion == finding.suggestion
    assert marked.citations == finding.citations
    # 原对象不被修改（纯函数语义）。
    assert finding.risk_level == "high"


# ---- build_rework_instruction：版本化模板填充 ----


def test_rework_instruction_fills_versioned_template() -> None:
    text = build_rework_instruction("r-02", ["引用 FAKE-001 不在白名单", "evidence 不落地"])
    assert "r-02" in text
    assert "引用 FAKE-001 不在白名单" in text
    assert "evidence 不落地" in text
    # 模板占位符必须全部替换（漏替换会把 {clause_id} 原样发给模型）。
    assert "{clause_id}" not in text
    assert "{reasons}" not in text
    # 与 mock 替身/串行路径共用的标记短语必须来自模板文件（红线：prompt 不硬编码）。
    assert "未通过确定性校验" in text
