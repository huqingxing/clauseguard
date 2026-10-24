"""Stage 10：用 lxml 直接操作 OOXML，把审查发现写成 Word 批注。

为什么不用 python-docx 的 add_comment：python-docx 至今不支持批注（任务红线）；
且批注必须同时改 document.xml / comments.xml / document.xml.rels / [Content_Types].xml
四个部件，python-docx 的保存流程不感知我们手工插入的部件。只有直接操作这四个部件、
再把其余 zip 条目原样拷贝，才能既挂准批注锚点，又保住原文档样式与页眉页脚。

坐标系（本模块的正确性基石）：锚点偏移来自 app.parse.loader 的 raw——块序列
（段落/表格）按文档顺序拼接、块间以 "\n" 连接。本模块用与 loader 完全一致的
文本语义（即 python-docx 的 CT_P/CT_R 取文规则：段落取直接子级 w:r/w:hyperlink，
run 取 w:t/w:tab/w:br/w:cr/w:noBreakHyphen/w:ptab；表格 token 复刻
"▣TABLE1▣\n" + 行(\t 间隔单元格) 规则）在 document.xml 上重建同一坐标系，
服务层再把重建文本与 loader 的 raw 逐块断言相等——任何语义漂移都在写盘前炸成
RenderError，而不是安静地把批注挂到错误位置。

锚点与高亮：
- 每条 finding 的 span（Clause.start/end，raw 偏移）映射到一段或多段 w:t；
  span 边界落在 run 中部时拆分 run（只拆「单 w:t + 可选 rPr」的纯净 run，
  遇字段/图片等混杂 run 直接报错，绝不静默错位）。
- commentRangeStart -> 原文 span -> commentRangeEnd -> commentReference 四件套
  按 OOXML 规范插入为 w:p 的 run 级兄弟节点；跨段落 span 天然连续。
- 高风险 w:highlight val="yellow"；中风险浅黄用 w:shd fill="FFFFCC"——
  ST_Highlight 枚举没有浅黄档位（darkYellow 是暗黄，方向相反），底纹是
  Word/WPS 原生支持且不触发"需要修复"的浅黄实现。
"""

from __future__ import annotations

import copy
import io
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime

from lxml import etree

from app.graph.state import Citation

# OOXML 命名空间（wordprocessingml 主命名空间 + 包级 rels/content-types）。
W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
CT_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
XML_NS = "http://www.w3.org/XML/1998/namespace"

_COMMENTS_REL_TYPE = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/comments"
)
_COMMENTS_CONTENT_TYPE = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.comments+xml"
)
_XML_DECLARATION = b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\r\n'

_DOCUMENT_PART = "word/document.xml"
_COMMENTS_PART = "word/comments.xml"
_DOC_RELS_PART = "word/_rels/document.xml.rels"
_CONTENT_TYPES_PART = "[Content_Types].xml"

# 风险档位 -> 批注首行中文标签。
_RISK_LABELS: dict[str, str] = {
    "high": "高风险",
    "medium": "中风险",
    "low": "低风险",
    "unverified": "未验证",
}

# w:highlight 在 CT_RPr 序列中位于 sz/szCs 之后、下列元素之前；按此顺序插入，
# 保证生成的 rPr 满足 schema 元素序（严格校验器与 WPS 均认）。
_RPR_AFTER_HIGHLIGHT = frozenset(
    f"{{{W_NS}}}{tag}"
    for tag in (
        "u",
        "effect",
        "bdr",
        "shd",
        "fitText",
        "vertAlign",
        "rtl",
        "cs",
        "em",
        "lang",
        "eastAsianLayout",
        "specVanish",
        "oMath",
    )
)


class RenderError(Exception):
    """回写失败（坐标系漂移 / 不可安全拆分的 run / 损坏的包）一律显式抛出。

    为什么值得单独一类：批注挂错位置比不挂更糟（误导人工审批），任何不确定都
    必须炸掉让上层转 run.failed，禁止静默降级（红线：不做假实现）。
    """


def qn(tag: str) -> str:
    """w: 命名空间限定名。"""
    return f"{{{W_NS}}}{tag}"


@dataclass(frozen=True)
class CommentSpec:
    """一条批注的输入：锚点 span（raw 偏移，含头不含尾）+ 批注内容。"""

    span: tuple[int, int]
    risk_level: str
    risk_type: str
    summary: str
    suggestion: str = ""
    citations: tuple[Citation, ...] = ()


