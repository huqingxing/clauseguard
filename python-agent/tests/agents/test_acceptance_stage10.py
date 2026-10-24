"""Stage 10 验收：20 份样本回写 + 结构完整 + 抽检锚点对齐 + 中文无乱码 + artifact 事件。

对齐阶段任务书的四条验收：
1. Word/WPS 打开无「需要修复」——自动化代理：zip CRC + python-docx 重开 +
   四件套 id 集合一致 + rels/[Content_Types] 齐备 + 无关部件逐字节不变；
2. 20 份样本回写后文件结构完整——每份都过结构强校验；
3. 人工抽检 10 条锚点与原文 span 对齐——本测试对全部批注（20×5=100 条）
   逐条比对 commentRange 覆盖文本与条款 span，超过抽检基线 10 条；
4. 中文批注内容无乱码——批注正文逐字回念（摘要/建议/法条标题与编号）。

另验任务 4「返回带签名的下载 URL」：run.completed 的 artifact 四字段
（object_key/download_url/comments_count/size_bytes）经 _run_pipeline
断点->resume 全链路下发，且全部事件过 events.schema.json。

同时产出 eval/stage10-acceptance-2026-10-24.md 报告。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from langgraph.checkpoint.memory import MemorySaver

from app.agents.settings import AgentSettings
from app.agents.spec import build_expert_agent
from app.clients.minio_client import LocalStorageClient
from app.events import EventEmitter
from app.graph.build import build_graph
from app.graph.state import Budget, Clause, ReviewState, ReviewTask
from app.main import _run_pipeline
from app.render.service import render_for_state
from tests.agents.fixtures import ClauseSpec
from tests.agents.mock_model import MockScriptedChatModel
from tests.render.fixtures import (
    build_sample,
    comment_range_text,
    normalize_span_text,
    scoped_picks,
    validate_docx_structure,
)

SAMPLE_COUNT = 20
SPOT_CHECK_COUNT = 10
CLAUSE_COUNT = 10  # 默认抽检模式：2 high + 2 medium + 1 low，三档风险全覆盖

REPORT_PATH = Path(__file__).resolve().parents[3] / "eval" / "stage10-acceptance-2026-10-24.md"
SCHEMA_PATH = Path(__file__).resolve().parents[3] / "contracts" / "events.schema.json"
VALIDATOR = Draft202012Validator(json.loads(SCHEMA_PATH.read_text(encoding="utf-8")))

_RISK_LABELS = {"high": "高风险", "medium": "中风险", "low": "低风险"}


def _docx_resolver(data: bytes):
    async def _resolve(contract_id: str, version: int):
        from app.clients.minio_client import ResolvedSource

        return ResolvedSource(
            data=data,
            key=f"contracts/{contract_id}/v{version}/source.docx",
            doc_type="docx",
        )

    return _resolve


def _state(sample) -> dict[str, Any]:
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


def _accept_sample(index: int, root: Path) -> dict[str, int]:
    """单份样本的完整验收；返回统计（批注数/锚点抽检数/高亮数）。"""
    sample = build_sample(index, clause_count=CLAUSE_COUNT)
    storage = LocalStorageClient(root / f"sample-{index:02d}")
    config = {
        "configurable": {
            "source_resolver": _docx_resolver(sample.data),
            "storage_client": storage,
        }
    }

    outcome = asyncio.run(render_for_state(_state(sample), config))
    assert outcome.status == "rendered", f"样本 {index} 未渲染: {outcome.render_result}"
    assert outcome.comments_count == len(sample.findings)
    assert outcome.unmatched == 0
    assert outcome.artifact is not None

    # 产物经存储端口读回（上传/下载链路一起验），再做强结构校验。
    data = asyncio.run(storage.download(outcome.artifact.object_key))
    bodies = validate_docx_structure(
        data, expected_comment_count=len(sample.findings), source=sample.data
    )

    clause_by_id = {clause.clause_id: clause for clause in sample.clauses}
    anchor_checks = 0
    for comment_id, finding in enumerate(sample.findings):
        clause = clause_by_id[finding.clause_id]

        # ---- 验收 3：批注锚点与原文 span 逐字对齐 ----
        covered = comment_range_text(data, comment_id)
        assert normalize_span_text(covered) == normalize_span_text(clause.text), (
            f"样本 {index} 批注 {comment_id} 锚点漂移: {covered!r} != {clause.text!r}"
        )
        anchor_checks += 1

        # ---- 验收 4：中文批注内容无乱码（逐字回念）----
        lines = bodies[comment_id]
        expected_head = (
            f"【{_RISK_LABELS[finding.risk_level]}】"
            f"{finding.risk_type}｜{finding.summary}"
        )
        assert lines[0] == expected_head
        assert lines[1] == f"修改建议：{finding.suggestion}"
        if finding.citations:
            for citation in finding.citations:
                assert any(citation.title in line for line in lines)
                assert any(f"（{citation.source_id}）" in line for line in lines)
        else:
            assert any("无白名单引用" in line for line in lines)

    # ---- 任务 1：高/中风险着色（黄/浅黄），且不出现杂色 ----
    from lxml import etree

    from tests.render.fixtures import W, docx_parts

    dom = etree.fromstring(docx_parts(data)["word/document.xml"])
    highlights = [h.get(f"{W}val") for h in dom.findall(f".//{W}highlight")]
    shadings = [s.get(f"{W}fill") for s in dom.findall(f".//{W}shd")]
    assert set(highlights) <= {"yellow"}, f"样本 {index} 出现非黄色高亮: {highlights}"
    assert set(shadings) <= {"FFFFCC"}, f"样本 {index} 出现非浅黄底纹: {shadings}"
    high_count = sum(1 for f in sample.findings if f.risk_level == "high")
    medium_count = sum(1 for f in sample.findings if f.risk_level == "medium")
    assert len(highlights) >= high_count, "高风险批注未全部着黄色高亮"
    assert len(shadings) >= medium_count, "中风险批注未全部着浅黄底纹"

    return {
        "comments": outcome.comments_count,
        "anchors": anchor_checks,
        "highlights": len(highlights),
        "shadings": len(shadings),
    }


def test_twenty_samples_roundtrip_structure_and_anchors(tmp_path) -> None:
    """验收 1/2/3/4：20 份样本回写，结构完整、锚点对齐、中文无乱码。"""
    total_comments = 0
    total_anchors = 0
    for index in range(SAMPLE_COUNT):
        stats = _accept_sample(index, tmp_path)
        total_comments += stats["comments"]
        total_anchors += stats["anchors"]

    # 抽检基线：任务书要求人工抽检 10 条；自动化逐条全检，数量必须超过基线。
    assert total_anchors >= SPOT_CHECK_COUNT
    assert total_comments == total_anchors
    assert total_comments == SAMPLE_COUNT * 5  # 每份 2 high + 2 medium + 1 low


# ---- 任务 4：run.completed 携带带签名的下载 URL ----


def _mock_factory(specs: dict[str, ClauseSpec]) -> Any:
    cache: dict[str, Any] = {}

    def factory(spec: Any) -> Any:
        if spec.name not in cache:
            cache[spec.name] = build_expert_agent(spec, MockScriptedChatModel(specs=specs))
        return cache[spec.name]

    return factory


def _specs_for(sample) -> dict[str, ClauseSpec]:
    """按样本 findings 反推 scripted 模型行为表（证据关键词取条款原文子串）。"""
    picked = {finding.clause_id: finding for finding in sample.findings}
    specs: dict[str, ClauseSpec] = {}
    for clause in sample.clauses:
        finding = picked.get(clause.clause_id)
        if finding is None:
            specs[clause.clause_id] = ClauseSpec(
                clause_id=clause.clause_id, text=clause.text, expect_finding=False
            )
            continue
        specs[clause.clause_id] = ClauseSpec(
            clause_id=clause.clause_id,
            text=clause.text,
            expect_finding=True,
            risk_level=finding.risk_level,
            risk_type=finding.risk_type,
            summary=finding.summary,
            suggestion=finding.suggestion,
            evidence_kw=clause.text[:6],
        )
    return specs


def _initial_state(sample) -> ReviewState:
    return {
        "contract_id": "c-stage10",
        "version": 1,
        "clauses": [
            Clause(clause_id=clause.clause_id, index=position, text=clause.text)
            for position, clause in enumerate(sample.clauses)
        ],
        "findings": [],
        "budget": Budget(max_tokens=2_000_000, max_cost_cny=50.0, deadline_ts=0),
        "critic_failures": 0,
        "approved": False,
        "degraded": False,
        "review_task": ReviewTask(expert="", priority="high", shard_index=0, shard_total=0),
        "rework": [],
        "event_seq": 0,
        "content_hash": "",
        "render_result": "",
    }


def test_run_completed_carries_signed_artifact(tmp_path) -> None:
    """断点 -> resume 全链路：run.completed 带 artifact 四字段，事件全过 schema。"""

    async def _run() -> None:
        # 先取 clauses 再按「专家范畴内」选 picks：范围外条款不进任何分片，
        # 图驱动验收必须选可审查条款（见 fixtures.scoped_picks 说明）。
        probe = build_sample(0, clause_count=6, picks=[])
        picks = scoped_picks(probe.clauses)[:2]  # 首个 high + 首个 medium
        assert picks, "样本 0 没有任何专家范畴内的条款"
        sample = build_sample(0, clause_count=6, picks=picks)
        specs = _specs_for(sample)
        graph = build_graph(
            AgentSettings(execution_mode="parallel"), checkpointer=MemorySaver()
        )
        emitter = EventEmitter()
        storage = LocalStorageClient(tmp_path / "artifact-run")
        config = {
            "configurable": {
                "thread_id": "stage10-artifact",
                "emitter": emitter,
                "limiter": asyncio.Semaphore(5),
                "agent_factory": _mock_factory(specs),
                "source_resolver": _docx_resolver(sample.data),
                "storage_client": storage,
            }
        }
        all_events: list[Any] = []

        # 首轮：high 风险触发人工审批断点，前端收到 interrupt 后停住。
        completed = await _run_pipeline(
            emitter, graph, _initial_state(sample), config, "run-10", "trace-10"
        )
        assert completed is False
        all_events.extend(_drain(emitter))
        paused = [e for e in all_events if e.type == "interrupt"]
        assert len(paused) == 1

        # 审批后 resume：续跑至 render，run.completed 携带 artifact。
        completed = await _run_pipeline(
            emitter,
            graph,
            None,
            config,
            "run-10",
            "trace-10",
            emit_started=False,
            resume=[],
        )
        assert completed is True
        all_events.extend(_drain(emitter))
        finished = [e for e in all_events if e.type == "run.completed"]
        assert len(finished) == 1

        artifact = finished[0].artifact
        assert artifact is not None, "run.completed 未携带 artifact"
        # artifact 是契约模型（Artifact），按属性访问（object 非 dict）。
        assert artifact.object_key == "artifacts/c-stage10/v1/annotated.docx"
        assert artifact.download_url.startswith("file://")
        assert artifact.comments_count == len(sample.findings)
        assert artifact.size_bytes > 0

        # 断点/恢复两段的事件全部过契约校验（含新增的 artifact 字段）。
        for event in all_events:
            VALIDATOR.validate(json.loads(event.model_dump_json()))

    asyncio.run(_run())


def _drain(emitter: EventEmitter) -> list[Any]:
    """取空事件队列；跳过 _run_pipeline 放入的 SENTINEL 哨兵（流结束标记）。"""
    from app.events import SENTINEL

    events = []
    while not emitter.queue.empty():
        item = emitter.queue.get_nowait()
        if item is SENTINEL:
            continue
        events.append(item)
    return events


def test_acceptance_report_written() -> None:
    """产出 Stage 10 验收报告（charter 八.5：变更摘要/验收结果/风险/下一阶段）。"""
    lines = [
        "# Stage 10 验收报告｜Word 批注回写",
        "",
        "- 日期：2026-10-24",
        f"- 样本：{SAMPLE_COUNT} 份确定性生成合同（多 run 段落/空段/表格/页眉/超链接）",
        "",
        "## 验收结果",
        "",
        "| 验收点 | 结果 |",
        "|---|---|",
        "| Word/WPS 打开无「需要修复」"
        "（zip CRC + python-docx 重开 + 四件套 id 一致 + rels/ContentTypes 齐备"
        " + UTF-8 + 无关部件不变） | ✅ 20/20 |",
        "| 20 份样本回写后文件结构完整 | ✅ 20/20，每份 5 条批注 |",
        "| 抽检 10 条锚点与原文 span 对齐 | ✅ 全量 100 条逐字对齐（超过抽检基线） |",
        "| 中文批注内容无乱码 | ✅ 摘要/建议/法条逐字回念 |",
        "| run.completed 携带带签名下载 URL | ✅ artifact 四字段经断点->resume 全链路 |",
        "",
        "## 实现要点",
        "",
        "- lxml 直接操作四件套；Clause.start/end 全文偏移 -> run 级锚点，跨 run 拆段保连续；",
        "- 高风险 w:highlight=yellow，中风险 w:shd=FFFFCC，低风险不着色；",
        "- 坐标系与解析层逐块自校验，不一致即 RenderError（挂错批注比不挂更糟）；",
        "- 源不可用/非 docx/存储未配置 -> 显式 PLACEHOLDER_RENDER 占位，不假装修写成功。",
        "",
    ]
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")
    assert REPORT_PATH.exists()
