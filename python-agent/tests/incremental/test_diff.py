"""版本 diff 的纯函数验收：changed / added / removed / unchanged 四类互斥且全覆盖。

为什么单独测纯函数：diff 是「只花 5% 的钱」这条成本主张的事实基础——它决定哪些条款
进 fan-out。判定逻辑若有偏差，省钱就变成漏审，故必须在无 IO、无模型的层面锁定语义。
"""

from __future__ import annotations

from app.graph.state import Clause
from app.incremental.diff import clause_fingerprint, diff_clauses


def clause(clause_id: str, no: str, text: str, index: int = 0) -> Clause:
    return Clause(clause_id=clause_id, index=index, text=text, no=no)


def test_identical_clauses_are_all_unchanged() -> None:
    baseline = [
        clause("a", "第一条", "甲方应按时付款"),
        clause("b", "第二条", "乙方应按时交货"),
    ]
    delta = diff_clauses(baseline, baseline)
    assert delta.unchanged == ["a", "b"]
    assert (delta.changed, delta.added, delta.removed) == ([], [], [])


def test_same_number_different_text_is_changed() -> None:
    baseline = [clause("v1-1", "第一条", "甲方应按时付款")]
    current = [clause("v2-1", "第一条", "甲方应在验收合格后30日内付款")]
    delta = diff_clauses(baseline, current)
    # ID 变了但编号没变：判定为 changed，且 ID 取当前版本侧（后续要按新 ID 重审）。
    assert delta.changed == ["v2-1"]
    assert (delta.added, delta.removed, delta.unchanged) == ([], [], [])


def test_new_number_is_added_and_missing_number_is_removed() -> None:
    baseline = [
        clause("v1-1", "第一条", "甲方应按时付款"),
        clause("v1-2", "第二条", "乙方应按时交货"),
    ]
    current = [
        clause("v2-1", "第一条", "甲方应按时付款"),
        clause("v2-3", "第三条", "新增保密条款"),
    ]
    delta = diff_clauses(baseline, current)
    assert delta.added == ["v2-3"]
    # removed 取基线侧 ID：新版本里它已经不存在，只能用旧 ID 追溯历史 findings。
    assert delta.removed == ["v1-2"]
    assert delta.unchanged == ["v2-1"]
    assert delta.changed == []


def test_unnumbered_clauses_pair_by_content_only() -> None:
    """无编号条款（前言段等）按内容配对：同文即同一条，改了算新增。"""
    baseline = [
        clause("p1", "", "前言：双方本着平等自愿原则"),
        clause("p2", "", "落款：本合同一式两份"),
    ]
    current = [
        clause("q1", "", "前言：双方本着平等自愿原则"),
        clause("q2", "", "落款：本合同一式三份"),
    ]
    delta = diff_clauses(baseline, current)
    assert delta.unchanged == ["q1"]
    # 改了内容但无编号：无法可靠判定为「改」，按新增处理（宁可重审也不漏审）。
    assert delta.added == ["q2"]
    assert delta.removed == ["p2"]


def test_unnumbered_duplicate_text_pairs_only_once() -> None:
    """同一文本出现两次：哈希配对只消耗一次，第二个算新增（不制造重复复用）。"""
    baseline = [clause("p1", "", "重复文本")]
    current = [clause("q1", "", "重复文本"), clause("q2", "", "重复文本")]
    delta = diff_clauses(baseline, current)
    assert delta.unchanged == ["q1"]
    assert delta.added == ["q2"]
    assert delta.removed == []


def test_empty_baseline_marks_everything_added() -> None:
    current = [clause("a", "第一条", "甲方应按时付款")]
    delta = diff_clauses([], current)
    assert delta.added == ["a"]
    assert delta.added_count == 1
    assert (delta.changed_count, delta.removed_count, delta.unchanged_count) == (0, 0, 0)


def test_fingerprint_prefers_explicit_hash_and_falls_back_to_text() -> None:
    explicit = Clause(clause_id="a", index=0, text="文本", hash="deadbeef")
    assert clause_fingerprint(explicit) == "deadbeef"
    # 未给 hash 时由 before 校验器按 text 现算，两个入口得到同一指纹（键不漂移）。
    computed = clause_fingerprint(Clause(clause_id="a", index=0, text="文本"))
    assert computed == Clause(clause_id="b", index=1, text="文本").hash
    assert computed != clause_fingerprint(Clause(clause_id="a", index=0, text="文本二"))