@dataclass(frozen=True)
class TextAnchor:
    """document.xml 中一段可锚定文本：w:t 元素 + 其在 raw 坐标系中的 [start, end)。"""

    element: etree._Element
    start: int
    end: int


@dataclass(frozen=True)
class BlockText:
    """一个块（段落/表格）在 raw 中的文本与区间；与 loader 的 Block 一一对应。"""

    text: str
    start: int
    end: int


@dataclass(frozen=True)
class AnchorMap:
    """重建的全文坐标系：text 应等于 loader 的 raw，blocks 与其逐块对应。"""

    text: str
    blocks: tuple[BlockText, ...]
    anchors: tuple[TextAnchor, ...]


@dataclass(frozen=True)
class WriteResult:
    """一次回写的产物字节与统计（skipped = span 无法映射到任何 w:t 的批注数）。"""

    data: bytes
    comments_written: int
    comments_skipped: int


# 段落/run/表格的文本部件：(w:t 元素或 None, 文本)。None 表示该段文本来自
# tab/break 等不可锚定字符（仍占 raw 偏移，保证后续锚点偏移不错位）。
_Part = tuple[etree._Element | None, str]


@dataclass
class _Seg:
    """span 与 w:t 的交集（可变：拆分后 element 指向拆分产物）。"""

    element: etree._Element
    start: int
    end: int
    a_start: int
    a_end: int


# ---------------------------------------------------------------------------
# 文本重建：与 app.parse.loader（python-docx 语义）逐字符对齐
# ---------------------------------------------------------------------------


def _run_parts(run: etree._Element) -> list[_Part]:
    """run 的文本部件；语义与 python-docx CT_R.text 一致。

    CT_R.text = "".join(str(e) for e in xpath("w:br|w:cr|w:noBreakHyphen|w:ptab|w:t|w:tab"))，
    其中 w:t->原文、w:tab/w:ptab->"\t"、w:cr->"\n"、w:noBreakHyphen->"-"、
    w:br->type=="textWrapping" 时 "\n"（默认值）否则 ""。其余子元素（rPr/
    drawing/instrText/fldChar/sym/object）不贡献文本。
    """
    parts: list[_Part] = []
    for child in run.iterchildren():
        tag = child.tag
        if tag == qn("t"):
            parts.append((child, child.text or ""))
        elif tag in (qn("tab"), qn("ptab")):
            parts.append((None, "\t"))
        elif tag == qn("br"):
            br_type = child.get(qn("type")) or "textWrapping"
            parts.append((None, "\n" if br_type == "textWrapping" else ""))
        elif tag == qn("cr"):
            parts.append((None, "\n"))
        elif tag == qn("noBreakHyphen"):
            parts.append((None, "-"))
    return parts


def _paragraph_parts(paragraph: etree._Element) -> list[_Part]:
    """段落文本部件；语义与 python-docx CT_P.text 一致。

    CT_P.text = "".join(e.text for e in xpath("w:r | w:hyperlink"))——只取直接
    子级的 run 与超链接（超链接内再取其直接子级 run）；w:ins/w:smartTag 等
    包裹层内的 run 不计入（loader 同样不计，两边一致才敢对齐）。
    """
    parts: list[_Part] = []
    for child in paragraph.iterchildren():
        if child.tag == qn("r"):
            parts.extend(_run_parts(child))
        elif child.tag == qn("hyperlink"):
            for run in child.iterchildren(qn("r")):
                parts.extend(_run_parts(run))
    return parts


def _cell_parts(cell: etree._Element) -> list[_Part]:
    """单元格文本部件；语义与 python-docx _Cell.text 一致（直接子级 w:p 以 \n 连接）。

    嵌套表格不在 cell.paragraphs 内，其文本不进 token——与 loader 一致。
    """
    paragraphs = [_paragraph_parts(p) for p in cell.iterchildren(qn("p"))]
    parts: list[_Part] = []
    for index, para in enumerate(paragraphs):
        if index:
            parts.append((None, "\n"))
        parts.extend(para)
    return parts


def _grid_span(cell: etree._Element) -> int:
    """w:tcPr/w:gridSpan，缺省 1。"""
    tc_pr = cell.find(qn("tcPr"))
    if tc_pr is None:
        return 1
    raw = tc_pr.get(qn("gridSpan"))
    if raw is None or not raw.isdigit():
        return 1
    return int(raw)


