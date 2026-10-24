"""文档加载层：把 PDF/DOCX 变成带精确字符偏移的块序列。

为什么单独成层：加载（格式/坐标/字号）与切分（编号/层级）解耦，便于单测各自覆盖。
偏移是硬约束——每个 Block.text 必须严格等于 raw[start:end]，加载层在收尾时统一回填核对。
异常一律带页号抛出（红线：禁止吞掉解析异常）。
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterator
from pathlib import Path
from typing import TypedDict

import pymupdf as fitz
from docx import Document as DocxDocument
from docx.document import Document as _DocxDocument
from docx.oxml.table import CT_Tbl
from docx.oxml.text.paragraph import CT_P
from docx.table import Table as DocxTable
from docx.text.paragraph import Paragraph as DocxParagraph

from app.parse.models import Block, ParseError, RawDocument

_BOLD_FLAG = 0x10  # PyMuPDF span flags 第 5 位表示粗体

# 表格框 (x0, y0, x1, y1)
_Rect = tuple[float, float, float, float]


class _LineInfo(TypedDict):
    """一行的文本与版式线索。

    为什么用 TypedDict 而非 dict[str, object]：下游（表格判定、建块）要按下标取 bbox/lefts，
    object 类型会逼出满屏 cast；TypedDict 让类型自然流动。
    """

    text: str
    sizes: list[float]
    flags: int
    lefts: list[float]
    bbox: list[float] | None


def _push(chunks: list[str], lengths: list[int], text: str) -> tuple[int, int]:
    """把一段文本追加到 raw，返回其 (start, end) 偏移（不含后续分隔符）。"""
    start = lengths[-1] if lengths else 0
    chunks.append(text)
    end = start + len(text)
    lengths.append(end)
    return start, end


def _mode_font_size(blocks: list[Block]) -> float:
    """取正文（text 块）字号的众数作为基准；空则返回 10（兜底，不影响偏移）。

    为什么平局显式取较小字号：statistics.mode 在等频时按数据序取首个，可能取到标题大字号，
    于是「正文被当标题基准、标题反被漏判」；取较小值符合「正文行数通常最多且字号最小」的先验。
    （中位数在标题/正文等频时会算出 12.5 这种双方都不是的幽灵字号，同样不可取。）
    """
    sizes = [b.font_size for b in blocks if b.kind == "text" and b.font_size > 0]
    if not sizes:
        return 10.0
    counts = Counter(sizes)
    top = max(counts.values())
    return float(min(s for s, c in counts.items() if c == top))


def _looks_like_table_page(lines_info: list[_LineInfo]) -> bool:
    """启发式判断本页是否疑似表格页：多数行是短文本，且左边界聚成多列。

    为什么需要：find_tables 单页约 0.1s，80 页纯正文全跑约 9s，远超验收 3s；
    纯正文页左边界近乎一致且行长，可直接跳过昂贵的表格识别。
    """
    if len(lines_info) < 4:
        return False
    short = 0
    lefts: list[float] = []
    for li in lines_info:
        if len(li["text"].strip()) <= 20:
            short += 1
        lefts.extend(li["lefts"])
    if short / len(lines_info) < 0.6:
        return False
    lefts.sort()
    clusters: list[float] = []
    for x in lefts:
        if not clusters or x - clusters[-1] > 6.0:
            clusters.append(x)
    return len(clusters) >= 3


def _table_index_for(bbox: list[float] | None, table_rects: list[_Rect]) -> int:
    """行中心落在哪个表格框内；不在任何框内返回 -1。"""
    if not bbox or not table_rects:
        return -1
    cy = (bbox[1] + bbox[3]) / 2
    cx = (bbox[0] + bbox[2]) / 2
    for i, rect in enumerate(table_rects):
        if rect[0] <= cx <= rect[2] and rect[1] <= cy <= rect[3]:
            return i
    return -1


def _emit_table(
    index: int,
    page_no: int,
    table_texts: list[str],
    chunks: list[str],
    lengths: list[int],
    blocks: list[Block],
    emitted: set[int],
) -> None:
    """把第 index 个表格压成块；同一表格只产出一次，位置即首次命中行处（保持文档顺序）。"""
    if index in emitted or index >= len(table_texts):
        return
    emitted.add(index)
    text = table_texts[index]
    if not text.strip():
        return
    token = f"\u25a3TABLE{page_no}\u25a3\n{text}"
    start, end = _push(chunks, lengths, token)
    blocks.append(Block(page=page_no, text=token, start=start, end=end, kind="table"))
    _push(chunks, lengths, "\n")


def _blocks_from_pdf(path: Path) -> tuple[str, list[Block], str]:
    try:
        # fitz.open 实为 Document 类别名，其 __init__ 无注解，strict 下需显式放行。
        doc = fitz.open(str(path))  # type: ignore[no-untyped-call]
    except Exception as exc:  # PyMuPDF 对坏文件抛 FileDataError 等，统一包装
        raise ParseError(f"无法打开或解析 PDF：{exc}") from exc

    chunks: list[str] = []
    lengths: list[int] = []
    blocks: list[Block] = []
    page_no = 0
    try:
        # 用 page_count + load_page 而非 `for page in doc`：Document 未实现 __iter__，
        # 迭代协议不可见；load_page 是文档化的按页访问入口。
        for pno in range(doc.page_count):
            page = doc.load_page(pno)  # type: ignore[no-untyped-call]  # PyMuPDF 部分方法无注解
            page_no += 1
            try:
                page_dict = page.get_text("dict")
            except Exception as exc:
                raise ParseError(f"页面结构化提取失败：{exc}", page=page_no) from exc

            # 单趟按文档顺序收集：图片块与文本行交错，保持阅读顺序。
            # 为什么不能分两趟（先图后文）：附件/表格节点会脱离所在条款，
            # 出现「图片排到第七条之前 → parent 悬空」的 bug。
            ordered: list[tuple[str, _LineInfo | None]] = []
            lines_info: list[_LineInfo] = []
            for blk in page_dict.get("blocks", []):
                if blk.get("type") != 0:
                    ordered.append(("img", None))
                    continue
                for line in blk.get("lines", []):
                    span_text = "".join(s.get("text", "") for s in line.get("spans", []))
                    if not span_text.strip():
                        continue
                    sizes = [float(s["size"]) for s in line.get("spans", []) if s.get("size")]
                    flags = 0
                    for s in line.get("spans", []):
                        flags |= int(s.get("flags", 0))
                    lefts = [
                        float(s["bbox"][0]) for s in line.get("spans", []) if s.get("bbox")
                    ]
                    info: _LineInfo = {
                        "text": span_text,
                        "sizes": sizes,
                        "flags": flags,
                        "lefts": lefts,
                        "bbox": line.get("bbox"),
                    }
                    ordered.append(("line", info))
                    lines_info.append(info)

            # 表格识别很贵（~0.1s/页）；仅在页面呈多列短文本（疑似表格）时才跑，
            # 纯正文页直接跳过，保证大文档解析性能（验收 80 页 < 3s）。
            table_rects: list[_Rect] = []
            table_texts: list[str] = []
            if _looks_like_table_page(lines_info):
                try:
                    tables = page.find_tables()
                    table_rects = [tuple(t.bbox) for t in tables.tables]
                    for t in tables.tables:
                        rows = t.extract()
                        table_texts.append(
                            "\n".join(
                                "\t".join("" if c is None else str(c) for c in row)
                                for row in rows
                            )
                        )
                except Exception:
                    table_rects = []
                    table_texts = []

            emitted_tables: set[int] = set()

            for item_kind, li in ordered:
                if item_kind == "img":
                    # 图片块 -> 附件占位（真实图片二进制走 MinIO，这里只留锚点）。
                    token = f"\u25a0IMG{page_no}\u25a0"
                    start, end = _push(chunks, lengths, token)
                    blocks.append(
                        Block(page=page_no, text=token, start=start, end=end, kind="attachment")
                    )
                    _push(chunks, lengths, "\n")
                    continue
                assert li is not None
                ti = _table_index_for(li["bbox"], table_rects)
                if ti >= 0:
                    # 表格内容行：在首次命中处产出表格块，保持文档顺序。
                    _emit_table(ti, page_no, table_texts, chunks, lengths, blocks, emitted_tables)
                    continue
                sizes = li["sizes"]
                flags = li["flags"]
                lefts = li["lefts"]
                start, end = _push(chunks, lengths, li["text"])
                blocks.append(
                    Block(
                        page=page_no,
                        text=li["text"],
                        start=start,
                        end=end,
                        font_size=max(sizes) if sizes else 0.0,
                        bold=bool(flags & _BOLD_FLAG),
                        indent=min(lefts) if lefts else 0.0,
                    )
                )
                _push(chunks, lengths, "\n")

            # 未被任何文本行命中的表格（如纯线框无文字）补在页尾。
            for i in range(len(table_texts)):
                _emit_table(i, page_no, table_texts, chunks, lengths, blocks, emitted_tables)
    except ParseError:
        raise
    except Exception as exc:
        raise ParseError(f"PDF 遍历异常：{exc}", page=page_no) from exc
    finally:
        doc.close()  # type: ignore[no-untyped-call]  # PyMuPDF 部分方法无注解

    raw = "".join(chunks)
    for b in blocks:  # 收尾统一回填，保证 text 严格等于 raw[start:end]。
        b.text = raw[b.start : b.end]
    return raw, blocks, "pdf"


def _iter_docx_block_items(parent: _DocxDocument) -> Iterator[object]:
    """按文档顺序产出段落与表格（python-docx 默认不保证二者顺序）。"""
    body = parent.element.body
    for child in body.iterchildren():
        if isinstance(child, CT_P):
            yield DocxParagraph(child, parent)
        elif isinstance(child, CT_Tbl):
            yield DocxTable(child, parent)


def _heading_level(style_name: str) -> int | None:
    """DOCX 标题样式 -> 层级；非标题返回 None。"""
    name = (style_name or "").lower()
    if "title" in name:
        return 1
    if "heading 1" in name or "heading1" in name:
        return 1
    if "heading 2" in name or "heading2" in name:
        return 2
    if "heading 3" in name or "heading3" in name:
        return 3
    if "heading 4" in name or "heading4" in name:
        return 4
    return None


def _blocks_from_docx(path: Path) -> tuple[str, list[Block], str]:
    try:
        document = DocxDocument(str(path))
    except Exception as exc:
        raise ParseError(f"无法打开或解析 DOCX：{exc}") from exc

    chunks: list[str] = []
    lengths: list[int] = []
    blocks: list[Block] = []
    # DOCX 不提供可靠页码，page 取 1（最佳努力；跨页验收以 PDF 为准）。
    page = 1
    try:
        for item in _iter_docx_block_items(document):
            if isinstance(item, DocxParagraph):
                text = item.text
                if not text.strip():
                    continue
                runs = item.runs
                bold = any(bool(r.bold) for r in runs)
                sizes = [float(r.font.size.pt) for r in runs if r.font.size is not None]
                indent_emu = item.paragraph_format.left_indent
                indent = float(indent_emu.pt) if indent_emu is not None else 0.0
                start, end = _push(chunks, lengths, text)
                blocks.append(
                    Block(
                        page=page,
                        text=text,
                        start=start,
                        end=end,
                        font_size=max(sizes) if sizes else 0.0,
                        bold=bold,
                        indent=indent,
                        style_level=_heading_level(item.style.name if item.style else ""),
                    )
                )
                _push(chunks, lengths, "\n")
            elif isinstance(item, DocxTable):
                rows = ["\t".join(cell.text for cell in row.cells) for row in item.rows]
                table_text = "\n".join(rows)
                if not table_text.strip():
                    continue
                token = f"\u25a3TABLE{page}\u25a3\n{table_text}"
                start, end = _push(chunks, lengths, token)
                blocks.append(
                    Block(page=page, text=token, start=start, end=end, kind="table")
                )
                _push(chunks, lengths, "\n")
    except ParseError:
        raise
    except Exception as exc:
        raise ParseError(f"DOCX 遍历异常：{exc}") from exc

    raw = "".join(chunks)
    for b in blocks:
        b.text = raw[b.start : b.end]
    return raw, blocks, "docx"


def load_document(path: str | Path) -> RawDocument:
    """按扩展名分派加载器；统一返回 RawDocument（raw + 块序列）。"""
    p = Path(path)
    suffix = p.suffix.lower()
    if suffix == ".pdf":
        raw, blocks, doc_type = _blocks_from_pdf(p)
    elif suffix in (".docx", ".doc"):
        raw, blocks, doc_type = _blocks_from_docx(p)
    else:
        raise ParseError(f"不支持的文件类型：{suffix or '(无扩展名)'}")

    return RawDocument(raw=raw, blocks=blocks, doc_type=doc_type, source=str(p))
