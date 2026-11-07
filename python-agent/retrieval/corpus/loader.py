"""加载逐条 jsonl 语料；检索实验台的所有入口共用这一个读取点。

为什么集中读取：后续索引、评测、切分统计若各自 open 文件，很容易出现「评测读到的
条目顺序与建库时不同」这种难以察觉的偏差；单一入口保证顺序与内容完全一致。
"""

from __future__ import annotations

import json
from pathlib import Path

CORPUS_DIR = Path(__file__).resolve().parent
CORPUS_FILES: tuple[str, ...] = (
    "civil_code.jsonl",
    "company_law.jsonl",
    "labor_contract_law.jsonl",
    "insurance_law.jsonl",
    "securities_law.jsonl",
    "enterprise_bankruptcy_law.jsonl",
    "civil_procedure_law.jsonl",
    "anti_unfair_competition_law.jsonl",
    "advertising_law.jsonl",
)


def load_corpus(directory: Path = CORPUS_DIR) -> list[dict[str, str]]:
    """按 CORPUS_FILES 的固定顺序读入全部法条；缺文件直接失败，不静默返回空集。"""
    entries: list[dict[str, str]] = []
    for filename in CORPUS_FILES:
        path = directory / filename
        if not path.exists():
            raise FileNotFoundError(f"语料缺失，请先运行 build_corpus.py: {path}")
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line:
                    entries.append(json.loads(line))
    return entries
