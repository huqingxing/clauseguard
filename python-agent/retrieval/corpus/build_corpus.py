"""把维基文库 wikitext 快照解析成逐条法条 jsonl（离线、确定性、可重跑）。

为什么保留原始快照再解析，而不是直接提交 jsonl：语料必须能追溯来源并逐字复核；
网络不可用时也要能重建，否则指标复现就依赖外部服务可用性。

为什么不用 LLM 或语义方法提取：法条本身带「第X条」硬标记，规则解析 100% 可复现，
且不会把法条原文改写掉——检索实验台要求的是可对照的真实文本。

用法（在 python-agent/ 下）：
    .venv/Scripts/python.exe -m retrieval.corpus.build_corpus
输出：
    retrieval/corpus/{civil_code,company_law,labor_contract_law}.jsonl
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from retrieval.numerals import cn_number

CORPUS_DIR = Path(__file__).resolve().parent
WIKITEXT_DIR = CORPUS_DIR / "wikitext"

# 条首一定是 {{gap}}'''第X条'''　正文；款行是 {{gap}}正文。两者之外的非空行
# 都是页面模板/导航，解析时统一跳过（用 unmatched 统计暴露意外格式，防止静默丢正文）。
_ARTICLE_HEAD = re.compile(
    r"^\{\{gap\}\}\s*'''(第[〇零一二三四五六七八九十百千两]+条)'''\s*(.*)$", re.I
)
_GAP_LINE = re.compile(r"^\{\{gap\}\}\s*(.*)$", re.I)
_HEADING = re.compile(r"^(={2,6})\s*(.+?)\s*\1\s*$")
_WIKI_LINK = re.compile(r"\[\[([^\[\]]+)\]\]")
_HTML_TAG = re.compile(r"<[^<>]+>")
_TEMPLATE = re.compile(r"\{\{[^{}]*\}\}")

# (快照文件, 输出文件, law, law_code, 兜底章节名, 期望条数)
SOURCES: tuple[tuple[str, str, str, str, str, int], ...] = (
    ("civil_code_01_general.txt", "civil_code.jsonl", "民法典", "civil_code", "第一编 总则", 1260),
    ("civil_code_02_property.txt", "civil_code.jsonl", "民法典", "civil_code", "第二编 物权", 1260),
    ("civil_code_03_contract.txt", "civil_code.jsonl", "民法典", "civil_code", "第三编 合同", 1260),
    ("civil_code_04_personality.txt", "civil_code.jsonl", "民法典", "civil_code",
     "第四编 人格权", 1260),
    ("civil_code_05_marriage.txt", "civil_code.jsonl", "民法典", "civil_code",
     "第五编 婚姻家庭", 1260),
    ("civil_code_06_succession.txt", "civil_code.jsonl", "民法典", "civil_code",
     "第六编 继承", 1260),
    ("civil_code_07_tort.txt", "civil_code.jsonl", "民法典", "civil_code", "第七编 侵权责任", 1260),
    ("civil_code_08_supplementary.txt", "civil_code.jsonl", "民法典", "civil_code", "附则", 1260),
    ("company_law_2023.txt", "company_law.jsonl", "公司法", "company_law", "第一章 总则", 266),
    ("labor_contract_law_2012.txt", "labor_contract_law.jsonl", "劳动合同法",
     "labor_contract_law", "第一章 总则", 98),
    # 下面 6 部不是为了凑「至少 2 部法律」，而是为了使切分后的 chunk 数真实落进
    # 2000–3000：法条平均仅 84 字，靠切窗口几乎不增块，只能增加真实条文数。
    # 全部选与合同/商事审查相关的法律，避免引入刑法这类无关领域噪声。
    ("insurance_law_2015.txt", "insurance_law.jsonl", "保险法", "insurance_law",
     "第一章 总则", 185),
    ("securities_law_2019.txt", "securities_law.jsonl", "证券法", "securities_law",
     "第一章 总则", 226),
    ("enterprise_bankruptcy_law.txt", "enterprise_bankruptcy_law.jsonl", "企业破产法",
     "enterprise_bankruptcy_law", "第一章 总则", 136),
    ("civil_procedure_law_2023.txt", "civil_procedure_law.jsonl", "民事诉讼法",
     "civil_procedure_law", "第一编 总则", 306),
    ("anti_unfair_competition_law_2019.txt", "anti_unfair_competition_law.jsonl",
     "反不正当竞争法", "anti_unfair_competition_law", "第一章 总则", 33),
    ("advertising_law_2021.txt", "advertising_law.jsonl", "广告法", "advertising_law",
     "第一章 总则", 74),
)


def emit(line: str) -> None:
    """脚本需要把统计贴到验收输出里；走 stdout 而不是 print，避免 ruff T20。"""
    sys.stdout.write(line + "\n")


def clean(text: str) -> str:
    """去掉 wikitext 标记但不动法条文字；保留款内标点与全角空格。"""

    def _link(match: re.Match[str]) -> str:
        parts = match.group(1).split("|")
        target = parts[-1]
        return target.split("/")[-1]

    out = _WIKI_LINK.sub(_link, text)
    out = out.replace("'''", "").replace("''", "")
    out = _TEMPLATE.sub("", out)
    out = _HTML_TAG.sub("", out)
    out = out.replace("\ufeff", "").replace("\u200b", "")
    return out.strip(" \t\u3000")


def parse_wikitext(
    raw: str, law: str, default_chapter: str
) -> tuple[list[dict[str, str]], list[str]]:
    """按「编/章标题 → 条首 → 款」三段式解析；款之间用 \n 保留，供 S4_paragraph 切分。"""
    entries: list[dict[str, str]] = []
    unmatched: list[str] = []
    headings: dict[int, str] = {}
    current: dict[str, str] | None = None
    parts: list[str] = []
    # 部分法律页（如企业破产法）没有 <onlyinclude> 包裹，整篇即正文；
    # 有标签的页则只取标签内，避免把导航模板/footer 当成法条。
    inside = "<onlyinclude>" not in raw

    def flush() -> None:
        if current is not None:
            body = "\n".join(part for part in parts if part)
            if body:
                entries.append({**current, "text": body})

    for line in raw.splitlines():
        if "</onlyinclude>" in line:
            line = line.split("</onlyinclude>", 1)[0]
            inside = False
        if "<onlyinclude>" in line:
            line = line.split("<onlyinclude>", 1)[1]
            inside = True
        # 维基文库部分款行带 BOM / 零宽空格，strip 去不掉；不清理会让这一款
        # 匹配不上 {{gap}} 而被当成模板行丢弃，正文内容就被静默截短。
        stripped = line.strip().lstrip("\ufeff\u200b").strip()
        if not inside or not stripped:
            continue

        heading = _HEADING.match(stripped)
        if heading is not None:
            level = len(heading.group(1))
            headings[level] = clean(heading.group(2)).replace("\u3000", " ")
            for deeper in [key for key in headings if key > level]:
                del headings[deeper]
            continue

        head = _ARTICLE_HEAD.match(stripped)
        if head is not None:
            flush()
            current = {
                "law": law,
                "article": head.group(1),
                "chapter": headings.get(2, default_chapter),
            }
            parts = [clean(head.group(2).lstrip("\u3000 "))]
            continue

        gap = _GAP_LINE.match(stripped)
        if gap is not None and current is not None:
            part = clean(gap.group(1))
            if part:
                parts.append(part)
            continue

        # Category 分类标签只在无 <onlyinclude> 的页面尾部出现，不是法条正文。
        if (current is not None and not stripped.startswith(("{{", "|", "[[Category:"))
                and not stripped.startswith("}}")):
            unmatched.append(stripped[:80])

    flush()
    return entries, unmatched


def build() -> int:
    """生成 jsonl 并做完整性校验；返回错误数，便于 CI 直接看退出码。"""
    grouped: dict[str, list[dict[str, str]]] = {}
    total_unmatched = 0

    for filename, outfile, law, _code, default_chapter, _expected in SOURCES:
        path = WIKITEXT_DIR / filename
        if not path.exists():
            emit(f"MISSING snapshot: {path}")
            return 1
        entries, unmatched = parse_wikitext(path.read_text(encoding="utf-8"), law, default_chapter)
        grouped.setdefault(outfile, []).extend(entries)
        total_unmatched += len(unmatched)
        emit(f"  {filename:38s} -> {len(entries):4d} 条  (未匹配行 {len(unmatched)})")
        for sample in unmatched[:3]:
            emit(f"      ! {sample}")

    errors = 0
    grand_total = 0
    for outfile, entries in grouped.items():
        seen: set[tuple[str, str]] = set()
        for entry in entries:
            key = (entry["law"], entry["article"])
            if key in seen:
                emit(f"DUPLICATE {key}")
                errors += 1
            seen.add(key)
            if not entry["text"]:
                emit(f"EMPTY TEXT {key}")
                errors += 1

        numbers = sorted(cn_number(e["article"][1:-1]) for e in entries)
        expected = next(exp for _f, o, _l, _c, _d, exp in SOURCES if o == outfile)
        missing = sorted(set(range(1, expected + 1)) - set(numbers))
        emit(
            f"  {outfile:28s} 条数={len(entries):4d}  期望={expected}  "
            f"缺号={len(missing)}  最大条号={numbers[-1] if numbers else 0}"
        )
        if missing:
            emit(f"      缺号前十: {missing[:10]}")
        if len(entries) != expected:
            emit(f"      ! 条数与期望不符（{len(entries)} != {expected}）")
            errors += 1

        path = CORPUS_DIR / outfile
        with path.open("w", encoding="utf-8", newline="\n") as handle:
            for entry in entries:
                handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        grand_total += len(entries)

    emit(f"TOTAL 条数 = {grand_total}；未匹配行合计 = {total_unmatched}；错误 = {errors}")
    if grand_total < 800:
        emit("FAIL: 语料条数 < 800")
        errors += 1
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(build())
