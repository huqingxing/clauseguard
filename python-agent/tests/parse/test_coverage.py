"""解析层覆盖率测试（≥80%）。

为什么不用 pytest-cov：版本表未列该依赖，铁律 5 禁止引入版本表外依赖；此处用标准库
sys.settrace + ast 自行统计语句行覆盖率，既拿到真实数字又不引入新包。
"""

from __future__ import annotations

import ast
import asyncio
import sys
from collections import defaultdict
from pathlib import Path
from unittest import mock

import pymupdf as fitz  # type: ignore[import-untyped]
from docx import Document as DocxDocument

import app.parse as parse_pkg
from app.parse import parse_contract
from app.parse.models import ClauseType, ParseError
from app.parse.repository import InMemoryClauseStore, PostgresClauseStore, create_clauses_table
from app.parse.sample_check import build_report, discover_samples
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


def _excluded_lines(path: Path) -> set[int]:
    """收集带 `# pragma: no cover` 的函数体内所有语句行号。

    pragma 可写在 def 行，也可写在紧邻的上一行（长签名放不下行内注释时用）。
    """
    lines = path.read_text(encoding="utf-8").splitlines()
    tree = ast.parse(path.read_text(encoding="utf-8"))
    excluded: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            window = lines[max(0, node.lineno - 2) : node.lineno]
            if any("pragma: no cover" in ln for ln in window):
                for n in ast.walk(node):
                    if isinstance(n, ast.stmt) and getattr(n, "lineno", None):
                        excluded.add(n.lineno)
    return excluded