def _is_vmerge_continue(cell: etree._Element) -> bool:
    tc_pr = cell.find(qn("tcPr"))
    return tc_pr is not None and tc_pr.get(qn("vMerge")) == "continue"


def _row_layout(cells: list[etree._Element]) -> list[tuple[etree._Element, int, int]]:
    """行内每个 tc 的 (元素, 网格偏移, 跨列数)。"""
    layout: list[tuple[etree._Element, int, int]] = []
    offset = 0
    for cell in cells:
        span = _grid_span(cell)
        layout.append((cell, offset, span))
        offset += span
    return layout


def _tc_at_offset(
    layout: list[tuple[etree._Element, int, int]], offset: int
) -> etree._Element | None:
    for cell, start, span in layout:
        if start <= offset < start + span:
            return cell
    return None


def _resolve_vmerge(
    rows: list[list[etree._Element]], row_index: int, offset: int
) -> etree._Element | None:
    """向上查找同网格偏移的非 continue 单元格（python-docx 的递归委托语义）。"""
    for above in range(row_index - 1, -1, -1):
        cell = _tc_at_offset(_row_layout(rows[above]), offset)
        if cell is None:
            return None
        if not _is_vmerge_continue(cell):
            return cell
    return None


def _table_parts(table: etree._Element) -> tuple[list[_Part], str]:
    """表格 token 部件；复刻 loader 的 `▣TABLE1▣\n` + 行(单元格 \t 连接) 规则。

    返回 (部件列表, 单元格拼接文本)。python-docx 的 row.cells 会把 gridSpan>1
    的单元格文本重复多次、竖合并（vMerge=continue）复用上方同格内容——token
    长度与内容都依赖这一展开，故逐格镜像；重复展开的同一批 w:t 在锚点去重时
    按元素身份合并（见 _segments）。
    """
    rows = [list(row.iterchildren(qn("tc"))) for row in table.iterchildren(qn("tr"))]
    row_cell_parts: list[list[list[_Part]]] = []
    for row_index, cells in enumerate(rows):
        row_cells: list[list[_Part]] = []
        for cell, offset, span in _row_layout(cells):
            if _is_vmerge_continue(cell) and row_index > 0:
                resolved = _resolve_vmerge(rows, row_index, offset)
                cell_parts = _cell_parts(resolved if resolved is not None else cell)
            else:
                cell_parts = _cell_parts(cell)
            # gridSpan>1：同一单元格文本按跨列数重复进入 token（与 row.cells 一致）。
            for _ in range(span):
                row_cells.append(cell_parts)
        row_cell_parts.append(row_cells)

    parts: list[_Part] = [(None, "▣TABLE1▣\n")]
    for row_index, row_cells in enumerate(row_cell_parts):
        if row_index:
            parts.append((None, "\n"))
        for cell_index, cell_parts in enumerate(row_cells):
            if cell_index:
                parts.append((None, "\t"))
            parts.extend(cell_parts)
    cell_text = "\n".join(
        "\t".join("".join(text for _, text in cell_parts) for cell_parts in row_cells)
        for row_cells in row_cell_parts
    )
    return parts, cell_text


def _joined(parts: list[_Part]) -> str:
    return "".join(text for _, text in parts)


def _block_maps(body: etree._Element) -> list[list[_Part]]:
    """按文档顺序收集非空块的文本部件（跳过规则与 loader 完全一致）。

    段落：text.strip() 为空则跳过（loader `if not text.strip(): continue`）；
    表格：单元格拼接文本 strip 为空则跳过（注意判据不是含前缀的 token）。
    """
    blocks: list[list[_Part]] = []
    for child in body.iterchildren():
        if child.tag == qn("p"):
            parts = _paragraph_parts(child)
            if not _joined(parts).strip():
                continue
            blocks.append(parts)
        elif child.tag == qn("tbl"):
            parts, cell_text = _table_parts(child)
            if not cell_text.strip():
                continue
            blocks.append(parts)
    return blocks


