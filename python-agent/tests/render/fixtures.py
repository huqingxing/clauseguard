"""Stage 10 测试基建：docx 样本生成 + OOXML 结构校验助手。

为什么自带生成器：验收要覆盖 20 份样本回写，样本必须确定性可复现且 CI 不依赖
真实合同文件；每份样本混合多 run 段落、空段、表格、页眉、超链接等结构，专治
「只在理想文档上能跑」的批注实现。
"""

from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass, field

from docx import Document
from lxml import etree

from app.graph.state import Citation, Finding

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
W = f"{{{W_NS}}}"

# 解析层为表格块合成的坐标系占位 token（见 app/parse/splitter.py）。
_TABLE_TOKEN = re.compile(r"▣TABLE\d+▣")

# 样本条款标题池（中文合同风格，确定性按 index 取用）。
_TITLES = (
    "适用范围",
    "合同价款",
    "交付与验收",
    "违约责任",
    "保密义务",
    "知识产权",
    "争议解决",
    "不可抗力",
    "通知与送达",
    "合同解除",
    "费用结算",
    "数据保护",
)

_BODIES = (
    "本合同适用于甲乙双方的全部交易，包括设备买卖、安装调试与售后维保。",
    "买方应在每批次货物验收合格后30日内，凭卖方开具的增值税专用发票支付该批次货款。",
    "卖方逾期交付的，每逾期一日按合同总价的0.001%向买方支付违约金。",
    "双方应对履约过程中知悉的对方商业秘密严格保密，保密期限为合同终止后三年。",
    "因本合同引起的争议，双方应友好协商解决；协商不成的，提交合同签订地仲裁委员会仲裁。",
    "甲方在履约过程中形成的一切改进成果及职务成果归乙方单独所有，甲方不得主张任何权利。",
)


@dataclass(frozen=True)
class DerivedClause:
    """从样本 docx 解析出的条款（span/text 以解析层为权威）。"""

    clause_id: str
    text: str
    start: int
    end: int


@dataclass
class SampleSpec:
    """一份样本的原料：docx 字节 + 派生条款 + 注入的 findings。"""

    data: bytes
    clauses: list[DerivedClause] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)


def _add_paragraph(
    doc: Document,
    text: str,
    *,
    style: str | None = None,
    bold: bool = False,
    runs: list[str] | None = None,
) -> None:
    """加一段；runs 非空时把文本拆到多个 run（专测跨 run 锚点）。"""
    paragraph = doc.add_paragraph(style=style) if style else doc.add_paragraph()
    for piece in runs or [text]:
        run = paragraph.add_run(piece)
        run.bold = bold


def _add_hyperlink(paragraph: object, url: str, text: str) -> None:
    """给段落加超链接（python-docx 无公共 add_hyperlink，按 OOXML 手工构造）。

    必须走 lxml 原生路径：w:hyperlink > w:r > w:t 是 CT_P.text 的取文范围之一，
    正好覆盖 writer 的 hyperlink 锚点分支。
    """
    from docx.opc.constants import RELATIONSHIP_TYPE as RT
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    part = paragraph.part  # type: ignore[attr-defined]
    r_id = part.relate_to(url, RT.HYPERLINK, is_external=True)
    hyperlink = OxmlElement("w:hyperlink")
    hyperlink.set(qn("r:id"), r_id)
    run = OxmlElement("w:r")
    node = OxmlElement("w:t")
    node.text = text
    run.append(node)
    hyperlink.append(run)
    paragraph._p.append(hyperlink)  # type: ignore[attr-defined]


