"""标注集结构的快速单测；语料级对账放在 validate.py（要加载 2584 条，不适合单测）。"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

DATA_FILE = Path(__file__).resolve().parents[1] / "dataset" / "queries.jsonl"
SCENES = {"subject", "commercial", "liability", "ip", "data", "compliance"}
TYPES = {"concept", "scene", "colloquial"}


def _rows() -> list[dict[str, object]]:
    return [
        json.loads(line)
        for line in DATA_FILE.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_total_and_qids() -> None:
    rows = _rows()
    assert len(rows) == 80
    assert [row["qid"] for row in rows] == [f"q{i:03d}" for i in range(1, 81)]


def test_schema() -> None:
    for row in _rows():
        assert set(row) == {"qid", "query", "gold", "scene", "type"}
        assert re.fullmatch(r"q\d{3}", str(row["qid"]))
        assert str(row["query"]).strip()
        assert row["scene"] in SCENES and row["type"] in TYPES
        gold = row["gold"]
        assert isinstance(gold, list) and 1 <= len(gold) <= 3
        assert len(set(gold)) == len(gold)
        for chunk_id in gold:
            parts = str(chunk_id).rsplit(":", 3)
            assert len(parts) == 4 and parts[2] == "0" and parts[3] == "S1_whole"


def test_distribution() -> None:
    rows = _rows()
    assert Counter(row["type"] for row in rows) == {
        "concept": 32,
        "scene": 24,
        "colloquial": 24,
    }
    by_scene = Counter(row["scene"] for row in rows)
    assert set(by_scene) == SCENES
    assert all(count >= 10 for count in by_scene.values())


def test_queries_are_unique() -> None:
    queries = [row["query"] for row in _rows()]
    assert len(set(queries)) == len(queries)
