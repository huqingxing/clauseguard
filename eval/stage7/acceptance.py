"""阶段 7 可执行验收：数字在、明细在、文档能被外行读懂。

跑法（仓库根）：
  python-agent/.venv/Scripts/python.exe -m eval.stage7.acceptance
退出码 0 才算通过；任何一条不满足直接 AssertionError 并打印原因。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = REPO_ROOT / "eval" / "out" / "stage7"
RAG_LAB = REPO_ROOT / "docs" / "rag-lab.md"
DOCS_README = REPO_ROOT / "docs" / "README.md"

REQUIRED_SECTIONS = (
    "## 0. 先说人话",
    "术语速查",
    "## 1. 问题定义",
    "## 2. 实验设计",
    "## 3. 结果矩阵",
    "## 4. rerank 取舍",
    "## 5. 对业务指标的影响",
    "## 6. 引用准确率",
    "## 7. 结论",
    "## 8. 局限",
    "## 9. 复现命令",
)
RESUME_PREFIX = "> **简历素材"


def _json(path: Path) -> dict[str, Any]:
    assert path.exists(), f"缺文件: {path}"
    return json.loads(path.read_text(encoding="utf-8"))


def _check_groups() -> tuple[dict[str, Any], dict[str, Any]]:
    mock = _json(OUT_DIR / "mock" / "metrics.json")
    live = _json(OUT_DIR / "live" / "metrics.json")
    for label, metrics in (("mock", mock), ("live", live)):
        assert metrics["meta"]["contracts"] == 30, f"{label} 不是 30 份"
        assert metrics["meta"]["model_backend"] == "live", f"{label} 用的不是真模型"
        assert metrics["recall"]["recall"] is not None, f"{label} 缺召回率"
        assert metrics["recall"]["gold"] > 0, f"{label} 金标为空"
        assert metrics["recall"]["contracts"] == 30
    assert mock["mode"] == "mock" and live["mode"] == "live", "两组变量串了"
    assert mock["meta"]["model"] == live["meta"]["model"], "两组模型不一致，实验无效"
    return mock, live


def _check_audit() -> dict[str, Any]:
    summary = _json(OUT_DIR / "citation_audit.json")
    assert summary["sample"] == 30, "抽检不是 30 条"
    assert sum(summary["counts"].values()) == 30, "判定数不足 30"
    assert summary["annotators"] == 1, "标注者数量须如实记录"
    report = (OUT_DIR / "citation_audit.md").read_text(encoding="utf-8")
    for seq in range(1, 31):
        assert re.search(rf"^\| {seq} \|", report, re.MULTILINE), f"明细表缺第 {seq} 行"
    annotations = [
        json.loads(line)
        for line in (Path(__file__).with_name("annotations.jsonl"))
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    assert len(annotations) == 30
    assert all(str(a.get("reason") or "").strip() for a in annotations), "有判定缺理由"
    assert all(
        a["verdict"] in ("support", "partial", "not_support") for a in annotations
    )
    return summary


def _check_docs(
    mock: dict[str, Any], live: dict[str, Any], summary: dict[str, Any]
) -> None:
    assert RAG_LAB.exists(), "缺 docs/rag-lab.md"
    text = RAG_LAB.read_text(encoding="utf-8")
    for section in REQUIRED_SECTIONS:
        assert section in text, f"rag-lab.md 缺章节/要素: {section}"
    for label, metrics in (("改造前", mock), ("改造后", live)):
        value = metrics["recall"]["recall"]
        assert f"{value:.4f}" in text or str(value) in text, (
            f"rag-lab.md 没写{label}召回率 {value}"
        )
    assert f"{summary['strict_accuracy']:.4f}" in text or (
        f"{summary['strict_accuracy'] * 100:.1f}" in text
    ), "rag-lab.md 没写引用准确率"
    assert "标注者" in text and "样本" in text and "语料" in text, "局限章节不完整"

    readme = DOCS_README.read_text(encoding="utf-8")
    line = next((ln for ln in readme.splitlines() if ln.startswith(RESUME_PREFIX)), "")
    assert line, "docs/README.md 顶部缺简历素材"
    body = line.split("**：", 1)[-1].strip()
    # 中文按字计：去掉空白与 Markdown 强调符号后长度即简历字数。
    plain = re.sub(r"[\s*`]", "", body)
    assert len(plain) <= 120, f"简历素材 {len(plain)} 字，超过 120 字"
    assert len(plain) >= 40, "简历素材过短，不像一段完整素材"
    print(f"[accept] 简历素材 {len(plain)} 字（≤120）")


def main() -> None:
    mock, live = _check_groups()
    summary = _check_audit()
    _check_docs(mock, live, summary)
    mock_r = mock["recall"]["recall"]
    live_r = live["recall"]["recall"]
    print(
        f"[accept] 改造前 mock 检索 recall={mock_r} "
        f"({mock['recall']['tp']}/{mock['recall']['gold']})"
    )
    print(
        f"[accept] 改造后 live 检索 recall={live_r} "
        f"({live['recall']['tp']}/{live['recall']['gold']})"
    )
    print(f"[accept] Δrecall={round(live_r - mock_r, 4)}")
    print(
        f"[accept] 引用抽检 strict={summary['strict_accuracy']} "
        f"counts={summary['counts']}"
    )
    print(
        "[accept] 真模型、30 份、两组数字、30 条明细、文档章节、简历字数 —— 全部 PASS"
    )


if __name__ == "__main__":
    main()
