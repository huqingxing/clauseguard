"""render 服务层 + 节点测试：占位路径、注入优先级、join 口径、事件与异常传播。

覆盖 Stage 10 的接线质量（红线：不做「看起来能跑」的假实现）：
- 源不可用 / 非 docx / 存储未配置 → 显式 PLACEHOLDER_RENDER 占位并说清 reason；
- config 注入的 source_resolver/storage_client 优先于环境配置（铁律 4）；
- finding -> 条款 span 的 join：未匹配计数、同文取首处、确定性；
- render 节点：事件成对发射、RenderError 不吞（挂错批注比不挂更糟）。
"""

from __future__ import annotations

import asyncio
import json

import pytest

from app.clients.minio_client import LocalStorageClient, ResolvedSource
from app.events import EventEmitter
from app.graph.nodes.render import NODE_NAME, render
from app.graph.state import Clause, Finding
from app.render.docx_writer import RenderError
from app.render.service import (
    PLACEHOLDER_PREFIX,
    build_comment_specs,
    render_for_state,
    resolve_source,
    resolve_storage,
)
from tests.render.fixtures import build_sample, derive_clauses

_EMPTY_ENV = (
    "clauseguard_minio_endpoint",
    "clauseguard_minio_access_key",
    "clauseguard_minio_secret_key",
    "clauseguard_minio_local_dir",
)


@pytest.fixture(autouse=True)
def _no_storage_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """占位路径测试要求环境无 MinIO 配置：显式清空，避免开发机 .env 泄漏进用例。"""
    for name in _EMPTY_ENV:
        monkeypatch.setenv(name, "")


def _docx_resolver(data: bytes, doc_type: str = "docx"):
    """config 注入的源解析器（测试覆盖点，非 mock_：返回真实样本字节）。"""

    async def _resolve(contract_id: str, version: int) -> ResolvedSource:
        return ResolvedSource(
            data=data, key=f"contracts/{contract_id}/v{version}/source.docx", doc_type=doc_type
        )

    return _resolve


def _state(sample) -> dict[str, object]:
    return {
        "contract_id": "c-stage10",
        "version": 1,
        "findings": sample.findings,
        "clauses": [
            Clause(clause_id=clause.clause_id, index=position, text=clause.text)
            for position, clause in enumerate(sample.clauses)
        ],
        "approved": True,
    }


def _config(**extra: object) -> dict[str, object]:
    return {"configurable": dict(extra)}


# ---- 全链路：渲染 + 上传 ----


def test_render_for_state_full_pipeline(tmp_path) -> None:
    """源解析 -> 挂批注 -> 本地存储上传：产物落盘、URL 可下载、统计自洽。"""

    async def _run() -> None:
        sample = build_sample(7, clause_count=6, picks=[(0, "high"), (2, "medium")])
        storage = LocalStorageClient(tmp_path)
        config = _config(
            source_resolver=_docx_resolver(sample.data), storage_client=storage
        )

        outcome = await render_for_state(_state(sample), config)

        assert outcome.status == "rendered"
        assert outcome.comments_count == 2
        assert outcome.unmatched == 0
        assert outcome.artifact is not None
        assert outcome.artifact.object_key == "artifacts/c-stage10/v1/annotated.docx"
        assert outcome.artifact.size_bytes > 0
        assert outcome.artifact.download_url.startswith("file://")

        payload = json.loads(outcome.render_result)
        assert payload["status"] == "rendered"
        assert payload["object_key"] == outcome.artifact.object_key
        assert payload["comments_count"] == 2
        assert payload["size_bytes"] == outcome.artifact.size_bytes
        assert payload["unmatched_clauses"] == 0

        # 产物真实落盘且可经存储端口读回（file:// 下载链路）。
        stored = await storage.download(outcome.artifact.object_key)
        assert stored[:4] == b"PK\x03\x04"

    asyncio.run(_run())


# ---- 占位路径（显式，不假装成功） ----


def test_placeholder_when_source_unavailable() -> None:
    """无注入、无环境配置：source-unavailable 占位，不产 artifact。"""

    async def _run() -> None:
        outcome = await render_for_state(_state(build_sample(8, picks=[])), _config())
        assert outcome.status == "placeholder"
        assert outcome.artifact is None
        assert outcome.render_result.startswith(PLACEHOLDER_PREFIX)
        assert "reason=source-unavailable" in outcome.render_result
        assert "findings=0" in outcome.render_result

    asyncio.run(_run())


def test_placeholder_for_non_docx_source() -> None:
    """PDF 源：non-docx 占位（本阶段只回写 docx；PDF 属 Stage 11+）。"""

    async def _run() -> None:
        sample = build_sample(9, picks=[])
        config = _config(
            source_resolver=_docx_resolver(b"%PDF-1.4 fake", doc_type="pdf"),
            storage_client=LocalStorageClient("/tmp/unused-stage10"),
        )
        outcome = await render_for_state(_state(sample), config)
        assert outcome.status == "placeholder"
        assert "reason=non-docx-source:pdf" in outcome.render_result

    asyncio.run(_run())


def test_placeholder_when_storage_unconfigured() -> None:
    """有 docx 源但无存储后端：storage-unconfigured 占位（不假装修写成功）。"""

    async def _run() -> None:
        sample = build_sample(10, picks=[])
        config = _config(source_resolver=_docx_resolver(sample.data))
        outcome = await render_for_state(_state(sample), config)
        assert outcome.status == "placeholder"
        assert "reason=storage-unconfigured" in outcome.render_result

    asyncio.run(_run())


