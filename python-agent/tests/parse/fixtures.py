"""测试样本生成器：在内存/临时目录构造 PDF 与 DOCX 样本，无需外部二进制。

为什么自带生成器：验收要覆盖「标准/无编号/多层/跨页/表格/附件」六类，靠生成器可确定性复现，
且 CI 不依赖真实合同文件。PDF 用内置 CJK 字体 china-s 保证中文可被提取。
"""

from __future__ import annotations

from pathlib import Path

import pymupdf as fitz  # type: ignore[import-untyped]
from docx import Document as DocxDocument


def _save_pdf(path: Path, pages: list[list[tuple[str, int]]]) -> None:
    """pages: 每页是 [(文本, 字号), ...]；逐条插入以保证各自成块。"""
    doc = fitz.open()
    for layout in pages:
        page = doc.new_page(width=595, height=842)
        y = 60
        for text, size in layout:
            page.insert_text((50, y), text, fontsize=size, fontname="china-s")
            y += 26
    doc.save(str(path))
    doc.close()


def _red_png_bytes() -> bytes:
    """造一个红色方块 PNG（通过绘制页 -> 像素图 -> PNG），用于 PDF 图片（附件）分支。"""
    src = fitz.open()
    sp = src.new_page(width=20, height=20)
    sp.draw_rect(fitz.Rect(0, 0, 20, 20), color=(1.0, 0.0, 0.0), fill=(1.0, 0.0, 0.0))
    png = sp.get_pixmap().tobytes("png")
    src.close()
    return png


def pdf_standard(path: Path) -> None:
    """标准合同：章/条/款/项 + 表格 + 附件 + 签章。"""
    _save_pdf(
        path,
        [
            [
                ("第一章 总则", 16),
                ("第一条 适用范围", 12),
                ("本合同适用于甲乙双方的全部交易。", 10),
                ("第二条 定义", 12),
                ("下列术语含义如下。", 10),
                ("（一）甲方指买方。", 10),
                ("（二）乙方指卖方。", 10),
                ("第三条 费用", 12),
                ("1.1 服务费为合同总额的百分之十。", 10),
                ("1.2 违约金上限为合同总额。", 10),
            ],
        ],
    )


def pdf_no_number(path: Path) -> None:
    """无编号合同：仅靠字号/加粗区分标题与正文。"""
    _save_pdf(
        path,
        [
            [
                ("服务说明", 15),
                ("本文件描述服务内容。", 10),
                ("责任限制", 15),
                ("任何一方不承担间接损失。", 10),
                ("保密义务", 15),
                ("双方应对资料保密。", 10),
            ],
        ],
    )


def pdf_nested(path: Path) -> None:
    """多级嵌套：章 -> 条 -> （一） -> 1.1。"""
    _save_pdf(
        path,
        [
            [
                ("第二章 履行", 16),
                ("第四条 交付", 12),
                ("（一）交付时间", 10),
                ("1.1 首期于签约后三十日。", 10),
                ("1.2 二期于验收后十五日。", 10),
                ("（二）交付方式", 10),
                ("2.1 通过指定平台交付。", 10),
            ],
        ],
    )


def pdf_cross_page(path: Path) -> None:
    """跨页条款：标题在页 1 底部，正文延续到页 2。"""
    _save_pdf(
        path,
        [
            [("第五条 争议解决", 12), ("本条款规定如下。", 10), ("（争议条款开头）", 10)],
            [
                ("任何一方均可提交仲裁。", 10),
                ("仲裁地为合同签署地。", 10),
                ("裁决为终局。", 10),
            ],
        ],
    )


