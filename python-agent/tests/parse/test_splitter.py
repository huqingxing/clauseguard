"""splitter 层测试：六类样本 + 父链完整性 + 切片硬约束 + 类型判定。"""

from __future__ import annotations

from pathlib import Path

from docx import Document as DocxDocument

from app.parse import parse_contract
from app.parse.models import ClauseType
from tests.parse.fixtures import (
    docx_attachment,
    docx_nested,
    docx_no_number,
    docx_standard,
    docx_table,
    pdf_attachment,
    pdf_cross_page,
    pdf_nested,
    pdf_no_number,
    pdf_standard,
    pdf_table,
)


def _assert_tree_and_slice(result) -> None:
    ids = {c.id for c in result.clauses}
    prev_start = -1
    for c in result.clauses:
        # 父链完整性：parent 必须存在或为根。
        assert c.parent_id is None or c.parent_id in ids, f"悬空 parent：{c.parent_id}"
        # 硬约束：text 必须等于 raw 切片。
        assert result.raw[c.start : c.end] == c.text, "raw[start:end] 必须等于 text"
        assert 1 <= c.level <= 4
        # 文档顺序：特殊块直接入列而普通条款延迟 flush，必须靠排序恢复阅读顺序。
        assert c.start >= prev_start, f"条款未按文档顺序排列：{c.id} start={c.start}"
        prev_start = c.start


def test_standard_pdf(tmp_path: Path) -> None:
    p = tmp_path / "std.pdf"
    pdf_standard(p)
    r = parse_contract(p)
    _assert_tree_and_slice(r)
    assert len(r.clauses) >= 8
    assert any(c.no == "第一章" for c in r.clauses)
    assert {c.level for c in r.clauses} >= {1, 2, 3, 4}
    roots = [c for c in r.clauses if c.parent_id is None]
    assert len(roots) == 1, "标准合同应有且仅有一个根条款（章）"


def test_standard_docx(tmp_path: Path) -> None:
    p = tmp_path / "std.docx"
    docx_standard(p)
    r = parse_contract(p)
    _assert_tree_and_slice(r)
    # DOCX 标准样本含一个表格，应独立成 table 类型条款。
    assert any(c.type == ClauseType.TABLE for c in r.clauses)
    table = next(c for c in r.clauses if c.type == ClauseType.TABLE)
    assert table.parent_id is not None


def test_no_number_pdf(tmp_path: Path) -> None:
    p = tmp_path / "nonum.pdf"
    pdf_no_number(p)
    r = parse_contract(p)
    _assert_tree_and_slice(r)
    # 无编号但靠字号区分标题，仍应切成多个同级条款。
    assert len(r.clauses) >= 3
    assert all(c.parent_id is None for c in r.clauses)


def test_no_number_docx(tmp_path: Path) -> None:
    p = tmp_path / "nonum.docx"
    docx_no_number(p)
    r = parse_contract(p)
    _assert_tree_and_slice(r)
    assert len(r.clauses) >= 3


def test_nested_pdf(tmp_path: Path) -> None:
    p = tmp_path / "nested.pdf"
    pdf_nested(p)
    r = parse_contract(p)
    _assert_tree_and_slice(r)
    assert len(r.clauses) >= 6
    assert any(c.level == 4 for c in r.clauses)


def test_nested_docx(tmp_path: Path) -> None:
    p = tmp_path / "nested.docx"
    docx_nested(p)
    r = parse_contract(p)
    _assert_tree_and_slice(r)
    assert len(r.clauses) == 7


def test_cross_page_merge(tmp_path: Path) -> None:
    p = tmp_path / "cross.pdf"
    pdf_cross_page(p)
    r = parse_contract(p)
    _assert_tree_and_slice(r)
    # 跨页条款：标题在页 1，正文延续到页 2，应合并为一条且 page 取起始页。
    assert len(r.clauses) == 1
    c = r.clauses[0]
    assert c.page == 1
    assert "仲裁" in c.text, "跨页正文（页 2 内容）应并入同一条款"


def test_table_pdf(tmp_path: Path) -> None:
    p = tmp_path / "tbl.pdf"
    pdf_table(p)
    r = parse_contract(p)
    _assert_tree_and_slice(r)
    tables = [c for c in r.clauses if c.type == ClauseType.TABLE]
    assert tables, "应切出一个表格条款"
    assert tables[0].parent_id is not None


def test_table_docx(tmp_path: Path) -> None:
    p = tmp_path / "tbl.docx"
    docx_table(p)
    r = parse_contract(p)
    _assert_tree_and_slice(r)
    assert any(c.type == ClauseType.TABLE for c in r.clauses)


def test_attachment_pdf(tmp_path: Path) -> None:
    p = tmp_path / "att.pdf"
    pdf_attachment(p)
    r = parse_contract(p)
    _assert_tree_and_slice(r)
    atts = [c for c in r.clauses if c.type == ClauseType.ATTACHMENT]
    assert atts, "图片应切为附件条款"
    assert atts[0].parent_id is not None


def test_attachment_docx(tmp_path: Path) -> None:
    p = tmp_path / "att.docx"
    docx_attachment(p)
    r = parse_contract(p)
    _assert_tree_and_slice(r)
    assert any(c.type == ClauseType.ATTACHMENT for c in r.clauses)


def test_signature_detection_docx(tmp_path: Path) -> None:
    p = tmp_path / "sign.docx"
    doc = DocxDocument()
    doc.add_paragraph("第八条 其他")
    doc.add_paragraph("甲方（盖章）：___________")
    doc.add_paragraph("乙方（盖章）：___________")
    doc.save(str(p))
    r = parse_contract(p)
    _assert_tree_and_slice(r)
    assert any(c.type == ClauseType.SIGNATURE for c in r.clauses)