def build_sample_docx(
    index: int,
    *,
    clause_count: int = 8,
    with_header: bool = True,
    with_table: bool = True,
    with_hyperlink: bool = True,
) -> bytes:
    """构造第 index 份样本合同 docx（确定性，无外部依赖）。"""
    doc = Document()
    if with_header:
        header = doc.sections[0].header
        header.paragraphs[0].text = f"ClauseGuard 测试合同 CG-2026-{index:03d}（机密）"

    title = doc.add_paragraph(style="Title")
    title.add_run(f"测试合同 {index:03d}")

    for clause_no in range(1, clause_count + 1):
        _add_paragraph(
            doc,
            f"第{_cn_num(clause_no)}条 {_TITLES[(index + clause_no) % len(_TITLES)]}",
            style="Heading 2",
        )
        body = _BODIES[(index * 3 + clause_no) % len(_BODIES)]
        if clause_no % 3 == 0:
            # 跨 run 段落：同一句话拆成三段（风险词恰在中段）。
            cut = len(body) // 3
            _add_paragraph(doc, body, runs=[body[:cut], body[cut : 2 * cut], body[2 * cut :]])
        else:
            _add_paragraph(doc, body)
        if clause_no % 4 == 0:
            doc.add_paragraph("")  # 空段：loader 跳过，不得进锚点坐标系
        if with_table and clause_no == 3:
            table = doc.add_table(rows=2, cols=2)
            table.rows[0].cells[0].text = "项目"
            table.rows[0].cells[1].text = "价格"
            table.rows[1].cells[0].text = "咨询服务"
            table.rows[1].cells[1].text = "100000"
        if with_hyperlink and clause_no == clause_count:
            paragraph = doc.add_paragraph()
            paragraph.add_run("详见 ")
            _add_hyperlink(paragraph, "https://example.com/terms", "条款说明")
            paragraph.add_run("页面。")

    buffer = io.BytesIO()
    doc.save(buffer)
    return buffer.getvalue()


_CN_DIGITS = "零一二三四五六七八九十"


def _cn_num(value: int) -> str:
    """1-99 -> 中文数字（样本条款编号用，避免引入依赖）。"""
    if value <= 10:
        return _CN_DIGITS[value]
    if value < 20:
        return f"十{_CN_DIGITS[value % 10]}" if value % 10 else "十"
    tens, ones = divmod(value, 10)
    return f"{_CN_DIGITS[tens]}十" + (_CN_DIGITS[ones] if ones else "")


def derive_clauses(data: bytes) -> list[DerivedClause]:
    """跑真实解析层（loader + splitter）拿条款 span——验收对齐的基准。"""
    import tempfile
    from pathlib import Path

    from app.parse.loader import load_document
    from app.parse.splitter import split_document

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "source.docx"
        path.write_bytes(data)
        parsed = split_document(load_document(path))
    return [
        DerivedClause(
            clause_id=clause.id, text=clause.text, start=clause.start, end=clause.end
        )
        for clause in parsed.clauses
    ]


_CITATIONS: dict[str, tuple[Citation, ...]] = {
    "high": (
        Citation(
            source_id="law-civil-566",
            title="中华人民共和国民法典",
            excerpt="第五百六十六条 合同解除后……",
        ),
        Citation(
            source_id="law-civil-577",
            title="中华人民共和国民法典",
            excerpt="第五百七十七条 当事人一方不履行合同义务……",
        ),
    ),
    "medium": (
        Citation(
            source_id="law-civil-585",
            title="中华人民共和国民法典",
            excerpt="第五百八十五条 当事人可以约定一方违约时……",
        ),
    ),
    "low": (),
    "unverified": (),
}


def make_findings(clauses: list[DerivedClause], picks: list[tuple[int, str]]) -> list[Finding]:
    """按 (条款下标, 风险档位) 生成 findings；条款下标越界自动跳过。"""
    findings: list[Finding] = []
    for position, risk_level in picks:
        if position >= len(clauses):
            continue
        clause = clauses[position]
        findings.append(
            Finding(
                clause_id=f"risk-{position:02d}",
                agent="commercial_expert",
                risk_type=_risk_type_for(risk_level),
                risk_level=risk_level,
                summary=f"第{position + 1}条存在{risk_level}档风险：条款表述对委托人不利。",
                suggestion="建议按公平原则调整条款表述，并补充对等义务。",
                citations=list(_CITATIONS.get(risk_level, ())),
                evidence=clause.text[:12],
            )
        )
    return findings


def _risk_type_for(risk_level: str) -> str:
    return {
        "high": "payment_risk",
        "medium": "breach_risk",
        "low": "acceptance_risk",
        "unverified": "succession_risk",
    }.get(risk_level, "general_risk")


