"""docx_writer 单测：坐标系对齐、跨 run 锚点、四件套、高亮、幂等重写。

覆盖 Stage 10 任务的三条硬要求：
1. 用 Clause.start/end 全文偏移重建 run 级锚点，跨 run 的 span 拆成多段且
   commentRange 连续；
2. 批注内容含风险等级/类型/建议/引用法条，高风险黄色高亮、中风险浅黄；
3. 中文不乱码、原文档样式与页眉页脚原样保留、python-docx 可重开。
"""

from __future__ import annotations

import io

import pytest
from docx import Document
from lxml import etree

from app.graph.state import Citation
from app.render.docx_writer import (
    CommentSpec,
    DocxEditor,
    RenderError,
    build_anchor_map,
    write_annotated_docx,
)
from tests.render.fixtures import (
    W,
    build_sample,
    comment_range_text,
    derive_clauses,
    docx_parts,
    normalize_span_text,
    validate_docx_structure,
)


def _citation() -> Citation:
    return Citation(
        source_id="law-civil-585",
        title="中华人民共和国民法典",
        excerpt="第五百八十五条 当事人可以约定一方违约时……",
    )


def _spec(span: tuple[int, int], risk_level: str = "high") -> CommentSpec:
    return CommentSpec(
        span=span,
        risk_level=risk_level,
        risk_type="payment_risk",
        summary="预付100%且无返还机制，资金风险极高。",
        suggestion="改为「预付30%+验收后付尾款」结构。",
        citations=(_citation(),),
    )


# ---- 坐标系：与解析层（loader raw）逐字符对齐 ----


def test_anchor_map_matches_loader_coordinate_system() -> None:
    """writer 重建的 raw/块文本必须与 loader 完全一致（否则批注会挂错位置）。"""
    sample = build_sample(1, clause_count=6, picks=[])
    editor = DocxEditor(sample.data)
    anchor_map = editor.anchor_map
    parsed = derive_clauses(sample.data)
    # 逐条款核对：span 文本必须能从锚点表原样拼回。
    for clause in parsed:
        covered = "".join(
            anchor.element.text or ""
            for anchor in anchor_map.anchors
            if anchor.start < clause.end and anchor.end > clause.start
        )
        assert normalize_span_text(covered) == normalize_span_text(clause.text)


def test_anchor_map_skips_empty_paragraphs_and_covers_tables() -> None:
    """空段不进坐标系；表格单元格文本进坐标系（token 前缀不可锚定）。"""
    doc = Document()
    doc.add_paragraph("第一条 测试")
    doc.add_paragraph("")
    doc.add_paragraph("正文段落。")
    table = doc.add_table(rows=1, cols=2)
    table.rows[0].cells[0].text = "项目"
    table.rows[0].cells[1].text = "价格"
    buffer = io.BytesIO()
    doc.save(buffer)

    parts = docx_parts(buffer.getvalue())
    anchor_map = build_anchor_map(etree.fromstring(parts["word/document.xml"]))
    assert anchor_map.text == "第一条 测试\n正文段落。\n▣TABLE1▣\n项目\t价格\n"
    assert [block.text for block in anchor_map.blocks] == [
        "第一条 测试",
        "正文段落。",
        "▣TABLE1▣\n项目\t价格",
    ]
    # 空段不产生锚点；表格两个单元格各一个锚点（合成前缀不可锚定）。
    assert [a.element.text for a in anchor_map.anchors] == [
        "第一条 测试",
        "正文段落。",
        "项目",
        "价格",
    ]


# ---- 锚点应用：四件套 + 对齐 ----


def test_comment_markers_wrap_exact_clause_span() -> None:
    """commentRangeStart/End/Reference 与条款 span 严格对齐（含多段条款）。"""
    sample = build_sample(2, clause_count=6, picks=[])
    parsed = derive_clauses(sample.data)
    target = parsed[0]  # 第一条：标题段 + 正文段（跨段条款）
    result = write_annotated_docx(sample.data, [_spec((target.start, target.end))])

    bodies = validate_docx_structure(
        result.data, expected_comment_count=1, source=sample.data
    )
    covered = comment_range_text(result.data, 0)
    assert normalize_span_text(covered) == normalize_span_text(target.text)
    # 批注内容：风险等级 + 类型 + 建议 + 引用法条（中文无乱码）。
    lines = bodies[0]
    assert lines[0] == "【高风险】payment_risk｜预付100%且无返还机制，资金风险极高。"
    assert lines[1] == "修改建议：改为「预付30%+验收后付尾款」结构。"
    assert "中华人民共和国民法典" in lines[2]
    assert "law-civil-585" in lines[2]


