"""BM25 稀疏召回：jieba 分词 + 停用词/单字过滤 + rank_bm25 内存索引。

为什么索引用内存而不是写回 PG：评测要对同一批 chunk 反复跑不同权重/融合策略，
内存索引构建一次即可复用；且 BM25 的词频统计依赖全库文档频率，增量维护容易
和库内状态不一致，重启时全量重算反而更可预测。
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import jieba
from rank_bm25 import BM25Okapi

from retrieval.store import ChunkRow

DEFAULT_STOPWORDS = Path(__file__).resolve().parent / "stopwords.txt"
_TOKEN = re.compile(r"[\u4e00-\u9fffA-Za-z][\u4e00-\u9fffA-Za-z0-9]*")
# 纯数字对法条检索几乎只有噪声（条号已单独存列），但「第一百八十六条」这类
# 中文数字由 jieba 切分后仍会保留语义，故只过滤阿拉伯数字串。
_DIGITS = re.compile(r"^\d+$")


def load_stopwords(path: Path = DEFAULT_STOPWORDS) -> frozenset[str]:
    if not path.exists():
        raise FileNotFoundError(f"停用词表缺失: {path}")
    return frozenset(
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    )


def tokenize(text: str, stopwords: frozenset[str] | None = None) -> list[str]:
    """过滤停用词、单字、纯数字与标点；单字会显著放大中文 BM25 的偶然命中。"""
    stops = stopwords if stopwords is not None else load_stopwords()
    tokens: list[str] = []
    for raw in jieba.lcut(text):
        token = raw.strip().lower()
        if len(token) < 2 or token in stops or _DIGITS.fullmatch(token):
            continue
        if not _TOKEN.fullmatch(token):
            continue
        tokens.append(token)
    return tokens


@dataclass(slots=True)
class SparseHit:
    chunk_id: str
    sparse_score: float


class BM25Index:
    """rows 与 tokenized 下标严格对齐；search 返回按分数降序的候选。"""

    def __init__(self, rows: Sequence[ChunkRow], stopwords_file: str = "") -> None:
        stops = load_stopwords(Path(stopwords_file)) if stopwords_file else load_stopwords()
        self.rows = list(rows)
        self.stopwords = stops
        self.corpus = [tokenize(row.text, stops) for row in self.rows]
        # rank_bm25 在空语料上构造可用，但全空文档会让 get_scores 恒为 0；
        # 保持单一实现，由调用方在空库时显式跳过稀疏召回。
        self._model = BM25Okapi(self.corpus) if self.corpus else None
        self._idf_cache: dict[str, float] = {}

    def __len__(self) -> int:
        return len(self.rows)

    def search(self, query: str, k: int) -> list[SparseHit]:
        if self._model is None or k <= 0:
            return []
        scores = self._model.get_scores(tokenize(query, self.stopwords))
        ranked = sorted(range(len(scores)), key=lambda index: -scores[index])[:k]
        # 分数为 0 的项不是「命中」而是「没匹配到任何词」，把它们放进融合会
        # 稀释 rank 信号，因此只保留正分项。
        return [
            SparseHit(chunk_id=self.rows[index].chunk_id, sparse_score=float(scores[index]))
            for index in ranked
            if scores[index] > 0
        ]