def default_picks(clause_count: int) -> list[tuple[int, str]]:
    """默认抽检模式：2 high + 2 medium + 1 low（间隔取，避免相邻条款叠加干扰）。"""
    picks: list[tuple[int, str]] = []
    if clause_count >= 1:
        picks.append((0, "high"))
    if clause_count >= 3:
        picks.append((2, "medium"))
    if clause_count >= 5:
        picks.append((4, "high"))
    if clause_count >= 7:
        picks.append((6, "medium"))
    if clause_count >= 9:
        picks.append((8, "low"))
    return picks


# 风险档位轮换模式：驱动图（专家审查）的样本必须选范围内条款，否则该条款
# 不进任何专家分片（dispatch 按 scope_keywords 过滤），图里不可能有 finding。
_LEVEL_PATTERN = ("high", "medium", "high", "medium", "low")


def scoped_picks(
    clauses: list[DerivedClause], levels: tuple[str, ...] = _LEVEL_PATTERN
) -> list[tuple[int, str]]:
    """只选至少一个专家范畴内的条款位置，按 levels 轮换派档位。

    为什么需要它：tests/render 的样本含文档标题段与表格段（无专家关键词），
    dispatch 不会把它们派给任何专家——用这些位置当 finding 的 clause 时，
    图驱动验收（专家->critic->审批->render）永远等不到 finding。
    """
    from app.agents.registry import EXPERTS

    positions = [
        index
        for index, clause in enumerate(clauses)
        if any(expert.in_scope(clause.text) for expert in EXPERTS)
    ]
    return [
        (position, levels[index % len(levels)]) for index, position in enumerate(positions)
    ]


def build_sample(
    index: int,
    *,
    clause_count: int = 8,
    picks: list[tuple[int, str]] | None = None,
    with_header: bool = True,
    with_table: bool = True,
    with_hyperlink: bool = True,
) -> SampleSpec:
    """一份完整样本：docx + 派生条款 + findings。

    条款 id 统一编为 risk-{下标:02d}（同时作为 ReviewState.clauses 的 clause_id），
    findings 按 picks 引用同键——与生产链路「state.clauses(clause_id->text) x
    解析层(text->span) x findings(clause_id)」的 join 口径一致。
    """
    data = build_sample_docx(
        index,
        clause_count=clause_count,
        with_header=with_header,
        with_table=with_table,
        with_hyperlink=with_hyperlink,
    )
    parsed = derive_clauses(data)
    clauses = [
        DerivedClause(
            clause_id=f"risk-{position:02d}",
            text=clause.text,
            start=clause.start,
            end=clause.end,
        )
        for position, clause in enumerate(parsed)
    ]
    resolved = picks if picks is not None else default_picks(len(clauses))
    findings = make_findings(clauses, resolved)
    return SampleSpec(data=data, clauses=clauses, findings=findings)


# ---------------------------------------------------------------------------
# OOXML 结构校验助手（验收与单测共用）
# ---------------------------------------------------------------------------


def docx_parts(data: bytes) -> dict[str, bytes]:
    """docx zip -> {条目名: 字节}；顺带做 CRC 完整性校验。"""
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        bad = archive.testzip()
        if bad is not None:
            raise AssertionError(f"zip 条目 CRC 校验失败: {bad}")
        return {info.filename: archive.read(info.filename) for info in archive.infolist()}


def parse_part(data: bytes, part: str) -> etree._Element:
    return etree.fromstring(docx_parts(data)[part])


def comment_range_text(data: bytes, comment_id: int) -> str:
    """提取 commentRangeStart(id)..commentRangeEnd(id) 覆盖的原文（w:t 拼接）。

    只收 w:t：tab/break 与块间分隔符不在任何 w:t 内，故与条款 span 文本的
    比较需先剥离空白（见 normalize_span_text）。
    """
    dom = parse_part(data, "word/document.xml")
    collecting = False
    chunks: list[str] = []
    for element in dom.iter():
        if element.tag == f"{W}commentRangeStart" and element.get(f"{W}id") == str(comment_id):
            collecting = True
            continue
        if element.tag == f"{W}commentRangeEnd" and element.get(f"{W}id") == str(comment_id):
            break
        if collecting and element.tag == f"{W}t":
            chunks.append(element.text or "")
    return "".join(chunks)