def build_anchor_map(doc_tree: etree._Element) -> AnchorMap:
    """在 document.xml 上重建 loader 口径的全文坐标系与 w:t 锚点表。"""
    body = doc_tree.find(qn("body"))
    if body is None:
        raise RenderError("document.xml 缺少 w:body，无法建立锚点坐标系")
    blocks = _block_maps(body)
    anchors: list[TextAnchor] = []
    block_texts: list[BlockText] = []
    texts: list[str] = []
    cursor = 0
    for parts in blocks:
        text = _joined(parts)
        block_texts.append(BlockText(text=text, start=cursor, end=cursor + len(text)))
        for element, piece in parts:
            if element is None or not piece:
                cursor += len(piece)
                continue
            anchors.append(
                TextAnchor(element=element, start=cursor, end=cursor + len(piece))
            )
            cursor += len(piece)
        texts.append(text)
        # loader 在每个非空块之后 push 一个 "\n" 分隔符（不计入任何块）。
        cursor += 1
    full_text = "".join(f"{text}\n" for text in texts)
    return AnchorMap(text=full_text, blocks=tuple(block_texts), anchors=tuple(anchors))


# ---------------------------------------------------------------------------
# 锚点应用：span -> 段 -> run 拆分 -> 批注四件套 + 高亮
# ---------------------------------------------------------------------------


def _paragraph_of(element: etree._Element) -> etree._Element:
    """w:t 所属的 w:p（向上穿过 w:hyperlink 等包裹层）。"""
    node = element.getparent()
    while node is not None and node.tag != qn("p"):
        node = node.getparent()
    if node is None:
        raise RenderError("锚点 w:t 不在任何 w:p 内，文档结构异常")
    return node


def _top_level(run: etree._Element) -> etree._Element:
    """run 在段落内容层的顶层节点：run 自身，或其所在的 w:hyperlink。

    commentRangeStart/End 只能作为 w:p 的内容层子元素存在——run 埋在
    w:hyperlink 里时必须相对于超链接插入标记，否则 Word 报「需要修复」。
    """
    node = run
    parent = node.getparent()
    while parent is not None and parent.tag != qn("p"):
        node = parent
        parent = node.getparent()
    return node


def _segments(anchors: tuple[TextAnchor, ...], span: tuple[int, int]) -> list[_Seg]:
    """span 与锚点表的交集（按文档序）；同一 w:t 因表格展开重复出现时按身份去重。"""
    start, end = span
    if end <= start:
        return []
    segments: list[_Seg] = []
    seen: set[int] = set()
    for anchor in anchors:
        if anchor.start >= end or anchor.end <= start:
            continue
        low, high = max(anchor.start, start), min(anchor.end, end)
        if high <= low:
            continue
        if id(anchor.element) in seen:
            continue
        seen.add(id(anchor.element))
        segments.append(
            _Seg(
                element=anchor.element,
                start=low,
                end=high,
                a_start=anchor.start,
                a_end=anchor.end,
            )
        )
    return segments


def _split_run(run: etree._Element, text_el: etree._Element, offset: int) -> etree._Element:
    """在 w:t 的第 offset 个字符处把 run 拆成前后两个 run，返回后段 run。

    只允许拆分「单个 w:t（+ 可选 rPr）」的纯净 run：多 w:t 或含
    instrText/drawing 等非文本子元素的 run 深拷贝会复制字段/图片，直接报错。
    实践中 span 边界都落在块边界（段落整段），拆分是防御性路径。
    """
    foreign = [c for c in run if c.tag != qn("rPr") and c is not text_el]
    if foreign:
        raise RenderError(
            "run 含多个文本子元素或混杂字段/图片，无法安全拆分以对齐批注锚点"
        )
    tail = copy.deepcopy(run)
    run.addnext(tail)
    head_text = text_el.text or ""
    text_el.text = head_text[:offset]
    tail_text = tail.find(qn("t"))
    if tail_text is None:  # pragma: no cover - 上面已校验只有一个 w:t
        raise RenderError("拆分副本缺少 w:t")
    tail_text.text = head_text[offset:]
    return tail


def _make_element(tag: str, attributes: dict[str, str]) -> etree._Element:
    element = etree.Element(qn(tag))
    for key, value in attributes.items():
        element.set(qn(key), value)
    return element


def _make_reference_run(comment_id: int) -> etree._Element:
    """批注引用 run：Word 渲染气泡与「审阅窗格」跳转的锚。"""
    run = etree.Element(qn("r"))
    r_pr = etree.SubElement(run, qn("rPr"))
    style = etree.SubElement(r_pr, qn("rStyle"))
    style.set(qn("val"), "CommentReference")
    reference = etree.SubElement(run, qn("commentReference"))
    reference.set(qn("id"), str(comment_id))
    return run