def _count_statements(path: Path) -> set[int]:
    """统计「函数体内可执行语句」的行号集合。

    为什么这样取分母：本测试只能在 _exercise 运行窗口内追踪，而模块级语句（import、模块常量、
    def 头、docstring、类体赋值）在 import 时已执行，窗口内不会再产生 line 事件；
    若把它们计入分母，会把 90% 误报成 60%（修复前实测 63.8%）。
    docstring 不产生可执行字节码，同样不计入。
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    excluded = _excluded_lines(path)
    stmts: set[int] = set()

    def is_docstring(node: ast.AST) -> bool:
        return (
            isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        )

    def visit(node: ast.AST, in_function: bool) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.lineno in excluded:
                return
            if in_function:  # 嵌套 def：外层函数执行时才创建，算可执行
                stmts.add(node.lineno)
            for child in node.body:
                visit(child, True)
            return
        if isinstance(node, ast.ClassDef):
            if node.lineno in excluded:
                return
            if in_function:
                stmts.add(node.lineno)
            for child in node.body:
                visit(child, False)  # 类体语句在 import 时执行；只有方法体需要追踪
            return
        if in_function and isinstance(node, (ast.stmt, ast.excepthandler)):
            if getattr(node, "lineno", None) is not None and not is_docstring(node):
                stmts.add(node.lineno)
        for child in ast.iter_child_nodes(node):
            visit(child, in_function)

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            visit(node, False)
    return stmts


def _exercise(tmp_path: Path) -> None:
    """跑遍解析层所有代码路径：六类样本（pdf+docx）、持久化、抽检、异常。"""
    pdf_fns = [pdf_standard, pdf_no_number, pdf_nested, pdf_cross_page, pdf_table, pdf_attachment]
    docx_fns = [docx_standard, docx_no_number, docx_nested, docx_table, docx_attachment]
    for fn in pdf_fns:
        p = tmp_path / f"{fn.__name__}.pdf"
        fn(p)
        parse_contract(p)
    for fn in docx_fns:
        p = tmp_path / f"{fn.__name__}.docx"
        fn(p)
        parse_contract(p)

    # 持久化：内存往返
    clauses = parse_contract(tmp_path / "pdf_standard.pdf").clauses
    store = InMemoryClauseStore()
    asyncio.run(store.save("r1", "std", clauses))
    asyncio.run(store.load("r1", "std"))

    # Postgres 实现（假连接）+ 建表
    conn = _FakeConn()
    pg = PostgresClauseStore(conn)
    asyncio.run(pg.save("r1", "std", clauses))
    asyncio.run(pg.load("r1", "std"))
    asyncio.run(create_clauses_table(conn))

    # 抽检脚本：build_report（含 manifest 比对分支）
    samples = discover_samples(tmp_path)
    report, _ = build_report(samples)
    assert "切片完整性" in report
    # 造一个 manifest 触发边界正确率分支
    ref = parse_contract(tmp_path / "docx_standard.docx").clauses[0]
    manifest_csv = tmp_path / "manifest.csv"
    manifest_csv.write_text(
        "source,no,level,start,end\n"
        f"docx_standard.docx,{ref.no},{ref.level},{ref.start},{ref.end}\n",
        encoding="utf-8",
    )
    from app.parse.sample_check import _load_manifest

    manifest = _load_manifest(manifest_csv)
    _, stats = build_report(
        [tmp_path / "docx_standard.docx"], manifest
    )
    assert stats["boundary_accuracy"] is not None

    # ---- 以下补齐真实代码路径（全部确定性输入，不依赖外部服务）----

    # doc_id 显式指定分支
    custom = parse_contract(tmp_path / "pdf_standard.pdf", doc_id="custom-doc")
    assert custom.doc_id == "custom-doc"

    # 不支持的文件类型
    txt = tmp_path / "note.txt"
    txt.write_text("hello", encoding="utf-8")
    try:
        parse_contract(txt)
        raise AssertionError("不支持的类型应抛 ParseError")
    except ParseError:
        pass

    # 空文档：无任何块 -> split_document 抛「未提取到文本块」
    empty_docx = tmp_path / "empty.docx"
    DocxDocument().save(str(empty_docx))
    try:
        parse_contract(empty_docx)
        raise AssertionError("空文档应抛 ParseError")
    except ParseError:
        pass

    # DOCX 细节：空段落/空表格跳过、Title/Heading3/Heading4 样式层级、签章特殊块
    detail = DocxDocument()
    detail.add_paragraph("")
    detail.add_paragraph("合同标题页", style="Title")
    detail.add_paragraph("第三章 细节", style="Heading 3")
    detail.add_paragraph("第四章 细节", style="Heading 4")
    detail.add_paragraph("正文承接段落。")
    detail.add_table(rows=1, cols=1)
    detail.add_paragraph("甲方（盖章）：____________")
    p_detail = tmp_path / "detail.docx"
    detail.save(str(p_detail))
    r_detail = parse_contract(p_detail)
    assert any(c.type == ClauseType.SIGNATURE for c in r_detail.clauses)

    # 特殊块位于文档最前（无开放条款）-> 根级节点，parent 不悬空
    lead = DocxDocument()
    lt = lead.add_table(rows=2, cols=2)
    lt.rows[0].cells[0].text = "项目"
    lt.rows[0].cells[1].text = "价格"
    lt.rows[1].cells[0].text = "咨询"
    lt.rows[1].cells[1].text = "1000"
    lead.add_paragraph("第一条 总则")
    p_lead = tmp_path / "lead.docx"
    lead.save(str(p_lead))
    r_lead = parse_contract(p_lead)
    assert r_lead.clauses[0].parent_id is None

    # 正文先于任何编号出现 -> 根级前言条款
    pre = DocxDocument()
    pre.add_paragraph("本合同由双方协商一致签订。")
    pre.add_paragraph("第一条 总则")
    p_pre = tmp_path / "pre.docx"
    pre.save(str(p_pre))
    parse_contract(p_pre)

    # 坏 DOCX（打不开）
    bad_docx = tmp_path / "bad.docx"
    bad_docx.write_bytes(b"not docx")
    try:
        parse_contract(bad_docx)
        raise AssertionError("坏 DOCX 应抛 ParseError")
    except ParseError:
        pass

    # 抽检脚本：单文件样本分支 + 解析失败样本（对照表失败分支）
    assert len(discover_samples(tmp_path / "pdf_standard.pdf")) == 1
    build_report([tmp_path / "bad.pdf"])

    # PDF 长正文页：短行占比不足 -> 跳过表格识别
    long_pdf = tmp_path / "long.pdf"
    ldoc = fitz.open()
    lpage = ldoc.new_page(width=595, height=842)
    for i in range(5):
        lpage.insert_text(
            (50, 60 + i * 26),
            "这是一段较长的正文内容，用来让短行占比低于阈值从而跳过表格识别分支。",
            fontsize=10,
            fontname="china-s",
        )
    ldoc.save(str(long_pdf))
    ldoc.close()
    parse_contract(long_pdf)

    # PDF 中等字号无编号标题：字号跳变落在 [1.12, 1.3) 区间 -> level 2
    mid_pdf = tmp_path / "mid.pdf"
    mdoc = fitz.open()
    mpage = mdoc.new_page(width=595, height=842)
    mpage.insert_text((50, 60), "服务范围说明", fontsize=12, fontname="china-s")
    for i in range(4):
        mpage.insert_text(
            (50, 90 + i * 26),
            "本合同项下的服务范围包括咨询、实施与培训三个阶段的具体内容与交付物。",
            fontsize=10,
            fontname="china-s",
        )
    mdoc.save(str(mid_pdf))
    mdoc.close()
    r_mid = parse_contract(mid_pdf)
    assert any(c.level == 2 for c in r_mid.clauses)

    # PDF 页面结构化提取失败 -> 带页号 ParseError（并经 except ParseError: raise 重抛）
    with mock.patch.object(fitz.Page, "get_text", side_effect=RuntimeError("boom")):
        try:
            parse_contract(tmp_path / "pdf_standard.pdf")
            raise AssertionError("get_text 失败应抛 ParseError")
        except ParseError:
            pass

    # 表格识别异常 -> 回落为无表格（不致命，正文照常产出）
    with mock.patch.object(fitz.Page, "find_tables", side_effect=RuntimeError("boom")):
        parse_contract(tmp_path / "pdf_table.pdf")

    # 纯空白行 -> 跳过
    fake_dict: dict[str, object] = {
        "blocks": [
            {
                "type": 0,
                "lines": [
                    {
                        "spans": [
                            {"text": "   ", "size": 10, "flags": 0, "bbox": [50, 50, 90, 60]}
                        ]
                    },
                    {
                        "spans": [
                            {
                                "text": "第一条 总则",
                                "size": 12,
                                "flags": 0,
                                "bbox": [50, 70, 130, 82],
                            }
                        ]
                    },
                ],
            }
        ]
    }
    with mock.patch.object(fitz.Page, "get_text", return_value=fake_dict):
        parse_contract(tmp_path / "pdf_standard.pdf")

    # DOCX 迭代器抛 ParseError -> 经 except ParseError: raise 原样重抛
    from app.parse import loader as loader_module

    with mock.patch.object(
        loader_module, "_iter_docx_block_items", side_effect=ParseError("boom")
    ):
        try:
            parse_contract(tmp_path / "docx_standard.docx")
            raise AssertionError("迭代器失败应抛 ParseError")
        except ParseError:
            pass

    # 异常路径：坏文件 -> ParseError
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"not pdf")
    try:
        parse_contract(bad)
        raise AssertionError("坏文件应抛 ParseError")
    except ParseError:
        pass


class _FakeConn:
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.commits = 0
        self._fetch: list[tuple] = []

    async def execute(self, sql: str, params=None) -> _FakeConn:
        self.calls.append((sql, params))
        return self

    async def executemany(self, sql: str, params) -> _FakeConn:
        self.calls.append((sql, params))
        return self

    async def commit(self) -> None:
        self.commits += 1

    async def fetchall(self) -> list[tuple]:
        return self._fetch


def test_parse_layer_coverage(tmp_path: Path) -> None:
    pkg_dir = Path(parse_pkg.__file__).parent
    files = sorted(pkg_dir.glob("*.py"))
    targets = {str(f) for f in files}

    executed: dict[str, set[int]] = defaultdict(set)

    def tracer(frame: object, event: str, _arg: object) -> object:
        if event == "line":
            fn = frame.f_code.co_filename  # type: ignore[attr-defined]
            if fn in targets:
                executed[fn].add(frame.f_lineno)  # type: ignore[attr-defined]
        return tracer

    sys.settrace(tracer)
    try:
        _exercise(tmp_path)
    finally:
        sys.settrace(None)

    total_stmts = 0
    total_exec = 0
    for f in files:
        stmts = _count_statements(f)
        ex = executed.get(str(f), set())
        total_stmts += len(stmts)
        total_exec += len(stmts & ex)

    rate = total_exec / total_stmts if total_stmts else 1.0
    assert rate >= 0.80, f"解析层语句覆盖率 {rate:.1%} 低于 80%"