def normalize_span_text(text: str) -> str:
    """剥离全部空白与表格合成 token 后比较：批注范围覆盖的可见字符必须与条款 span 一致。

    合成 token（▣TABLE1▣）是解析层为表格块造的坐标系占位，文档中无对应 w:t，
    任何正确的锚点实现都不可能覆盖它——比较前必须剔除，否则永远不等。
    """
    return "".join(_TABLE_TOKEN.sub("", text).split())


def comment_bodies(data: bytes) -> dict[int, list[str]]:
    """comments.xml -> {id: [段落文本, ...]}。"""
    root = parse_part(data, "word/comments.xml")
    bodies: dict[int, list[str]] = {}
    for comment in root.findall(f"{W}comment"):
        comment_id = int(comment.get(f"{W}id", "-1"))
        bodies[comment_id] = [
            "".join(node.text or "" for node in paragraph.iter(f"{W}t"))
            for paragraph in comment.findall(f"{W}p")
        ]
    return bodies


def validate_docx_structure(
    data: bytes,
    *,
    expected_comment_count: int,
    source: bytes | None = None,
) -> dict[int, list[str]]:
    """结构完整性强校验（Word/WPS「需要修复」的最大自动化代理）：

    - zip CRC 完整；python-docx 可重新打开；
    - commentRangeStart/End/commentReference 与 comments.xml 的 id 集合一致且数量相等；
    - document.xml.rels 有 comments 关系、[Content_Types].xml 有 comments Override；
    - 四个部件均为 UTF-8 声明；
    - 页眉等无关部件与源逐字节一致（不重排原文、不坏样式）。
    """
    from docx import Document as DocxDocument

    parts = docx_parts(data)
    for required in ("word/document.xml", "word/comments.xml", "[Content_Types].xml"):
        assert required in parts, f"缺少部件 {required}"
    DocxDocument(io.BytesIO(data))  # 打不开即 AssertionError/异常

    dom = etree.fromstring(parts["word/document.xml"])
    comments_root = etree.fromstring(parts["word/comments.xml"])
    starts = {e.get(f"{W}id") for e in dom.findall(f".//{W}commentRangeStart")}
    ends = {e.get(f"{W}id") for e in dom.findall(f".//{W}commentRangeEnd")}
    refs = {e.get(f"{W}id") for e in dom.findall(f".//{W}commentReference")}
    declared = {c.get(f"{W}id") for c in comments_root.findall(f"{W}comment")}
    assert starts == ends == refs == declared, (
        f"批注 id 不一致: start={sorted(starts)} end={sorted(ends)} "
        f"ref={sorted(refs)} comments={sorted(declared)}"
    )
    assert len(declared) == expected_comment_count, (
        f"批注数量不符: {len(declared)} != {expected_comment_count}"
    )
    assert comments_root.tag == f"{W}comments", "comments.xml 根元素必须是 w:comments"

    rels = etree.fromstring(parts["word/_rels/document.xml.rels"])
    rel_types = [r.get("Type") for r in rels]
    assert any(t and t.endswith("/comments") for t in rel_types), "rels 缺少 comments 关系"
    content_types = etree.fromstring(parts["[Content_Types].xml"])
    overrides = {o.get("PartName") for o in content_types}
    assert "/word/comments.xml" in overrides, "[Content_Types].xml 缺少 comments Override"

    for part in ("word/document.xml", "word/comments.xml", "word/_rels/document.xml.rels"):
        assert parts[part].startswith(b'<?xml version="1.0" encoding="UTF-8"'), (
            f"{part} 不是 UTF-8 声明开头"
        )
        parts[part].decode("utf-8")  # 乱码直接 UnicodeDecodeError

    if source is not None:
        source_parts = docx_parts(source)
        for name, blob in source_parts.items():
            if name in parts and name not in {
                "word/document.xml",
                "word/_rels/document.xml.rels",
                "[Content_Types].xml",
            }:
                assert parts[name] == blob, f"无关部件被改动: {name}（样式/页眉页脚必须原样）"

    return comment_bodies(data)
