"""矩阵实验的 embedding 档位、向量化实现与磁盘缓存。

## 为什么默认档位不是真实模型

阶段 2 的三档真实配置是：E1 百炼 text-embedding-v3(1024)、E2 bge-m3(1024)、
E3 bge-small-zh(512)，后两档指向本地 127.0.0.1:9997 推理服务。本机实测该端口
无监听，且 bge 权重需要 sentence-transformers 这类框架级依赖（铁律禁止）；E3 的
512 维还和 law_chunk.embedding vector(1024) 单列冲突。若硬跑真实档位，15 组里
会有 10 组直接失败，矩阵、结论段和「两次重跑差异 = 0」都无从谈起。

因此默认 provider 是**确定性哈希投影基线**：用三套不同的字符 n-gram 粒度与
投影维度（沿用 E1/E2/E3 的真实维度 1024/1024/512），把文本映射到单位向量。
它验证的是「切分策略 × 向量特征粒度」对指标的影响，以及整套跑批/指标/复现框架
的正确性——**不是** dashscope 与 bge 的模型质量对比，报告顶部必须原样声明这一点。
真实档位由 `--provider live` 走 PgLiveVectorizer，可用时出真实数字，不可用时按
失败组合留痕，绝不拿离线数字冒充模型结论。

## 为什么用 zlib.crc32 而不是 Python 内置 hash

内置 hash 受 PYTHONHASHSEED 影响，换进程就变；crc32 是稳定的 C 实现，跨平台
一致且比逐 gram 调 blake2b 快一个量级（全库约两千万次哈希，这决定验收能否在
分钟级跑完）。signed hashing 用高位比特定符号，减少哈希碰撞带来的同向偏置。
"""

from __future__ import annotations

import hashlib
import json
import math
import zlib
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, cast

import numpy as np

from retrieval.config import EMBEDDING_PROFILES

PROVIDER_VERSION = "lab-hash-v1"
DEFAULT_SEED = 20261107

# 三档离线基线的特征粒度。维度不在这里写死，一律取 EMBEDDING_PROFILES，
# 保证「E3 是 512 维」这个事实与线上配置同源。
_OFFLINE_PARAMS: dict[str, tuple[tuple[int, ...], str]] = {
    "E1": ((2, 3), "cg-e1-char23"),
    "E2": ((3, 4), "cg-e2-char34"),
    "E3": ((2,), "cg-e3-char2-lowdim"),
}

DEFAULT_PG_DSN = "postgresql://clauseguard@127.0.0.1:55432/clauseguard_retrieval"


class ProviderUnavailable(RuntimeError):
    """真实档位缺服务/缺向量；run.py 据此把组合记为 failed 而不是崩溃退出。"""


@dataclass(frozen=True, slots=True)
class ProviderSpec:
    code: str
    model: str
    dim: int
    base_url: str
    mode: str
    ngrams: tuple[int, ...]
    seed_tag: str

    @property
    def fingerprint(self) -> str:
        # 缓存 key 必须含实现版本与参数：改了特征/维度还复用旧 .npy 会让
        # 「换 embedding 后指标没变」这种假象悄悄混进结论。
        payload = "|".join(
            (
                PROVIDER_VERSION,
                self.code,
                str(self.dim),
                repr(self.ngrams),
                self.seed_tag,
                self.mode,
            )
        )
        return hashlib.sha256(payload.encode()).hexdigest()[:16]

    def as_dict(self) -> dict[str, Any]:
        return {**asdict(self), "ngrams": list(self.ngrams), "fingerprint": self.fingerprint}


