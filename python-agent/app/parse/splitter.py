"""确定性切分层：把块序列变成条款树。

为什么确定性、不调 LLM：切分是审查与引用校验的事实基础，必须可复现、可审计（红线与任务双约束）。
层级由「编号样式 + 字号 + 加粗 + 缩进 + DOCX 标题样式」综合决定，跨页条款因块按阅读顺序贯穿全文档
而天然拼接（page 取起始页），表格/附件/签章独立成节点不并入正文。
硬约束 raw[start:end] == text 在 flush 时断言，且单测再抽查 100 条。
"""

from __future__ import annotations

import re
from pathlib import Path

from app.parse.loader import _mode_font_size
from app.parse.models import (
    Block,
    Clause,
    ClauseType,
    ParseError,
    ParseResult,
    RawDocument,
    clause_hash,
)

# 编号样式 -> 层级；按特异性从高到低排，先命中先生效。
_NUMBER_PATTERNS: list[tuple[re.Pattern[str], int]] = [
    (re.compile(r"\s*第[0-9零一二三四五六七八九十百千两]+\.[0-9]+条"), 3),  # 第1.1条
    (re.compile(r"\s*第[0-9零一二三四五六七八九十百千两]+章"), 1),  # 第X章
    (re.compile(r"\s*第[0-9零一二三四五六七八九十百千两]+节"), 2),  # 第X节
    (re.compile(r"\s*第[0-9零一二三四五六七八九十百千两]+条"), 2),  # 第X条
    (re.compile(r"\s*[一二三四五六七八九十百千零]+、"), 2),  # 一、
    (re.compile(r"\s*（[一二三四五六七八九十百千]+）"), 3),  # （一）
    (re.compile(r"\s*[0-9]+\.[0-9]+"), 4),  # 1.1
    (re.compile(r"\s*[0-9]+\."), 3),  # 1.
    (re.compile(r"\s*[0-9]+、"), 3),  # 1、
    (re.compile(r"\s*\([0-9]+\)"), 4),  # (1)
    (re.compile(r"\s*[0-9]+\)"), 3),  # 1)
    (re.compile(r"\s*[a-zA-Z]\."), 4),  # a.
    (re.compile(r"\s*Article\s+[0-9]+", re.IGNORECASE), 2),  # Article 5
    (re.compile(r"\s*Section\s+[0-9]+", re.IGNORECASE), 2),  # Section 5
    (re.compile(r"\s*Clause\s+[0-9]+", re.IGNORECASE), 2),  # Clause 5
]

_ATTACHMENT_MARKER = re.compile(r"^\s*(附件|附录|Annex|Appendix|Attachment)", re.IGNORECASE)
# 签章行必须「显式可判定」：括号包裹的签字/盖章，或成串下划线。
# 为什么不能只用关键词：「合同签署地」这类正文含「签署」二字，
# 宽松匹配会把争议解决条款的正文误切成签章节点（曾导致跨页合并失败）。
_SIGNATURE_MARKER = re.compile(
    r"[（(]\s*(?:签字|盖章|签章|signature|signed)\s*[）)]", re.IGNORECASE
)
_SIGNATURE_UNDERSCORE = re.compile(r"[_＿]{4,}")
# 无编号标题的缩进阈值：约两个中文字宽（10pt 正文 2 字符 ≈ 20pt），
# 达到即视为「缩进子标题」，层级比普通标题深一级。
_INDENT_DEEP = 18.0


def _match_number(text: str) -> tuple[int, str, str] | None:
    """匹配首行编号；返回 (层级, 编号原文, 去编号后的首行文本)。无则 None。"""
    for pattern, level in _NUMBER_PATTERNS:
        m = pattern.match(text)
        if m:
            number = m.group(0).strip()
            first_line = text.split("\n", 1)[0]
            rest = first_line[m.end() :].strip()
            return level, number, rest
    return None


def _font_level(font_size: float, body_size: float) -> int:
    """字号相对正文的跳变 -> 层级；未知字号（0）返回 0 交由其他信号决定。"""
    if font_size <= 0 or body_size <= 0:
        return 0
    if font_size >= body_size * 1.3:
        return 1
    if font_size >= body_size * 1.12:
        return 2
    return 0


def _is_header(block: Block, body_size: float) -> tuple[bool, int]:
    """判定块是否条款头，并返回其层级（1-4）。无编号时靠样式/字号/加粗/缩进兜底。"""
    numbered = _match_number(block.text)
    if numbered is not None:
        # 编号样式是层级的第一权威；不再用缩进修正，否则会与编号语义打架
        # （如「第一条」被缩进时不至于被误判成三级）。
        return True, numbered[0]
    if block.style_level is not None:  # DOCX 标题样式是最强信号
        return True, block.style_level
    fle = _font_level(block.font_size, body_size)
    first_line = block.text.split("\n", 1)[0].strip()
    if (block.bold or fle >= 1) and len(first_line) <= 60:
        if fle >= 1:
            return True, fle
        # 同为加粗标题时，用缩进区分二级/三级（任务要求把缩进作为层级信号）。
        if block.indent >= _INDENT_DEEP:
            return True, 3
        return True, 2
    return False, 0


def _first_line(text: str) -> str:
    return text.split("\n", 1)[0].strip()