def _insert_in_rpr_order(r_pr: etree._Element, element: etree._Element) -> None:
    """按 CT_RPr 元素序插入（插到第一个应排在目标之后的元素之前）。"""
    for child in r_pr:
        if child.tag in _RPR_AFTER_HIGHLIGHT:
            child.addprevious(element)
            return
    r_pr.append(element)


def _apply_highlight(run: etree._Element, risk_level: str) -> None:
    """高风险黄色高亮（w:highlight）；中风险浅黄底纹（w:shd）。"""
    r_pr = run.find(qn("rPr"))
    if r_pr is None:
        r_pr = etree.Element(qn("rPr"))
        run.insert(0, r_pr)
    if risk_level == "high":
        for existing in r_pr.findall(qn("highlight")):
            r_pr.remove(existing)
        highlight = _make_element("highlight", {"val": "yellow"})
        _insert_in_rpr_order(r_pr, highlight)
    elif risk_level == "medium":
        for existing in r_pr.findall(qn("shd")):
            r_pr.remove(existing)
        shading = _make_element("shd", {"val": "clear", "color": "auto", "fill": "FFFFCC"})
        _insert_in_rpr_order(r_pr, shading)


def _apply_markers(
    comment_id: int,
    segments: list[_Seg],
    risk_level: str,
) -> bool:
    """把一条批注的四件套与高亮应用到 document.xml（可重入：支持叠加批注）。

    返回是否发生过 run 拆分（拆分后锚点表失效，调用方需重建）。
    """
    # 1) span 边界落在 run 中部的先拆 run，保证批注范围与 span 严格对齐。
    split_happened = False
    for segment in segments:
        remaining = segment.a_end - segment.a_start
        local_start = segment.start - segment.a_start
        local_end = segment.end - segment.a_start
        run = segment.element.getparent()
        if run is None:  # pragma: no cover - 锚点必在 run 内
            raise RenderError("锚点 w:t 没有父 run")
        if local_start > 0:
            tail = _split_run(run, segment.element, local_start)
            tail_text = tail.find(qn("t"))
            if tail_text is not None:
                segment.element = tail_text
            run = tail
            local_end -= local_start
            remaining -= local_start
            local_start = 0
            split_happened = True
        if local_end < remaining:
            _split_run(run, segment.element, local_end)
            split_happened = True

    # 2) 按所属段落分组（span 跨段落时每组独立放标记，范围天然连续）。
    groups: list[tuple[etree._Element, list[_Seg]]] = []
    for segment in segments:
        paragraph = _paragraph_of(segment.element)
        if groups and groups[-1][0] is paragraph:
            groups[-1][1].append(segment)
        else:
            groups.append((paragraph, [segment]))

    # 3) 每组：范围内 run 收齐；首组前插 commentRangeStart，末组后插
    #    commentRangeEnd + commentReference；范围内 run 上高亮。
    for index, (_paragraph, group_segments) in enumerate(groups):
        runs: list[etree._Element] = []
        for segment in group_segments:
            run = segment.element.getparent()
            if run is not None and run not in runs:
                runs.append(run)
        if not runs:  # pragma: no cover - 有段必有 run
            continue
        if index == 0:
            # 顶层节点（可能是 w:hyperlink）之前插起始标记。
            _top_level(runs[0]).addprevious(
                _make_element("commentRangeStart", {"id": str(comment_id)})
            )
        if index == len(groups) - 1:
            end_marker = _make_element("commentRangeEnd", {"id": str(comment_id)})
            last = _top_level(runs[-1])
            last.addnext(end_marker)
            end_marker.addnext(_make_reference_run(comment_id))
        for run in runs:
            _apply_highlight(run, risk_level)
    return split_happened


# ---------------------------------------------------------------------------
# comments.xml / rels / [Content_Types].xml / zip 重打包
# ---------------------------------------------------------------------------


def _comment_lines(spec: CommentSpec) -> list[str]:
    """批注正文行：风险等级+类型+摘要 / 建议 / 逐条引用法条（白名单来源）。"""
    label = _RISK_LABELS.get(spec.risk_level, spec.risk_level or "未知")
    lines = [f"【{label}】{spec.risk_type}｜{spec.summary}"]
    if spec.suggestion:
        lines.append(f"修改建议：{spec.suggestion}")
    if spec.citations:
        for citation in spec.citations:
            lines.append(
                f"引用依据：{citation.title}——{citation.excerpt}（{citation.source_id}）"
            )
    else:
        lines.append("引用依据：（无白名单引用，该结论未经检索依据支撑）")
    return lines


