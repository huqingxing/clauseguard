"""loader 层测试：偏移完整性、页码、表格/图片识别、异常带页号。"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.parse.loader import load_document
from app.parse.models import ParseError
from tests.parse.fixtures import (
    docx_standard,
    pdf_attachment,
    pdf_cross_page,
    pdf_standard,
    pdf_table,
)


def _assert_offsets_consistent(path: Path) -> None:
    doc = load_document(path)
    assert doc.raw, "raw 不应为空"
    for b in doc.blocks:
        assert doc.raw[b.start : b.end] == b.text, "Block.text 必须严格等于 raw[start:end]"
        assert b.start >= 0 and b.end <= len(doc.raw)


def test_pdf_blocks_offsets_and_pages(tmp_path: Path) -> None:
    p = tmp_path / "std.pdf"
    pdf_standard(p)
    _assert_offsets_consistent(p)
    doc = load_document(p)
    # 标准样本应含多个文本块，且页码从 1 开始。
    assert doc.doc_type == "pdf"
    assert all(b.page >= 1 for b in doc.blocks)


def test_pdf_cross_page_blocks_have_correct_pages(tmp_path: Path) -> None:
    p = tmp_path / "cross.pdf"
    pdf_cross_page(p)
    doc = load_document(p)
    pages = {b.page for b in doc.blocks}
    assert {1, 2}.issubset(pages), "跨页样本应同时出现第 1、2 页块"


def test_pdf_table_detected(tmp_path: Path) -> None:
    p = tmp_path / "tbl.pdf"
    pdf_table(p)
    doc = load_document(p)
    kinds = {b.kind for b in doc.blocks}
    assert "table" in kinds, "应识别出一个表格块"


def test_pdf_image_becomes_attachment(tmp_path: Path) -> None:
    p = tmp_path / "att.pdf"
    pdf_attachment(p)
    doc = load_document(p)
    assert any(b.kind == "attachment" for b in doc.blocks), "图片块应标记为 attachment"


def test_docx_blocks_offsets(tmp_path: Path) -> None:
    p = tmp_path / "std.docx"
    docx_standard(p)
    _assert_offsets_consistent(p)
    doc = load_document(p)
    assert doc.doc_type == "docx"
    assert any(b.kind == "table" for b in doc.blocks), "DOCX 表格应被识别"


def test_unsupported_type_raises(tmp_path: Path) -> None:
    p = tmp_path / "x.txt"
    p.write_text("hello")
    with pytest.raises(ParseError):
        load_document(p)


def test_corrupt_pdf_raises_parse_error(tmp_path: Path) -> None:
    p = tmp_path / "bad.pdf"
    p.write_bytes(b"not a real pdf %%%%")
    with pytest.raises(ParseError):
        load_document(p)