# ---- 注入优先级（铁律 4：运行态依赖走 config） ----


def test_resolve_source_prefers_injected_resolver() -> None:
    """注入的 resolver 必须压过环境配置（测试/线上切换源只改 config）。"""

    async def _run() -> None:
        sentinel = ResolvedSource(data=b"PK\x03\x04x", key="injected", doc_type="docx")
        config = _config(
            source_resolver=lambda cid, ver: asyncio.sleep(0, result=sentinel)
        )
        resolved = await resolve_source("c", 1, config)
        assert resolved is sentinel

    asyncio.run(_run())


def test_resolve_storage_prefers_injected_client(tmp_path) -> None:
    injected = LocalStorageClient(tmp_path)
    assert resolve_storage(_config(storage_client=injected)) is injected
    assert resolve_storage(_config()) is None  # 环境已清空（autouse fixture）


# ---- join：finding -> 条款 span ----


def test_build_comment_specs_join_and_unmatched() -> None:
    """clause_id -> 条款文本 -> span 三级 join；未知 id 计未匹配并跳过。"""
    sample = build_sample(
        11, clause_count=6, picks=[(0, "high"), (2, "medium"), (4, "low")]
    )
    parsed = derive_clauses(sample.data)
    state_clauses = [
        Clause(clause_id=clause.clause_id, index=position, text=clause.text)
        for position, clause in enumerate(sample.clauses)
    ]
    findings = list(sample.findings) + [
        Finding(
            clause_id="ghost-99",
            agent="commercial_expert",
            risk_type="payment_risk",
            risk_level="high",
            summary="孤儿 finding",
            suggestion="无",
            citations=[],
            evidence="",
        )
    ]

    specs, unmatched = build_comment_specs(findings, state_clauses, parsed)

    assert unmatched == 1
    assert len(specs) == len(sample.findings)
    span_by_clause = {clause.clause_id: (clause.start, clause.end) for clause in sample.clauses}
    spec_spans = {spec.span for spec in specs}
    # 三条 finding 的 span 必须与派生条款逐一对应（join 不漂移）。
    for clause_id, span in span_by_clause.items():
        if clause_id in {f.clause_id for f in sample.findings}:
            assert span in spec_spans


def test_build_comment_specs_duplicate_text_takes_first() -> None:
    """同文多处出现取首处 span（确定性优先，避免批注随机漂移）。"""
    state_clauses = [Clause(clause_id="c1", index=0, text="重复条款文本")]
    parse_clauses = [
        _FakeClause(text="重复条款文本", start=0, end=6),
        _FakeClause(text="重复条款文本", start=20, end=26),
    ]
    findings = [
        Finding(
            clause_id="c1",
            agent="legal_expert",
            risk_type="general_risk",
            risk_level="medium",
            summary="s",
            suggestion="g",
            citations=[],
            evidence="",
        )
    ]
    specs, unmatched = build_comment_specs(findings, state_clauses, parse_clauses)
    assert unmatched == 0
    assert [spec.span for spec in specs] == [(0, 6)]


class _FakeClause:
    def __init__(self, text: str, start: int, end: int) -> None:
        self.text = text
        self.start = start
        self.end = end


# ---- render 节点：事件 / 占位 / 异常传播 ----


def _drain(emitter: EventEmitter) -> list[object]:
    events = []
    while not emitter.queue.empty():
        events.append(emitter.queue.get_nowait())
    return events


def test_render_node_emits_paired_events_and_returns_result(tmp_path) -> None:
    """节点发射 NODE_STARTED/NODE_FINISHED 且返回可解析的 render_result。"""

    async def _run() -> None:
        sample = build_sample(12, clause_count=5, picks=[(0, "high")])
        emitter = EventEmitter()
        config = _config(
            emitter=emitter,
            source_resolver=_docx_resolver(sample.data),
            storage_client=LocalStorageClient(tmp_path),
        )
        result = await render(_state(sample), config)  # type: ignore[arg-type]

        events = _drain(emitter)
        started = [e for e in events if e.type == "node.started" and e.node == NODE_NAME]
        finished = [e for e in events if e.type == "node.finished" and e.node == NODE_NAME]
        assert len(started) == 1 and len(finished) == 1

        payload = json.loads(result["render_result"])  # type: ignore[index]
        assert payload["status"] == "rendered"
        assert payload["comments_count"] == 1

    asyncio.run(_run())


def test_render_node_placeholder_still_completes() -> None:
    """占位路径也要走完节点（发齐事件），保证图能抵达 END。"""

    async def _run() -> None:
        emitter = EventEmitter()
        result = await render(  # type: ignore[arg-type]
            _state(build_sample(13, picks=[])), _config(emitter=emitter)
        )
        assert result["render_result"].startswith(PLACEHOLDER_PREFIX)  # type: ignore[index]
        events = _drain(emitter)
        assert [e.type for e in events] == ["node.started", "node.finished"]

    asyncio.run(_run())


def test_render_node_propagates_render_error(tmp_path) -> None:
    """源字节损坏（非 zip）：RenderError 必须冒泡（不吞、不写坏产物）。"""

    async def _run() -> None:
        config = _config(
            emitter=EventEmitter(),
            source_resolver=_docx_resolver(b"not-a-zip"),
            storage_client=LocalStorageClient(tmp_path),
        )
        with pytest.raises(RenderError):
            await render(_state(build_sample(14, picks=[])), config)  # type: ignore[arg-type]

    asyncio.run(_run())