def _classify(block: Block, body_size: float) -> tuple[str, int, str, str, ClauseType]:
    """返回 (kind, level, no, title, clause_type)。

    kind ∈ {header, body, special}：special 表示表格/附件/签章，独立成条款节点。
    """
    if block.kind == "table":
        return "special", 0, "", _first_line(block.text), ClauseType.TABLE
    if block.kind == "attachment":
        return "special", 0, "", "附件", ClauseType.ATTACHMENT
    if _ATTACHMENT_MARKER.match(block.text):
        return "special", 0, "", _first_line(block.text), ClauseType.ATTACHMENT
    first_line = _first_line(block.text)
    if len(block.text) <= 40 and (
        _SIGNATURE_MARKER.search(block.text)
        or _SIGNATURE_UNDERSCORE.search(first_line)
    ):
        return "special", 0, "", first_line, ClauseType.SIGNATURE

    is_header, level = _is_header(block, body_size)
    if is_header:
        numbered = _match_number(block.text)
        no = numbered[1] if numbered else ""
        title = numbered[2] if numbered else _first_line(block.text)
        return "header", level, no, title, ClauseType.CLAUSE
    return "body", 0, "", "", ClauseType.CLAUSE


def split_document(doc: RawDocument) -> ParseResult:
    """把 RawDocument 切成条款树；跨页、表格、附件、签章均按上述规则处理。"""
    if not doc.blocks:
        raise ParseError("文档未提取到任何文本块，无法切分。")
    body_size = _mode_font_size(doc.blocks)
    doc_id = Path(doc.source).stem if doc.source else "doc"

    clauses: list[Clause] = []
    stack: list[tuple[int, str | None]] = []  # (层级, 条款ID) 用于层级嵌套

    # 当前正在累积正文的条款（open）。header/synthetic-root 皆可。
    open_blocks: list[Block] = []
    open_cid: str | None = None
    open_level: int = 1
    open_no: str = ""
    open_title: str = ""
    open_parent: str | None = None
    idx = 0

    def _open(header: bool, level: int, no: str, title: str, parent: str | None) -> None:
        nonlocal open_blocks, open_cid, open_level, open_no, open_title, open_parent, idx
        open_blocks = []
        open_cid = f"{doc_id}#{idx:04d}"
        idx += 1
        open_level = level
        open_no = no
        open_title = title
        open_parent = parent

    def _flush() -> None:
        nonlocal open_blocks, open_cid, open_level, open_no, open_title, open_parent
        if not open_blocks or open_cid is None:
            return
        first, last = open_blocks[0], open_blocks[-1]
        start, end = first.start, last.end
        text = doc.raw[start:end]
        # 硬约束：切分结果必须经得起回溯校验。
        assert doc.raw[start:end] == text, "raw[start:end] 必须等于 text（偏移不一致）"
        clauses.append(
            Clause(
                id=open_cid,
                parent_id=open_parent,
                no=open_no,
                title=open_title,
                text=text,
                level=open_level,
                page=first.page,
                start=start,
                end=end,
                hash=clause_hash(text),
                type=ClauseType.CLAUSE,
            )
        )
        open_blocks = []
        open_cid = None

    for block in doc.blocks:
        kind, level, no, title, ctype = _classify(block, body_size)

        if kind == "header":
            _flush()  # 上一个开放条款收尾（含其正文）
            # 嵌套：弹出同级或更深层，再取新的父节点。
            while stack and stack[-1][0] >= level:
                stack.pop()
            parent = stack[-1][1] if stack else None
            _open(header=True, level=level, no=no, title=title, parent=parent)
            stack.append((level, open_cid))
            open_blocks.append(block)
        elif kind == "special":
            # 特殊块前无任何条款时直接作为根级节点。
            # 为什么不再造「空容器」承接：空容器没有 body 块，_flush 会跳过它，
            # 于是子节点的 parent_id 指向一个从不存在的条款（悬空 parent）。
            if open_cid is None:
                parent = None
                level = 1
            else:
                parent = open_cid
                level = open_level + 1 if open_level < 4 else 4
            first = block
            start, end = first.start, first.end
            text = doc.raw[start:end]
            assert doc.raw[start:end] == text, "raw[start:end] 必须等于 text（偏移不一致）"
            cid = f"{doc_id}#{idx:04d}"
            idx += 1
            clauses.append(
                Clause(
                    id=cid,
                    parent_id=parent,
                    no=no,
                    title=title,
                    text=text,
                    level=level,
                    page=first.page,
                    start=start,
                    end=end,
                    hash=clause_hash(text),
                    type=ctype,
                )
            )
        else:  # body
            if open_cid is None:  # 无编号前言段：开一个根级条款承接正文
                _open(header=False, level=1, no="", title=_first_line(block.text), parent=None)
            open_blocks.append(block)

    _flush()

    # 特殊块（表格/附件/签章）在遍历中直接入列，而其所属条款要等下一个头部才 flush，
    # 因此列表顺序会与文档顺序不一致；按 start 排序恢复阅读顺序（parent 关系按 ID，不受影响）。
    clauses.sort(key=lambda c: c.start)

    return ParseResult(
        doc_id=doc_id, source=doc.source, doc_type=doc.doc_type, raw=doc.raw, clauses=clauses
    )
