"""阶段 1：5 种切分策略单测。

核心红线是「raw[start:end] == text」：检索实验台后续要拿 chunk 当引用证据，
一旦切分器改写了原文，评测的命中率和引用溯源就全不可信，所以每条用例都回放一遍。
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from retrieval.chunker import (
    ALL_STRATEGIES,
    LAW_CODES,
    PARAGRAPH_MIN_CHARS,
    WINDOW_OVERLAP,
    WINDOW_SIZE,
    Strategy,
    assert_offsets,
    chunk_entry,
    source_text,
)

SHORT: dict[str, str] = {
    "law": "民法典",
    "article": "第五百八十五条",
    "chapter": "第三编 合同",
    "text": "当事人可以约定一方违约时应当根据违约情况向对方支付一定数额的违约金。",
}

_PARA_1 = (
    "当事人应当按照约定全面履行自己的义务，遵循诚信原则，"
    "根据合同的性质、目的和交易习惯履行通知、协助、保密等义务。"
) * 2
_PARA_2 = (
    "约定的违约金低于造成的损失的，人民法院或者仲裁机构可以根据当事人的请求予以增加；"
    "约定的违约金过分高于造成的损失的，可以适当减少。"
) * 2

MULTI_PARAGRAPH: dict[str, str] = {
    "law": "公司法",
    "article": "第二十三条",
    "chapter": "第二章 公司登记",
    "text": "\n".join(
        [
            _PARA_1,
            _PARA_2,
            "当事人就迟延履行约定违约金的，违约方支付违约金后，还应当履行债务。",
        ]
    ),
}

_BASE_SENTENCE = (
    "当事人应当按照约定全面履行自己的义务，遵循诚信原则，根据合同的性质、目的和交易习惯"
    "履行通知、协助、保密等义务，不得擅自变更或者解除合同。"
)


def long_text(length: int) -> str:
    """只用确定性重复文本构造超长条，避免测试依赖真实语料或随机数据。"""
    return (_BASE_SENTENCE * (length // len(_BASE_SENTENCE) + 1))[:length]


def assert_valid(entry: dict[str, str], strategy: Strategy) -> list:
    """通用不变量：边界合法、偏移可回放、chunk_id 规则、元数据不漂移。"""
    chunks = chunk_entry(entry, strategy)
    raw = source_text(entry, strategy)
    assert chunks, f"{strategy.value} 不应产出空结果"
    for idx, chunk in enumerate(chunks):
        assert 0 <= chunk.start < chunk.end <= len(raw)
        assert raw[chunk.start:chunk.end] == chunk.text
        assert chunk.chunk_id == (
            f"{LAW_CODES[entry['law']]}:{entry['article']}:{idx}:{strategy.value}"
        )
        assert chunk.strategy == strategy
        assert chunk.law == entry["law"]
        assert chunk.article == entry["article"]
        assert chunk.chapter == entry["chapter"]
    # 切片之间不能留空洞，否则原文有内容永远检索不到。
    assert chunks[0].start == 0
    assert chunks[-1].end == len(raw)
    assert all(next_.start <= prev.end for prev, next_ in zip(chunks, chunks[1:], strict=False))
    return chunks


# 5 种策略 × 2 条用例 = 10 条：短单款条 + 多款长条。
@pytest.mark.parametrize("strategy", ALL_STRATEGIES, ids=lambda s: s.value)
@pytest.mark.parametrize("entry", [SHORT, MULTI_PARAGRAPH], ids=["short", "multi"])
def test_strategy_offsets_and_ids(entry: dict[str, str], strategy: Strategy) -> None:
    assert_valid(entry, strategy)


def test_s1_whole_is_exactly_one_chunk() -> None:
    for entry in (SHORT, MULTI_PARAGRAPH):
        chunks = chunk_entry(entry, Strategy.S1_WHOLE)
        assert len(chunks) == 1
        assert chunks[0].text == entry["text"]
        assert chunks[0].start == 0 and chunks[0].end == len(entry["text"])


@pytest.mark.parametrize("strategy", [Strategy.S2_FIXED512, Strategy.S3_FIXED256])
def test_fixed_windows_size_overlap_and_coverage(strategy: Strategy) -> None:
    entry = {**SHORT, "text": long_text(3000)}
    chunks = assert_valid(entry, strategy)
    size = WINDOW_SIZE[strategy.value]
    overlap = WINDOW_OVERLAP[strategy.value]
    assert len(chunks) > 1
    assert all(len(chunk.text) <= size for chunk in chunks)
    assert all(
        following.start - previous.start == size - overlap
        for previous, following in zip(chunks, chunks[1:], strict=False)
    )
    # 相邻窗口的重叠区必须在两个窗口里逐字一致，否则说明 start 算错。
    raw = source_text(entry, strategy)
    for previous, following in zip(chunks, chunks[1:], strict=False):
        overlap_text = raw[following.start:previous.end]
        assert len(overlap_text) == overlap
        assert previous.text.endswith(overlap_text)
        assert following.text.startswith(overlap_text)


@pytest.mark.parametrize("strategy", [Strategy.S2_FIXED512, Strategy.S3_FIXED256])
def test_fixed_window_tail_is_not_duplicated(strategy: Strategy) -> None:
    size = WINDOW_SIZE[strategy.value]
    # 恰好一个窗口 + 1 字：尾巴必须只有 1 字，不能复制出第二个完整窗口。
    entry = {**SHORT, "text": long_text(size + 1)}
    chunks = chunk_entry(entry, strategy)
    assert len(chunks) == 2
    assert len(chunks[-1].text) == 1 + WINDOW_OVERLAP[strategy.value]


def test_s4_merges_paragraphs_shorter_than_threshold() -> None:
    parts = [f"第{i}款约定了双方的权利义务与履行方式。" for i in range(1, 5)]
    entry = {**SHORT, "text": "\n".join(parts)}
    chunks = assert_valid(entry, Strategy.S4_PARAGRAPH)
    assert len(chunks) == 1
    assert chunks[0].text == entry["text"]
    assert chunks[0].text.count("\n") == 3
    assert sum(len(part) for part in parts) + 3 < PARAGRAPH_MIN_CHARS * 2


def test_s4_splits_long_paragraphs() -> None:
    parts = [long_text(150) for _ in range(3)]
    entry = {**SHORT, "text": "\n".join(parts)}
    chunks = assert_valid(entry, Strategy.S4_PARAGRAPH)
    assert len(chunks) == 3
    # 前两块的末尾带着款间换行（归入前一块以保证无缝覆盖），最后一块正好是原文末尾。
    assert all(
        chunk.text.startswith(part)
        for chunk, part in zip(chunks, parts, strict=True)
    )
    assert chunks[0].text.endswith("\n") and chunks[1].text.endswith("\n")
    assert chunks[-1].text == parts[-1]


def test_s5_context_injects_law_chapter_and_article_number() -> None:
    chunks = assert_valid(SHORT, Strategy.S5_CONTEXT)
    assert len(chunks) == 1
    raw = source_text(SHORT, Strategy.S5_CONTEXT)
    assert raw.startswith("《民法典》第三编合同 第585条：")
    assert raw.endswith(SHORT["text"])
    assert chunks[0].start == 0 and chunks[0].end == len(raw)
    assert chunks[0].text == raw


@pytest.mark.parametrize("strategy", ALL_STRATEGIES, ids=lambda s: s.value)
def test_empty_text_produces_no_chunks(strategy: Strategy) -> None:
    # 空文本若产出「只含 S5 前缀」的 chunk，会变成召回收不到的噪声。
    assert chunk_entry({**SHORT, "text": ""}, strategy) == []
    assert chunk_entry({**SHORT, "text": "   "}, strategy) == []


@pytest.mark.parametrize("strategy", ALL_STRATEGIES, ids=lambda s: s.value)
def test_very_long_article_boundary(strategy: Strategy) -> None:
    entry = {**SHORT, "text": long_text(5000)}
    chunks = assert_valid(entry, strategy)
    if strategy in (Strategy.S1_WHOLE, Strategy.S5_CONTEXT, Strategy.S4_PARAGRAPH):
        assert len(chunks) == 1
    else:
        assert len(chunks) > 5


def test_unknown_law_fails_loudly() -> None:
    # 静默用中文法律名当 law_code 会污染下游索引键，必须显式失败。
    with pytest.raises(KeyError):
        chunk_entry({**SHORT, "law": "不存在的法律"}, Strategy.S1_WHOLE)


def test_assert_offsets_detects_tampered_chunk() -> None:
    chunk = chunk_entry(SHORT, Strategy.S1_WHOLE)[0]
    assert_offsets([SHORT], Strategy.S1_WHOLE, [chunk])
    tampered = replace(chunk, text=chunk.text + "（篡改）")
    with pytest.raises(AssertionError, match="offset mismatch"):
        assert_offsets([SHORT], Strategy.S1_WHOLE, [tampered])
