"""性能与规模测试：80 页 PDF 解析+切分 < 3s；随机抽 100 条断言 raw[start:end]==text。"""

from __future__ import annotations

import random
import time
from pathlib import Path

import pymupdf as fitz  # type: ignore[import-untyped]

from app.parse import parse_contract


def _make_80_page_pdf(path: Path) -> int:
    """造 80 页、每页 2 个编号条款 + 5 行正文；返回条款数（供抽样）。"""
    doc = fitz.open()
    for page_no in range(1, 81):
        page = doc.new_page(width=595, height=842)
        y = 60
        for k in range(2):
            head = f"第{page_no}.{k}条 条款标题"
            page.insert_text((50, y), head, fontsize=12, fontname="china-s")
            y += 24
            for j in range(5):
                body = f"本页第{page_no}条的第{k}段第{j}行正文内容。"
                page.insert_text((50, y), body, fontsize=10, fontname="china-s")
                y += 20
    doc.save(str(path))
    doc.close()
    return 80 * 2


def test_80_page_parse_under_3s(tmp_path: Path) -> None:
    p = tmp_path / "big.pdf"
    _make_80_page_pdf(p)
    start = time.perf_counter()
    result = parse_contract(p)
    elapsed = time.perf_counter() - start
    assert elapsed < 3.0, f"80 页解析+切分耗时 {elapsed:.2f}s，超过 3s 上限"
    assert len(result.clauses) > 100


def test_random_100_slice_integrity(tmp_path: Path) -> None:
    p = tmp_path / "big.pdf"
    _make_80_page_pdf(p)
    result = parse_contract(p)
    assert len(result.clauses) >= 100
    rng = random.Random(20261023)
    sample = rng.sample(result.clauses, 100)
    for c in sample:
        assert result.raw[c.start : c.end] == c.text, "随机抽样的条款必须满足 raw[start:end]==text"
