"""「一次审查 = 一个 thread」验收 harness（ADR-0006）。

为什么单独成模块：T1/T2/T3/阶段 4 都必须走**生产构造点**（POST /v1/runs 与
/v1/runs/{run_id}/resume），而不是手动拼 config 喂图——否则测的不是 thread 作用域
本身，而是测试自己造的 thread_id。共享注入的 checkpointer + registry 用来模拟
生产 Postgres（跨请求、跨「进程重启」仍在），与 Stage 9 验收同口径。

确定性来自三处：
- 固定 docx 字节（tests/render/fixtures.build_sample_docx），经真实解析层切分；
- MODEL_BACKEND=mock（固定输出，不联网、不需密钥）；
- 只替换 ingest 的外部 HTTP 下载（_download_source），业务代码零改动。

禁止项守卫：本模块不清理 checkpoint、不在 run 前清空 findings、不改事件契约。
"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass, field
from typing import Any

import httpx
from langgraph.checkpoint.memory import MemorySaver

from app.agents.settings import get_agent_settings
from app.config import get_settings
from app.db import InMemoryRunRegistry
from app.incremental.cache import InMemoryIncrementalStore
from app.main import create_app
from tests.render.fixtures import build_sample_docx

# env 前缀：AgentSettings 用 clauseguard_agent_，Settings 用字段名原样。
ENV_MODEL_BACKEND = "CLAUSEGUARD_AGENT_MODEL_BACKEND"
ENV_MOCK_LATENCY = "CLAUSEGUARD_AGENT_MOCK_LATENCY_S"


def _configure_mock_backend() -> None:
    """把模型切到确定性替身；必须在 create_app 前调用（两个 settings 都是 lru_cache）。"""
    os.environ[ENV_MODEL_BACKEND] = "mock"
    os.environ[ENV_MOCK_LATENCY] = "0"
    os.environ.setdefault("CLAUSEGUARD_RETRIEVAL_MODE", "mock")
    get_agent_settings.cache_clear()
    get_settings.cache_clear()


def _fixed_docx(index: int, clause_count: int = 6) -> bytes:
    """确定性合同样本；index 不同 → 条款正文不同（供 T2 区分版本）。"""
    return build_sample_docx(
        index,
        clause_count=clause_count,
        with_header=False,
        with_table=False,
        with_hyperlink=False,
    )


def _high_risk_docx() -> bytes:
    """确定性高风险合同：正文含 mock 模型的 high 标记词「不设上限」，必然触发
    human_approval 的 interrupt 断点，供阶段 4 验证「审批 resume → completed」。
    """
    import io

    from docx import Document

    doc = Document()
    doc.add_paragraph(style="Title").add_run("高风险测试合同")
    for no, title in enumerate(("违约责任", "责任范围", "争议解决"), 1):
        doc.add_paragraph(f"第{no}条 {title}", style="Heading 2")
        doc.add_paragraph(
            f"第{no}条约定：乙方应承担的全部赔偿责任不设上限，且甲方有权单方解除本合同。"
        )
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


# mock 模型 high 判定标记（见 app/agents/mock_model.py），仅供验收样本构造。
HIGH_RISK_DOCX_INDEX = -1


def _parse_sse(text: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for line in text.splitlines():
        if line.startswith("data:"):
            events.append(json.loads(line[len("data:") :].strip()))
    return events


def normalize_clause_id(clause_id: str) -> str:
    """剥离解析层临时文件名前缀：`tmp6yiw0nj7#0004` → `#0004`。

    ingest 把下载字节写进 tempfile.NamedTemporaryFile 再解析，文件名每次随机，故
    clause_id 前缀不可复现；`#序号` 才是由固定 docx 决定的稳定部分。仅用于快照对比
    与跨版本断言，不触碰生产代码、不改写 run 下发的原始 findings。
    """
    _, sep, ordinal = str(clause_id).partition("#")
    return f"#{ordinal}" if sep else str(clause_id)


def normalize_findings(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """逐字段归一化并稳定排序，供 T3 快照逐位对比。"""
    out: list[dict[str, Any]] = []
    for finding in findings:
        item = dict(finding)
        item["clause_id"] = normalize_clause_id(str(item.get("clause_id") or ""))
        out.append(item)
    return sorted(
        out,
        key=lambda f: (f.get("clause_id", ""), f.get("agent", ""), f.get("risk_type", "")),
    )


@dataclass
class RunResult:
    """一次 /v1/runs 的可观测结果。"""

    contract_id: str
    version: int
    events: list[dict[str, Any]] = field(default_factory=list)
    findings: list[dict[str, Any]] = field(default_factory=list)
    run_id: str = ""
    interrupted: bool = False
    completed: bool = False

    @property
    def unverified(self) -> list[dict[str, Any]]:
        return [f for f in self.findings if f.get("risk_level") == "unverified"]

    @property
    def clause_keys(self) -> set[str]:
        """用于跨版本互不包含断言：归一化 clause_id + evidence 前 20 字（正文随版本变）。

        用 evidence 而非仅 clause_id：不同版本 docx_index 不同 → 条款正文不同，
        即使序号相同也应视为不同条款；只有「同一条款正文」才算串到对方版本。
        """
        keys = set()
        for finding in self.findings:
            cid = normalize_clause_id(str(finding.get("clause_id") or ""))
            evidence = str(finding.get("evidence") or "")[:20]
            keys.add(f"{cid}|{evidence}")
        return keys

    @property
    def normalized_findings(self) -> list[dict[str, Any]]:
        return normalize_findings(self.findings)


class ThreadScopeHarness:
    """共享 checkpointer/registry 的双端 runner；可跨『重启』保留持久化层。"""

    def __init__(self) -> None:
        _configure_mock_backend()
        self.saver = MemorySaver()
        self.registry = InMemoryRunRegistry()
        self.store = InMemoryIncrementalStore()
        self.app = create_app(
            checkpointer=self.saver,
            registry=self.registry,
            incremental_store=self.store,
        )
        # 每次 run 用哪份 docx：key = (contract_id, version)
        self._sources: dict[tuple[str, int], bytes] = {}
        # 安装下载替身：只替换外部 IO，ingest 的解析/哈希/条款映射全走真实代码。
        import app.graph.nodes.ingest as ingest_mod

        async def _fake_download(source: dict[str, Any]) -> tuple[bytes, str]:
            raw = str(source.get("raw") or "1:1")
            cid, _, ver = raw.partition(":")
            data = self._sources.get((cid, int(ver or 1)))
            if data is None:
                data = _fixed_docx(1)
            return data, str(source.get("filename") or "contract.docx")

        ingest_mod._download_source = _fake_download  # type: ignore[attr-defined]
        self._ingest_mod = ingest_mod

    def set_source(self, contract_id: str, version: int, docx_index: int) -> None:
        self._sources[(contract_id, version)] = (
            _high_risk_docx() if docx_index == HIGH_RISK_DOCX_INDEX else _fixed_docx(docx_index)
        )

    def restart(self) -> None:
        """模拟 python-agent 进程重启：新 app 实例，但持久化层（saver/registry）不变。

        与 Stage 9 验收同口径：注入同一实例即等价于「重启后 Postgres 里的 checkpoint
        与 run_registry 仍在」。新 app 会重新 build_graph，但不会重建 checkpointer。
        """
        _configure_mock_backend()
        self.app = create_app(
            checkpointer=self.saver,
            registry=self.registry,
            incremental_store=self.store,
        )

    async def _post(self, path: str, payload: Any) -> list[dict[str, Any]]:
        transport = httpx.ASGITransport(app=self.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(path, json=payload, timeout=120.0)
            assert resp.status_code == 200, f"{path} -> {resp.status_code}: {resp.text[:300]}"
            return _parse_sse(resp.text)

    async def run_once(self, contract_id: str, version: int, docx_index: int = 1) -> RunResult:
        """走生产 /v1/runs 端点发起一次完整审查。"""
        self.set_source(contract_id, version, docx_index)
        events = await self._post(
            "/v1/runs",
            {
                "contract_id": contract_id,
                "version": version,
                "mode": "full",
                "source": {
                    "download_url": f"/api/v1/contracts/{contract_id}/content",
                    "filename": "contract.docx",
                    "raw": f"{contract_id}:{version}",
                },
            },
        )
        result = RunResult(contract_id=contract_id, version=version, events=events)
        for ev in events:
            if ev.get("type") == "run.started":
                result.run_id = str(ev.get("run_id") or "")
            elif ev.get("type") == "interrupt":
                result.interrupted = True
            elif ev.get("type") == "run.completed":
                result.completed = True
                result.findings = list(ev.get("findings") or [])
        return result

    async def resume(self, run_id: str, approvals: Any = None) -> RunResult:
        """走生产 resume 端点，模拟审批后续跑。"""
        events = await self._post(
            f"/v1/runs/{run_id}/resume",
            approvals if approvals is not None else [],
        )
        result = RunResult(contract_id="", version=0, events=events, run_id=run_id)
        for ev in events:
            if ev.get("type") == "run.completed":
                result.completed = True
                result.findings = list(ev.get("findings") or [])
        return result

    async def resume_raw(self, run_id: str, approvals: Any = None) -> tuple[int, str]:
        """不断言 200，供「旧格式记录应被明确拒绝」用例检查状态码与错误体。"""
        transport = httpx.ASGITransport(app=self.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                f"/v1/runs/{run_id}/resume",
                json=approvals if approvals is not None else [],
                timeout=30.0,
            )
            return resp.status_code, resp.text

    def checkpoint_threads(self) -> set[str]:
        """列出 checkpointer 里实际落过的 thread_id（内存实现的存储键）。"""
        store = getattr(self.saver, "storage", None)
        if isinstance(store, dict):
            return {str(k) for k in store}
        return set()


def run(coro: Any) -> Any:
    """同步驱动异步 harness（版本表无 pytest-asyncio，沿用项目既有口径）。"""
    return asyncio.run(coro)