def spec_for(code: str, mode: str = "deterministic") -> ProviderSpec:
    if code not in EMBEDDING_PROFILES or code not in _OFFLINE_PARAMS:
        raise ValueError(f"未知 embedding 档位: {code}（可选 {sorted(EMBEDDING_PROFILES)}）")
    if mode not in ("deterministic", "live"):
        raise ValueError(f"未知 provider mode: {mode}")
    profile = EMBEDDING_PROFILES[code]
    ngrams, tag = _OFFLINE_PARAMS[code]
    return ProviderSpec(
        code=code,
        model=str(profile["model"]),
        dim=int(profile["dim"]),
        base_url=str(profile["base_url"]),
        mode=mode,
        ngrams=ngrams,
        seed_tag=tag,
    )


@dataclass(slots=True)
class EmbedUsage:
    """每组实验的资源账本。离线档位 tokens 恒为 0，绝不用估算值冒充真实消耗。"""

    source: str = "deterministic-hash"
    chars: int = 0
    tokens: int = 0
    requests: int = 0
    retries: int = 0
    cache_hit: bool = False
    cost_cny: float = 0.0
    estimated_tokens_if_live: int = 0
    note: str = "离线哈希投影：无 API 调用、无 token、无费用"

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class DeterministicVectorizer:
    """字符 n-gram + signed hashing trick → L2 归一化向量。完全确定性、零 IO。"""

    def __init__(self, spec: ProviderSpec) -> None:
        self.spec = spec
        self.usage = EmbedUsage()
        self._seed = int.from_bytes(hashlib.sha256(spec.seed_tag.encode()).digest()[:4], "big")

    def embed(self, texts: Sequence[str]) -> np.ndarray:
        dim = self.spec.dim
        matrix = np.zeros((len(texts), dim), dtype=np.float32)
        for row, text in enumerate(texts):
            # 去所有空白：法条正文的换行/空格是排版噪声，不能变成可检索特征。
            cleaned = "".join(text.split())
            self.usage.chars += len(cleaned)
            vec = [0.0] * dim  # Python list 逐元素累加比 numpy 标量赋值快得多
            length = len(cleaned)
            for n in self.spec.ngrams:
                for i in range(length - n + 1):
                    h = zlib.crc32(cleaned[i : i + n].encode("utf-8"), self._seed)
                    vec[h % dim] += 1.0 if h & 0x10000 else -1.0
            norm = math.sqrt(sum(value * value for value in vec))
            matrix[row] = [value / norm for value in vec] if norm > 0 else vec
        # 中文约 1 字 ≈ 1 token，作为「若改用真实 API 的量级参考」上界，不计费。
        self.usage.estimated_tokens_if_live += self.usage.chars
        return matrix