def test_cross_run_span_splits_into_multiple_segments() -> None:
    """span 覆盖多 run 时拆段覆盖全部 run，commentRange 连续不间断。"""
    doc = Document()
    paragraph = doc.add_paragraph()
    paragraph.add_run("买方应在合同签订后")
    paragraph.add_run("3个工作日内")
    paragraph.add_run("支付合同总价的100%作为预付款，该款项不予退还。")
    buffer = io.BytesIO()
    doc.save(buffer)
    data = buffer.getvalue()

    anchor_map = build_anchor_map(etree.fromstring(docx_parts(data)["word/document.xml"]))
    span = (0, anchor_map.blocks[0].end)
    result = write_annotated_docx(data, [_spec(span, risk_level="medium")])

    covered = comment_range_text(result.data, 0)
    assert normalize_span_text(covered) == normalize_span_text(anchor_map.blocks[0].text)
    # 三个 run 都落在范围内（高亮/底纹逐个覆盖，证明拆段成功）。
    dom = etree.fromstring(docx_parts(result.data)["word/document.xml"])
    shaded = dom.findall(f".//{W}shd")
    assert len(shaded) == 3
    assert all(shading.get(f"{W}fill") == "FFFFCC" for shading in shaded)


def test_highlight_colors_by_risk_level() -> None:
    """高风险黄色高亮（w:highlight=yellow）；中风险浅黄（w:shd=FFFFCC）；低风险不着色。"""
    doc = Document()
    doc.add_paragraph("高风险条款文本甲。")
    doc.add_paragraph("中风险条款文本乙。")
    doc.add_paragraph("低风险条款文本丙。")
    buffer = io.BytesIO()
    doc.save(buffer)
    data = buffer.getvalue()

    anchor_map = build_anchor_map(etree.fromstring(docx_parts(data)["word/document.xml"]))
    blocks = anchor_map.blocks
    result = write_annotated_docx(
        data,
        [
            _spec((blocks[0].start, blocks[0].end), risk_level="high"),
            _spec((blocks[1].start, blocks[1].end), risk_level="medium"),
            _spec((blocks[2].start, blocks[2].end), risk_level="low"),
        ],
    )
    dom = etree.fromstring(docx_parts(result.data)["word/document.xml"])
    highlights = [h.get(f"{W}val") for h in dom.findall(f".//{W}highlight")]
    shadings = [s.get(f"{W}fill") for s in dom.findall(f".//{W}shd")]
    assert highlights == ["yellow"]  # 只有高风险段
    assert shadings == ["FFFFCC"]  # 只有中风险段


def test_mid_run_boundary_is_split_exactly() -> None:
    """span 起止落在 run 中部时：边界外文本不得进批注范围（精确对齐）。"""
    doc = Document()
    paragraph = doc.add_paragraph()
    paragraph.add_run("前缀甲乙丙")
    paragraph.add_run("中段风险表述")
    paragraph.add_run("后缀丁戊己")
    buffer = io.BytesIO()
    doc.save(buffer)
    data = buffer.getvalue()

    anchor_map = build_anchor_map(etree.fromstring(docx_parts(data)["word/document.xml"]))
    full = anchor_map.blocks[0].text
    # 只锚定「中段风险表述」：前缀「前缀甲乙丙」5 字，故起 5 止 11。
    assert full == "前缀甲乙丙中段风险表述后缀丁戊己"
    result = write_annotated_docx(data, [_spec((5, 11))])
    covered = comment_range_text(result.data, 0)
    assert covered == "中段风险表述"
    # 原文总文本不变（不重排原文）。
    reopened = Document(io.BytesIO(result.data))
    assert reopened.paragraphs[0].text == full


def test_unmappable_span_is_skipped_not_written() -> None:
    """span 完全落在合成 token 区（无 w:t）时跳过该批注，不写孤儿批注。"""
    doc = Document()
    doc.add_paragraph("正文。")
    table = doc.add_table(rows=1, cols=1)
    table.rows[0].cells[0].text = "表格内容"
    buffer = io.BytesIO()
    doc.save(buffer)
    data = buffer.getvalue()
    anchor_map = build_anchor_map(etree.fromstring(docx_parts(data)["word/document.xml"]))
    table_block = anchor_map.blocks[-1]
    # 只锚 token 合成前缀「▣TABLE1▣\n」部分（无对应 w:t）。
    prefix_end = table_block.start + len("▣TABLE1▣\n")
    result = write_annotated_docx(data, [_spec((table_block.start, prefix_end))])
    assert result.comments_written == 0
    assert result.comments_skipped == 1
    assert result.data == data  # 原样返回，不引入空 comments.xml


