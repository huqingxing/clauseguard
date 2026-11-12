"""端到端验收：默认配置下「上传 → 审查 → 下载批注稿」链路接通（修复方案 A）。

不注入 source_resolver / storage_client，完全走真实配置（CLAUSEGUARD_MINIO_LOCAL_DIR）。
模拟 Java 把源 docx 按约定键写入共享存储根，Python render 据此取源并回写带批注的 docx。

覆盖：
- 新约定键（contracts/{id}/v{n}/source.docx）落盘后，render 出带批注产物，无 source-unavailable；
- 产物 docx 可被 python-docx 打开，且 comments.xml 非空（≥1 条批注）；
- 历史遗留键（{id}-v{n}.docx）回退命中并打 WARN，不报错；
- 源确实缺失时降级 placeholder + WARN（不是 ERROR）。
"""

from __future__ import annotations

import asyncio

import pytest

from app.graph.state import Clause
from app.render.service import PLACEHOLDER_PREFIX, render_for_state
from tests.render.fixtures import build_sample, docx_parts


def _make_state(sample, contract_id: str, version: int) -> dict[str, object]:
    state = {
        "contract_id": contract_id,
        "version": version,
        "findings": sample.findings,
        "clauses": [
            Clause(clause_id=c.clause_id, index=pos, text=c.text)
            for pos, c in enumerate(sample.clauses)
        ],
        "approved": True,
    }
    return state


def _config() -> dict[str, object]:
    return {"configurable": {}}


def test_e2e_new_key_renders_annotated_docx(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """模拟 Java 按约定键写入 + Python 默认配置取源 → 出带批注产物。"""
    # 共享存储根：Java 默认上传根（./uploads）与 Python CLAUSEGUARD_MINIO_LOCAL_DIR 指向同一目录。
    monkeypatch.setenv("clauseguard_minio_local_dir", str(tmp_path))

    sample = build_sample(7, clause_count=6, picks=[(0, "high"), (2, "medium")])
    cid, version = "c-e2e", 1
    # 模拟 Java 上传：源 docx 落在约定键。
    src_dir = tmp_path / "contracts" / cid / f"v{version}"
    src_dir.mkdir(parents=True)
    (src_dir / "source.docx").write_bytes(sample.data)

    async def _run() -> dict[str, object]:
        outcome = await render_for_state(_make_state(sample, cid, version), _config())
        return {
            "status": outcome.status,
            "render_result": outcome.render_result,
            "comments": outcome.comments_count,
            "artifact": outcome.artifact,
        }

    res = asyncio.run(_run())
    assert res["status"] == "rendered", res["render_result"]
    assert "source-unavailable" not in res["render_result"]
    assert res["comments"] >= 1
    assert res["artifact"] is not None

    # 产物真实落盘（file:// 下载链路）且 comments.xml 非空。
    artifact_path = tmp_path / "artifacts" / cid / f"v{version}" / "annotated.docx"
    assert artifact_path.is_file(), "annotated.docx 未落盘"
    parts = docx_parts(artifact_path.read_bytes())
    assert "word/comments.xml" in parts
    assert b"<w:comment " in parts["word/comments.xml"], "comments.xml 应至少含 1 条批注"


def test_e2e_legacy_key_fallback_warns(tmp_path, monkeypatch: pytest.MonkeyPatch, caplog) -> None:
    """旧上传（扁平键 {id}-v{n}.docx）缺失新约定键时回退命中，打 WARN，仍出批注。"""
    monkeypatch.setenv("clauseguard_minio_local_dir", str(tmp_path))

    sample = build_sample(9, clause_count=6, picks=[(1, "high")])
    cid, version = "c-legacy", 1
    # 仅放置遗留格式源（模拟修复前 Java 落点）。
    (tmp_path / f"{cid}-v{version}.docx").write_bytes(sample.data)

    async def _run() -> dict[str, object]:
        outcome = await render_for_state(_make_state(sample, cid, version), _config())
        return {"status": outcome.status, "render_result": outcome.render_result,
                "comments": outcome.comments_count}

    res = asyncio.run(_run())
    assert res["status"] == "rendered", res["render_result"]
    assert res["comments"] >= 1
    assert any("历史遗留键" in r.message for r in caplog.records)


def test_e2e_missing_source_warns_not_errors(
    tmp_path, monkeypatch: pytest.MonkeyPatch, caplog
) -> None:
    """源确实缺失：降级 placeholder + WARN（不抛异常、不 ERROR 级）。"""
    monkeypatch.setenv("clauseguard_minio_local_dir", str(tmp_path))

    sample = build_sample(11, clause_count=6, picks=[(0, "high")])
    async def _run() -> dict[str, object]:
        outcome = await render_for_state(_make_state(sample, "c-missing", 1), _config())
        return {"status": outcome.status, "render_result": outcome.render_result}

    res = asyncio.run(_run())
    assert res["status"] == "placeholder"
    assert res["render_result"].startswith(PLACEHOLDER_PREFIX)
    assert "reason=source-unavailable" in res["render_result"]
    assert any("源合同缺失" in r.message for r in caplog.records)
    assert not any(r.levelno >= 40 for r in caplog.records)  # 无 ERROR 级日志