def _build_comment_element(spec: CommentSpec, comment_id: int, author: str) -> etree._Element:
    comment = _make_element(
        "comment",
        {
            "id": str(comment_id),
            "author": author,
            "initials": "CG",
            "date": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
    )
    for index, line in enumerate(_comment_lines(spec)):
        paragraph = etree.SubElement(comment, qn("p"))
        p_pr = etree.SubElement(paragraph, qn("pPr"))
        style = etree.SubElement(p_pr, qn("pStyle"))
        style.set(qn("val"), "CommentText")
        if index == 0:
            # Word 惯例：批注首段带 annotationRef 引用标记。
            marker_run = etree.SubElement(paragraph, qn("r"))
            marker_rpr = etree.SubElement(marker_run, qn("rPr"))
            marker_style = etree.SubElement(marker_rpr, qn("rStyle"))
            marker_style.set(qn("val"), "CommentReference")
            etree.SubElement(marker_run, qn("annotationRef"))
        run = etree.SubElement(paragraph, qn("r"))
        text = etree.SubElement(run, qn("t"))
        text.set(f"{{{XML_NS}}}space", "preserve")
        text.text = line
    return comment


def _serialize(root: etree._Element) -> bytes:
    """UTF-8 声明 + standalone 序列化（与 Word 自家产物一致，双引号声明）。"""
    body: bytes = etree.tostring(root, encoding="UTF-8", xml_declaration=False)
    return _XML_DECLARATION + body


@dataclass(frozen=True)
class _ZipEntry:
    filename: str
    date_time: tuple[int, int, int, int, int, int]
    compress_type: int
    external_attr: int
    data: bytes


def _read_zip(source: bytes) -> list[_ZipEntry]:
    try:
        with zipfile.ZipFile(io.BytesIO(source)) as archive:
            return [
                _ZipEntry(
                    filename=info.filename,
                    date_time=info.date_time,
                    compress_type=info.compress_type,
                    external_attr=info.external_attr,
                    data=archive.read(info.filename),
                )
                for info in archive.infolist()
            ]
    except zipfile.BadZipFile as exc:
        raise RenderError(f"不是有效的 docx（zip 损坏）: {exc}") from exc


def _repack(entries: list[_ZipEntry], replacements: dict[str, bytes]) -> bytes:
    """重打包：条目顺序/时间戳/压缩方式原样保留，仅替换四个目标部件。

     replacements 中不在原包内的部件（典型：新增的 word/comments.xml）必须追加
    写入——rels 引用它之后包内就必须有它，否则 python-docx/Word 直接打不开。
    """
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        written: set[str] = set()
        for entry in entries:
            data = replacements.get(entry.filename, entry.data)
            written.add(entry.filename)
            info = zipfile.ZipInfo(entry.filename, date_time=entry.date_time)
            info.compress_type = entry.compress_type
            info.external_attr = entry.external_attr
            archive.writestr(info, data)
        for filename, data in replacements.items():
            if filename in written:
                continue
            info = zipfile.ZipInfo(filename, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data)
    return buffer.getvalue()


class DocxEditor:
    """在单个 docx 上挂批注的编辑器；锚点表惰性重建（拆分后失效重算）。"""

    def __init__(self, source: bytes) -> None:
        self.source = source
        self._entries = _read_zip(source)
        self._doc_tree: etree._Element = etree.fromstring(self._part_bytes(_DOCUMENT_PART))
        self._anchors: AnchorMap | None = None
        self._anchors_dirty = False
        self._rels_root: etree._Element | None = None
        self._ct_root: etree._Element | None = None

    def _part_bytes(self, part: str) -> bytes:
        for entry in self._entries:
            if entry.filename == part:
                return entry.data
        raise RenderError(f"docx 缺少必需部件: {part}")

    @property
    def anchor_map(self) -> AnchorMap:
        if self._anchors is None or self._anchors_dirty:
            self._anchors = build_anchor_map(self._doc_tree)
            self._anchors_dirty = False
        return self._anchors

    def write_comments(
        self, comments: list[CommentSpec], *, author: str = "ClauseGuard"
    ) -> WriteResult:
        """把批注写进 document.xml 并补齐 comments.xml/rels/content-types。

        逐条应用、每条前按当前树重建段映射：前一条拆分过 run 后，后一条的同一
        span 会重新映射到拆分产物上（叠加批注不错位）。span 无法映射到任何
        w:t（如纯合成 token 跨度）的批注跳过并计数，不写孤儿批注。
        """
        id_offset = self._existing_comment_ids()
        applied: list[tuple[int, CommentSpec]] = []
        skipped = 0
        for spec in comments:
            segments = _segments(self.anchor_map.anchors, spec.span)
            if not segments:
                skipped += 1
                continue
            comment_id = id_offset + len(applied)
            if _apply_markers(comment_id, segments, spec.risk_level):
                self._anchors_dirty = True
            applied.append((comment_id, spec))

        if not applied:
            # 无任何可写批注：原样返回，不引入空 comments.xml（避免无谓改动）。
            return WriteResult(
                data=self.source,
                comments_written=0,
                comments_skipped=skipped,
            )

        comments_root = self._comments_root(id_offset)
        for comment_id, spec in applied:
            comments_root.append(_build_comment_element(spec, comment_id, author))
        self._ensure_comments_relationship()
        self._ensure_comments_content_type()
        assert self._rels_root is not None and self._ct_root is not None

        replacements = {
            _DOCUMENT_PART: _serialize(self._doc_tree),
            _COMMENTS_PART: _serialize(comments_root),
            _DOC_RELS_PART: _serialize(self._rels_root),
            _CONTENT_TYPES_PART: _serialize(self._ct_root),
        }
        return WriteResult(
            data=_repack(self._entries, replacements),
            comments_written=len(applied),
            comments_skipped=skipped,
        )

    # -- 已有 comments.xml / rels / content-types 的合并式处理 --

    def _existing_comment_ids(self) -> int:
        """已有批注的最大 id + 1（幂等重写：不覆盖他人批注）。"""
        for entry in self._entries:
            if entry.filename != _COMMENTS_PART:
                continue
            root = etree.fromstring(entry.data)
            ids = [int(c.get(qn("id"), "-1")) for c in root.findall(qn("comment"))]
            return max(ids, default=-1) + 1
        return 0

    def _comments_root(self, existing_count: int) -> etree._Element:
        """取（或新建）comments.xml 根节点；已有则追加，保留原批注。"""
        if existing_count:
            return etree.fromstring(self._part_bytes(_COMMENTS_PART))
        return etree.Element(qn("comments"), nsmap={"w": W_NS})

    def _ensure_comments_relationship(self) -> None:
        rels_root = etree.fromstring(self._part_bytes(_DOC_RELS_PART))
        for relationship in rels_root.findall(f"{{{PKG_REL_NS}}}Relationship"):
            if relationship.get("Type") == _COMMENTS_REL_TYPE:
                self._rels_root = rels_root
                return
        used = {
            relationship.get("Id")
            for relationship in rels_root.findall(f"{{{PKG_REL_NS}}}Relationship")
        }
        next_id = 1
        while f"rId{next_id}" in used:
            next_id += 1
        relationship = etree.SubElement(rels_root, f"{{{PKG_REL_NS}}}Relationship")
        relationship.set("Id", f"rId{next_id}")
        relationship.set("Type", _COMMENTS_REL_TYPE)
        relationship.set("Target", "comments.xml")
        self._rels_root = rels_root

    def _ensure_comments_content_type(self) -> None:
        ct_root = etree.fromstring(self._part_bytes(_CONTENT_TYPES_PART))
        for override in ct_root.findall(f"{{{CT_NS}}}Override"):
            if override.get("PartName") == "/word/comments.xml":
                self._ct_root = ct_root
                return
        override = etree.SubElement(ct_root, f"{{{CT_NS}}}Override")
        override.set("PartName", "/word/comments.xml")
        override.set("ContentType", _COMMENTS_CONTENT_TYPE)
        self._ct_root = ct_root


def write_annotated_docx(
    source: bytes, comments: list[CommentSpec], *, author: str = "ClauseGuard"
) -> WriteResult:
    """一次性入口：解析 -> 建坐标系 -> 挂批注 -> 重打包。"""
    editor = DocxEditor(source)
    return editor.write_comments(comments, author=author)