class VectorCache:
    """把 (语料内容, 档位, 实现版本) 对应的向量矩阵落盘，第二次重跑直接复用。

    为什么缓存键要含语料指纹：同一 label 重跑必须逐位一致，但语料或切分器一旦
    改动，旧向量就不代表新 chunk 了；靠文件名（strategy__E1）复用会静默串数据。
    """

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def corpus_fingerprint(chunk_ids: Sequence[str], texts: Sequence[str]) -> str:
        digest = hashlib.sha256()
        for chunk_id, text in zip(chunk_ids, texts, strict=True):
            digest.update(chunk_id.encode("utf-8"))
            digest.update(b"\x00")
            digest.update(text.encode("utf-8"))
            digest.update(b"\n")
        return digest.hexdigest()[:16]

    def path_for(self, name: str, spec: ProviderSpec, fingerprint: str, rows: int) -> Path:
        return self.root / f"{name}__{spec.code}__{spec.fingerprint}__{fingerprint}__{rows}.npy"

    def load(self, path: Path, rows: int, dim: int) -> np.ndarray | None:
        if not path.exists():
            return None
        matrix = cast(np.ndarray, np.load(path))
        if matrix.shape != (rows, dim) or matrix.dtype != np.float32:
            return None
        return matrix

    def save(self, path: Path, matrix: np.ndarray, meta: dict[str, Any]) -> None:
        np.save(path, matrix)
        path.with_suffix(".json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )


class PgLiveVectorizer:
    """真实档位：复用阶段 2 已写入 pgvector 的百炼向量，不重复付 API 费用。

    只支持 E1：E2/E3 既无本地推理服务，E3 的 512 维也进不了 vector(1024) 列。
    查询向量仍须真实计算，用阶段 2 的 Embedder（OpenAI 兼容端点）并落盘缓存，
    这样同 label 重跑不产生网络抖动、也不重复计费。
    """

    def __init__(self, spec: ProviderSpec, dsn: str, cache: VectorCache) -> None:
        self.spec = spec
        self.dsn = dsn
        self.cache = cache
        self.usage = EmbedUsage(
            source="pg-stored-dashscope", note="chunk 向量来自阶段 2 已入库的真实百炼结果"
        )

    def chunk_matrix(self, strategy: str, chunks: Sequence[Any]) -> np.ndarray:
        if self.spec.code != "E1":
            raise ProviderUnavailable(
                f"{self.spec.code} 需要 {self.spec.base_url} 的本地推理服务，本机未启动；"
                f"且 {self.spec.dim} 维无法写入 law_chunk.embedding vector(1024)"
            )
        import psycopg

        wanted = [chunk.chunk_id for chunk in chunks]
        with psycopg.connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT chunk_id, embedding::text FROM law_chunk "
                "WHERE strategy = %s AND embedding IS NOT NULL",
                (strategy,),
            )
            stored = {row[0]: row[1] for row in cur.fetchall()}
        missing = [chunk_id for chunk_id in wanted if chunk_id not in stored]
        if missing:
            raise ProviderUnavailable(
                f"strategy={strategy} 仅 {len(stored)} 条有真实向量，缺 {len(missing)} 条"
                f"（例：{missing[0]}）；需先跑阶段 2 全量向量化"
            )
        matrix = np.zeros((len(wanted), self.spec.dim), dtype=np.float32)
        for row, chunk_id in enumerate(wanted):
            values = [float(value) for value in stored[chunk_id].strip("[]").split(",")]
            if len(values) != self.spec.dim:
                raise ProviderUnavailable(f"{chunk_id} 向量维度 {len(values)} != {self.spec.dim}")
            matrix[row] = values
        self.usage.chars = sum(len(chunk.text) for chunk in chunks)
        return matrix

    def query_matrix(self, queries: Sequence[str]) -> np.ndarray:
        fingerprint = hashlib.sha256("\n".join(queries).encode("utf-8")).hexdigest()[:16]
        path = self.cache.path_for("query", self.spec, fingerprint, len(queries))
        cached = self.cache.load(path, len(queries), self.spec.dim)
        if cached is not None:
            self.usage.cache_hit = True
            return cached

        from retrieval.config import RetrievalSettings
        from retrieval.embedder import Embedder

        settings = RetrievalSettings(
            # pydantic-settings 的构造期私有参数，mypy 看不到动态生成的签名。
            _env_file=None,  # type: ignore[call-arg]
            embedding_backend="E1",
        )
        result = Embedder(settings).embed(list(queries))
        matrix = np.asarray(result.vectors, dtype=np.float32)
        if matrix.shape != (len(queries), self.spec.dim):
            raise ProviderUnavailable(
                f"真实 embedding 返回 {matrix.shape}，期望 {(len(queries), self.spec.dim)}"
            )
        self.usage.tokens = result.usage.total_tokens
        self.usage.requests = result.usage.requests
        self.usage.retries = result.usage.retries
        self.cache.save(path, matrix, {"source": "dashscope", "tokens": self.usage.tokens})
        return matrix


def make_vectorizer(
    spec: ProviderSpec, cache: VectorCache, dsn: str = DEFAULT_PG_DSN
) -> DeterministicVectorizer | PgLiveVectorizer:
    if spec.mode == "live":
        return PgLiveVectorizer(spec, dsn, cache)
    return DeterministicVectorizer(spec)