def pdf_table(path: Path) -> None:
    """含表格的合同：表格独立切出。"""
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    y = 60
    page.insert_text((50, y), "第六条 价格表", fontsize=12, fontname="china-s")
    y += 26
    page.insert_text((50, y), "下列价格适用。", fontsize=10, fontname="china-s")
    y += 40
    # 用 find_tables 可识别的网格：画几条线并放置单元格文本。
    rect = fitz.Rect(50, y, 300, y + 60)
    page.draw_rect(rect)
    page.draw_line(rect.tl, rect.tr)
    page.draw_line(rect.bl, rect.br)
    page.draw_line(fitz.Point(50, y + 30), fitz.Point(300, y + 30))
    page.draw_line(fitz.Point(175, y), fitz.Point(175, y + 60))
    page.insert_text((60, y + 20), "项目", fontsize=10, fontname="china-s")
    page.insert_text((185, y + 20), "价格", fontsize=10, fontname="china-s")
    page.insert_text((60, y + 50), "咨询", fontsize=10, fontname="china-s")
    page.insert_text((185, y + 50), "1000", fontsize=10, fontname="china-s")
    doc.save(str(path))
    doc.close()


def pdf_attachment(path: Path) -> None:
    """含图片附件的合同。"""
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    y = 60
    page.insert_text((50, y), "第七条 技术规格", fontsize=12, fontname="china-s")
    y += 40
    page.insert_image(fitz.Rect(50, y, 120, y + 70), stream=_red_png_bytes())
    doc.save(str(path))
    doc.close()


# --------------------------- DOCX ---------------------------


def _add_docx_blocks(doc: DocxDocument, spec: list[tuple[str, str | None, bool]]) -> None:
    """spec: (文本, 样式名|None, 是否加粗)。"""
    for text, style, bold in spec:
        p = doc.add_paragraph(style=style) if style else doc.add_paragraph()
        run = p.add_run(text)
        run.bold = bold


def docx_standard(path: Path) -> None:
    doc = DocxDocument()
    spec = [
        ("第一章 总则", "Heading 1", False),
        ("第一条 适用范围", "Heading 2", False),
        ("本合同适用于甲乙双方的全部交易。", None, False),
        ("第二条 定义", "Heading 2", False),
        ("下列术语含义如下。", None, False),
        ("（一）甲方指买方。", None, False),
        ("（二）乙方指卖方。", None, False),
        ("第三条 费用", "Heading 2", False),
        ("1.1 服务费为合同总额的百分之十。", None, False),
        ("1.2 违约金上限为合同总额。", None, False),
    ]
    _add_docx_blocks(doc, spec)
    table = doc.add_table(rows=2, cols=2)
    table.rows[0].cells[0].text = "项目"
    table.rows[0].cells[1].text = "价格"
    table.rows[1].cells[0].text = "咨询"
    table.rows[1].cells[1].text = "1000"
    doc.save(str(path))


def docx_no_number(path: Path) -> None:
    doc = DocxDocument()
    spec = [
        ("服务说明", None, True),
        ("本文件描述服务内容。", None, False),
        ("责任限制", None, True),
        ("任何一方不承担间接损失。", None, False),
        ("保密义务", None, True),
        ("双方应对资料保密。", None, False),
    ]
    _add_docx_blocks(doc, spec)
    doc.save(str(path))


def docx_nested(path: Path) -> None:
    doc = DocxDocument()
    spec = [
        ("第二章 履行", "Heading 1", False),
        ("第四条 交付", "Heading 2", False),
        ("（一）交付时间", None, False),
        ("1.1 首期于签约后三十日。", None, False),
        ("1.2 二期于验收后十五日。", None, False),
        ("（二）交付方式", None, False),
        ("2.1 通过指定平台交付。", None, False),
    ]
    _add_docx_blocks(doc, spec)
    doc.save(str(path))


def docx_table(path: Path) -> None:
    doc = DocxDocument()
    spec = [
        ("第六条 价格表", "Heading 2", False),
        ("下列价格适用。", None, False),
    ]
    _add_docx_blocks(doc, spec)
    table = doc.add_table(rows=2, cols=2)
    table.rows[0].cells[0].text = "项目"
    table.rows[0].cells[1].text = "价格"
    table.rows[1].cells[0].text = "咨询"
    table.rows[1].cells[1].text = "1000"
    doc.save(str(path))


def docx_attachment(path: Path) -> None:
    doc = DocxDocument()
    spec = [
        ("第七条 补充材料", "Heading 2", False),
        ("附件一：技术规格如下。", None, False),
        ("详见附图与附表。", None, False),
    ]
    _add_docx_blocks(doc, spec)
    doc.save(str(path))