def test_zero_comments_returns_source_unchanged() -> None:
    """无 findings 时不产生 comments.xml，产物与源逐字节一致。"""
    sample = build_sample(3, clause_count=4, picks=[])
    result = write_annotated_docx(sample.data, [])
    assert result.comments_written == 0
    assert result.data == sample.data
    assert "word/comments.xml" not in docx_parts(result.data)


def test_rewrite_merges_with_existing_comments() -> None:
    """对已挂批注的文档再回写：追加而非覆盖（幂等重写不丢历史批注）。"""
    sample = build_sample(4, clause_count=4, picks=[])
    parsed = derive_clauses(sample.data)
    first = write_annotated_docx(sample.data, [_spec((parsed[0].start, parsed[0].end))])
    second = write_annotated_docx(first.data, [_spec((parsed[1].start, parsed[1].end))])

    bodies = validate_docx_structure(second.data, expected_comment_count=2)
    assert sorted(bodies) == [0, 1]
    assert normalize_span_text(comment_range_text(second.data, 0)) == normalize_span_text(
        parsed[0].text
    )
    assert normalize_span_text(comment_range_text(second.data, 1)) == normalize_span_text(
        parsed[1].text
    )


def test_corrupt_docx_raises_render_error() -> None:
    """非 zip 字节直接 RenderError（不静默产出坏文件）。"""
    with pytest.raises(RenderError):
        write_annotated_docx(b"not a zip file at all", [_spec((0, 1))])


def test_hyperlink_run_marker_stays_at_paragraph_level() -> None:
    """锚点 run 在 w:hyperlink 内时，标记必须插在段落内容层（否则 Word 报修复）。"""
    from tests.render.fixtures import _add_hyperlink

    doc = Document()
    paragraph = doc.add_paragraph()
    paragraph.add_run("详见")
    _add_hyperlink(paragraph, "https://example.com", "条款说明")
    paragraph.add_run("页面。")
    buffer = io.BytesIO()
    doc.save(buffer)
    data = buffer.getvalue()

    anchor_map = build_anchor_map(etree.fromstring(docx_parts(data)["word/document.xml"]))
    span = (anchor_map.blocks[0].start, anchor_map.blocks[0].end)
    result = write_annotated_docx(data, [_spec(span)])
    dom = etree.fromstring(docx_parts(result.data)["word/document.xml"])
    for marker in dom.findall(f".//{W}commentRangeStart"):
        assert marker.getparent().tag == f"{W}p", "commentRangeStart 不得埋在 hyperlink 内"
    assert normalize_span_text(comment_range_text(result.data, 0)) == normalize_span_text(
        anchor_map.blocks[0].text
    )


def test_overlapping_comments_on_same_clause_both_anchor() -> None:
    """同一条款两条 finding（同 span）叠加：两条批注都挂准，不错位。"""
    sample = build_sample(5, clause_count=4, picks=[])
    parsed = derive_clauses(sample.data)
    span = (parsed[0].start, parsed[0].end)
    result = write_annotated_docx(
        sample.data, [_spec(span, risk_level="high"), _spec(span, risk_level="medium")]
    )
    validate_docx_structure(result.data, expected_comment_count=2, source=sample.data)
    for comment_id in (0, 1):
        assert normalize_span_text(comment_range_text(result.data, comment_id)) == (
            normalize_span_text(parsed[0].text)
        )


def test_zip_integrity_after_repack() -> None:
    """重打包后 zip CRC 完整、条目不丢（Word 打不开的第一嫌疑就是包坏）。"""
    sample = build_sample(6, clause_count=5, picks=[])
    parsed = derive_clauses(sample.data)
    result = write_annotated_docx(sample.data, [_spec((parsed[0].start, parsed[0].end))])
    source_names = set(docx_parts(sample.data))
    output_names = set(docx_parts(result.data))
    assert output_names - source_names == {"word/comments.xml"}
    assert source_names - output_names == set()
