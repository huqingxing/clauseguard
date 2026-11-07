"""命中判定口径的单测：核心是「同 article 不同策略必须算命中」。"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from metrics import (  # noqa: E402
    Case,
    evaluate,
    gold_keys,
    is_hit,
    match_key,
    parse_chunk_id,
    recall_at_k,
    reciprocal_rank,
)

GOLD = ["civil_code:第五百八十五条:0:S1_whole"]


@dataclass(frozen=True)
class FakeHit:
    """模拟阶段 2 的 Hit（slots dataclass 没有 __dict__）。"""

    article: str
    law: str = "民法典"


def test_parse_chunk_id() -> None:
    ref = parse_chunk_id("civil_code:第五百八十五条:2:S3_fixed256")
    assert (ref.law_code, ref.article, ref.idx, ref.strategy) == (
        "civil_code",
        "第五百八十五条",
        "2",
        "S3_fixed256",
    )


def test_parse_chunk_id_rejects_malformed() -> None:
    with pytest.raises(ValueError, match="非法 chunk_id"):
        parse_chunk_id("civil_code:第五百八十五条")


@pytest.mark.parametrize(
    "item",
    [
        "civil_code:第五百八十五条:2:S3_fixed256",  # 字符串 chunk_id
        FakeHit("第五百八十五条"),  # dataclass
        {"article": "第五百八十五条", "law": "民法典"},  # 端点 JSON
        "第五百八十五条",  # 裸 article
    ],
)
def test_match_key_normalizes_all_result_shapes(item: object) -> None:
    assert match_key(item) == "第五百八十五条"


def test_same_article_different_strategy_is_hit() -> None:
    # 阶段 3 的核心约定：S3 的第 2 个切片与 gold 的 S1 整条是同一法条。
    assert is_hit(["civil_code:第五百八十五条:2:S3_fixed256"], GOLD, 5)


def test_different_article_is_miss() -> None:
    assert not is_hit(["civil_code:第五百七十七条:0:S1_whole"], GOLD, 5)


def test_hit_respects_k_cutoff() -> None:
    retrieved = ["civil_code:第五百七十七条:0:S1_whole"] * 5 + GOLD
    assert not is_hit(retrieved, GOLD, k=5)
    assert is_hit(retrieved, GOLD, k=6)


def test_non_positive_k_never_hits() -> None:
    assert not is_hit(GOLD, GOLD, k=0)


def test_gold_keys_deduplicates_equivalent_annotations() -> None:
    same_article = GOLD + ["civil_code:第五百八十五条:3:S4_paragraph"]
    assert gold_keys(same_article) == {"第五百八十五条"}
    assert recall_at_k(same_article, same_article, 5) == 1.0


def test_recall_at_k_partial() -> None:
    gold = ["civil_code:第五百八十五条:0:S1_whole", "civil_code:第五百八十四条:0:S1_whole"]
    assert recall_at_k(GOLD, gold, 5) == 0.5
    assert recall_at_k([], gold, 5) == 0.0
    assert recall_at_k(GOLD, [], 5) == 0.0


def test_reciprocal_rank() -> None:
    assert reciprocal_rank(GOLD, GOLD, 5) == 1.0
    assert reciprocal_rank(["civil_code:第五百七十七条:0:S1_whole", GOLD[0]], GOLD, 5) == 0.5
    assert reciprocal_rank(["civil_code:第五百七十七条:0:S1_whole"], GOLD, 5) == 0.0


def test_law_article_mode_disambiguates_shared_article_numbers() -> None:
    # 民法典与广告法都有「第四条」；只比 article 会误命中，带 law 的严格口径不会。
    gold = ["advertising_law:第四条:0:S1_whole"]
    wrong_law = ["civil_code:第四条:0:S1_whole"]
    assert is_hit(wrong_law, gold, 5) is True  # 默认口径的已知缺陷，单测固定下来
    assert is_hit(wrong_law, gold, 5, mode="law_article") is False


def test_evaluate_groups_and_macro_average() -> None:
    cases = [
        Case("q001", GOLD, GOLD, "liability", "concept"),
        Case("q002", ["civil_code:第五百七十七条:0:S1_whole"], GOLD, "liability", "scene"),
        Case("q003", GOLD, GOLD, "ip", "colloquial"),
    ]
    report = evaluate(cases, k=5)
    assert report["n"] == 3
    assert report["overall"]["hit_rate"] == pytest.approx(2 / 3)
    assert report["by_scene"]["liability"]["hit_rate"] == 0.5
    assert report["by_scene"]["ip"]["hit_rate"] == 1.0
    assert report["by_type"]["scene"]["mrr"] == 0.0


def test_evaluate_empty() -> None:
    assert evaluate([], 5) == {"k": 5, "mode": "article", "n": 0}


def test_unknown_mode_raises() -> None:
    with pytest.raises(ValueError, match="未知比对口径"):
        match_key(GOLD[0], "bogus")  # type: ignore[arg-type]
