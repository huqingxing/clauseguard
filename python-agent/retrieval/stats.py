"""阶段 1 可执行验收：语料统计 + 5 策略 chunk 数 + 随机 20 条偏移回放。

固定随机种子而不是每次真随机：验收输出要能逐字复现，否则「抽 20 条都通过」
这种证据无法在两次运行之间对照。

用法：
    .venv/Scripts/python.exe -m retrieval.stats
退出码 0 = 全部通过；1 = 任一硬约束不满足。
"""

from __future__ import annotations

import random
import statistics
import sys
from collections import Counter

from retrieval.chunker import (
    ALL_STRATEGIES,
    Chunk,
    Strategy,
    assert_offsets,
    chunk_corpus,
    source_text,
)
from retrieval.corpus.loader import load_corpus

SEED = 20261106
SAMPLES_PER_STRATEGY = 4  # 5 策略 × 4 = 20 条，覆盖全部策略而非只抽到某一策略
CHUNK_MIN = 2000
CHUNK_MAX = 3000


def emit(line: str) -> None:
    sys.stdout.write(line + "\n")


def replay(entries: list[dict[str, str]], chunk: Chunk) -> bool:
    """从原始语料重建 raw，独立验证切片没有被切分器改写。"""
    raw = source_text(
        next(e for e in entries if e["law"] == chunk.law and e["article"] == chunk.article),
        chunk.strategy,
    )
    return raw[chunk.start:chunk.end] == chunk.text


def main() -> int:
    entries = load_corpus()
    errors = 0

    emit("=== 语料统计 ===")
    by_law = Counter(entry["law"] for entry in entries)
    for law, count in by_law.items():
        emit(f"  {law:<12s} {count:5d} 条")
    total_chars = sum(len(entry["text"]) for entry in entries)
    emit(f"  合计 {len(entries)} 条 / {len(by_law)} 部法律 / {total_chars} 字符")
    if len(entries) < 800:
        emit("FAIL: 语料条数 < 800")
        errors += 1

    emit("")
    emit("=== 5 种切分策略统计（口径：该策略切完整个语料库的 chunk 数）===")
    emit(f"  {'strategy':<16s} {'chunks':>7s} {'avg':>8s} {'min':>6s} {'max':>6s}  区间校验")
    all_chunks: dict[Strategy, list[Chunk]] = {}
    for strategy in ALL_STRATEGIES:
        chunks = chunk_corpus(entries, strategy)
        all_chunks[strategy] = chunks
        lens = [len(chunk.text) for chunk in chunks]
        in_range = CHUNK_MIN <= len(chunks) <= CHUNK_MAX
        if not in_range:
            errors += 1
        emit(
            f"  {strategy.value:<16s} {len(chunks):7d} {statistics.mean(lens):8.1f} "
            f"{min(lens):6d} {max(lens):6d}  {'PASS' if in_range else 'FAIL'}"
            f" ({CHUNK_MIN}-{CHUNK_MAX})"
        )
        assert_offsets(entries, strategy, chunks)  # 全量回放，失败直接抛 AssertionError
    emit(f"  5 策略合计 chunk = {sum(len(c) for c in all_chunks.values())}")

    emit("")
    emit(f"=== 随机抽 20 条断言 raw[start:end] == text（seed={SEED}，每策略 4 条）===")
    rng = random.Random(SEED)
    picked: list[tuple[Strategy, Chunk]] = []
    for strategy in ALL_STRATEGIES:
        pool = all_chunks[strategy]
        picked.extend((strategy, chunk) for chunk in rng.sample(pool, SAMPLES_PER_STRATEGY))
    for index, (_strategy, chunk) in enumerate(picked, start=1):
        ok = replay(entries, chunk)
        if not ok:
            errors += 1
        emit(
            f"  [{index:2d}] {chunk.chunk_id:<48s} [{chunk.start:4d}:{chunk.end:4d}] "
            f"{'PASS' if ok else 'FAIL'}"
        )
    emit(f"  抽样 {len(picked)} 条，失败 {sum(1 for _s, c in picked if not replay(entries, c))} 条")
    emit(f"  全量偏移回放：{sum(len(c) for c in all_chunks.values())} 条 chunk 全部通过")

    emit("")
    emit(f"=== 结论：{'PASS' if errors == 0 else f'FAIL ({errors} 项不达标)'} ===")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
