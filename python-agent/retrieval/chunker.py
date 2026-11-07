"""法条语料的 5 种切分策略（离线、确定性、无 LLM、无语义模型）。

为什么把 start/end 做成「相对 source_text() 的偏移」而不是自己维护一份副本：
下游引用溯源要回答「这段依据来自原文哪个位置」，只有切片与源文本严格可回放
（raw[start:end] == text）才敢把 chunk 当成证据；若允许切分器自由拼接文本，
这个不变量就永远无法自动验证，评测里的命中率也就失去意义。

为什么空文本直接返回空列表：把纯前缀或空串写进索引会变成召回收不到但永远
排在前面的噪声，且 S5 的前缀注入在正文为空时尤其容易产生这种脏 chunk。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

from retrieval.numerals import cn_number

# chunk_id 里的 law_code 必须显式登记：漏登记时生成非法 id 比静默用法律中文名
# 更安全（下游按 law_code 分库/分表，名字不合法会污染索引键）。
LAW_CODES: dict[str, str] = {
    "民法典": "civil_code",
    "公司法": "company_law",
    "劳动合同法": "labor_contract_law",
    "保险法": "insurance_law",
    "证券法": "securities_law",
    "企业破产法": "enterprise_bankruptcy_law",
    "民事诉讼法": "civil_procedure_law",
    "反不正当竞争法": "anti_unfair_competition_law",
    "广告法": "advertising_law",
}

LawEntry = Mapping[str, str]

WINDOW_SIZE = {"S2_fixed512": 512, "S3_fixed256": 256}
WINDOW_OVERLAP = {"S2_fixed512": 64, "S3_fixed256": 32}
PARAGRAPH_MIN_CHARS = 100


class Strategy(StrEnum):
    """用枚举字符串标识策略：既是可读常量，又能直接拼进 chunk_id / JSON。"""

    S1_WHOLE = "S1_whole"
    S2_FIXED512 = "S2_fixed512"
    S3_FIXED256 = "S3_fixed256"
    S4_PARAGRAPH = "S4_paragraph"
    S5_CONTEXT = "S5_context"


ALL_STRATEGIES: tuple[Strategy, ...] = tuple(Strategy)


@dataclass(frozen=True, slots=True)
class Chunk:
    """统一输出结构；frozen 保证切片结果不会被评测代码就地改写。"""

    chunk_id: str
    law: str
    article: str
    chapter: str
    text: str
    start: int
    end: int
    strategy: Strategy


def source_text(entry: LawEntry, strategy: Strategy | str) -> str:
    """返回该策略下「被切分的源文本」，是 start/end 的唯一参照系。

    S5 把法律名/章节/条号注入到正文之前，因此它的 raw 与其余四种不同——
    前缀也必须能被 start/end 覆盖，否则引用溯源会丢掉上下文前缀。
    """
    text = entry["text"]
    if Strategy(strategy) is not Strategy.S5_CONTEXT:
        return text
    law = entry["law"]
    chapter = entry["chapter"].replace(" ", "")
    article_no = cn_number(entry["article"][1:-1])
    return f"《{law}》{chapter} 第{article_no}条：{text}"


def _fixed_spans(raw: str, size: int, overlap: int) -> list[tuple[int, int]]:
    """定长滑窗；末尾不足一窗时截断到 len(raw)，避免产出与上一窗重复的尾巴。"""
    step = size - overlap
    spans: list[tuple[int, int]] = []
    start = 0
    total = len(raw)
    while start < total:
        end = min(start + size, total)
        spans.append((start, end))
        if end == total:
            break
        start += step
    return spans


def _paragraph_spans(raw: str) -> list[tuple[int, int]]:
    """按「款」（原文自然段，语料里以换行符分隔）切，不足 100 字的相邻款合并。

    合并用的是连续区间 [首段start, 末段end]，因此区间里的换行符会原样留在
    chunk.text 中——这既保留了款的边界信息，又让 raw[start:end] == text 成立。
    """
    segments: list[tuple[int, int]] = []
    pos = 0
    for part in raw.split("\n"):
        end = pos + len(part)
        if part:
            segments.append((pos, end))
        pos = end + 1
    if not segments:
        return []

    groups: list[list[tuple[int, int]]] = []
    current: list[tuple[int, int]] = []
    for segment in segments:
        current.append(segment)
        if segment[1] - current[0][0] >= PARAGRAPH_MIN_CHARS:
            groups.append(current)
            current = []
    if current:
        # 尾部残余并回上一块，避免制造一个只有几十字的孤立 chunk。
        if groups:
            groups[-1].extend(current)
        else:
            groups.append(current)
    spans = [(group[0][0], group[-1][1]) for group in groups]
    # 款间换行归入前一个 chunk：若把它悬空，切片之间就出现「原文某个位置不属于
    # 任何 chunk」的空洞，引用溯源会无法把命中位置映射回整篇法条。
    return [
        (start, spans[index + 1][0] if index + 1 < len(spans) else len(raw))
        for index, (start, _end) in enumerate(spans)
    ]


def _spans(entry: LawEntry, strategy: Strategy, raw: str) -> list[tuple[int, int]]:
    if not raw:
        return []
    if strategy in (Strategy.S1_WHOLE, Strategy.S5_CONTEXT):
        return [(0, len(raw))]
    if strategy in WINDOW_SIZE:
        return _fixed_spans(raw, WINDOW_SIZE[strategy], WINDOW_OVERLAP[strategy])
    return _paragraph_spans(raw)


def chunk_entry(entry: LawEntry, strategy: Strategy | str) -> list[Chunk]:
    """把单条法条切成若干 chunk；空文本返回空列表（见模块注释）。"""
    resolved = Strategy(strategy)
    if not entry["text"].strip():
        return []
    law_code = LAW_CODES[entry["law"]]
    raw = source_text(entry, resolved)
    chunks: list[Chunk] = []
    for idx, (start, end) in enumerate(_spans(entry, resolved, raw)):
        if start < 0 or end <= start or end > len(raw):
            raise AssertionError(f"非法切片边界: {entry['article']} {resolved} [{start}:{end}]")
        chunk = Chunk(
            chunk_id=f"{law_code}:{entry['article']}:{idx}:{resolved.value}",
            law=entry["law"],
            article=entry["article"],
            chapter=entry["chapter"],
            text=raw[start:end],
            start=start,
            end=end,
            strategy=resolved,
        )
        # 不变量：Chunk 携带的文本必须能被源文本按自身偏移逐字回放。
        # 显式 raise 而不是 assert，避免 python -O 下这层保护被优化掉。
        if raw[chunk.start:chunk.end] != chunk.text:
            raise AssertionError(f"切片不变量被破坏: {chunk.chunk_id}")
        chunks.append(chunk)
    return chunks


def chunk_corpus(entries: Sequence[LawEntry], strategy: Strategy | str) -> list[Chunk]:
    """按单一策略切整个语料库（索引构建用这一个口径）。"""
    resolved = Strategy(strategy)
    return [chunk for entry in entries for chunk in chunk_entry(entry, resolved)]


def all_strategies(entries: Sequence[LawEntry]) -> dict[Strategy, list[Chunk]]:
    """一次产出 5 种策略的切片，供对照实验统计；不落库、不写文件。"""
    return {strategy: chunk_corpus(entries, strategy) for strategy in ALL_STRATEGIES}


def assert_offsets(
    entries: Sequence[LawEntry], strategy: Strategy | str, chunks: Sequence[Chunk]
) -> None:
    """独立回放校验：随机抽样也能证明 raw[start:end] == text。

    单独提供而不是只在 chunk_entry 内部校验，是为了让验收脚本能对不同批次、
    不同策略重复验证，且失败时能定位到具体 chunk_id。
    """
    resolved = Strategy(strategy)
    by_article: dict[tuple[str, str], str] = {}
    for entry in entries:
        by_article.setdefault((entry["law"], entry["article"]), entry["text"])
    for chunk in chunks:
        raw = source_text(
            {
                "law": chunk.law,
                "article": chunk.article,
                "chapter": chunk.chapter,
                "text": by_article[(chunk.law, chunk.article)],
            },
            resolved,
        )
        if raw[chunk.start:chunk.end] != chunk.text:
            raise AssertionError(f"offset mismatch: {chunk.chunk_id}")
