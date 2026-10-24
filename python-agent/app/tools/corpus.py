"""检索 mock 的公共候选集结构与确定性打分。

为什么固定候选集而不是随机/外部数据：Stage 6 验收要求可复现——同一 query 必须得到同一批
候选（含 id/article/text/score），引用白名单校验与召回指标才有意义。
"""

from __future__ import annotations

import json
from typing import Any

# 候选条目字段：id/article/title/text/keywords（keywords 仅用于 mock 打分，不返回给模型）。
CorpusEntry = dict[str, Any]


def search_corpus(corpus: list[CorpusEntry], query: str, top_k: int = 3) -> list[dict[str, Any]]:
    """按关键词命中数打分取 top_k；无命中时退化为语料前 top_k 条（保证白名单非空）。

    无命中也返回候选是有意的：检索不到支撑时，模型的引用会因白名单为空而被丢弃，
    该 finding 自动降级 unverified——这正是硬约束想要的行为，不应在工具层掩盖。
    """
    scored: list[tuple[int, CorpusEntry]] = []
    for entry in corpus:
        hits = sum(1 for keyword in entry["keywords"] if keyword in query)
        scored.append((hits, entry))
    hit_only = [(hits, entry) for hits, entry in scored if hits > 0]
    pool = hit_only if hit_only else [(0, entry) for entry in corpus[:top_k]]
    pool.sort(key=lambda item: (-item[0], item[1]["id"]))
    return [
        {
            "id": entry["id"],
            "article": entry["article"],
            "title": entry["title"],
            "text": entry["text"],
            "score": round(0.5 + 0.12 * hits, 2),
        }
        for hits, entry in pool[:top_k]
    ]


def candidates_json(corpus: list[CorpusEntry], query: str) -> str:
    """工具返回值统一序列化为 JSON 字符串（ToolMessage content 需要可序列化载体）。"""
    return json.dumps({"candidates": search_corpus(corpus, query)}, ensure_ascii=False)
