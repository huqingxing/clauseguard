"""Stage 10 render 服务层：源合同 -> 条款偏移 -> 批注 docx -> 存储产物。

职责链（每步都可单测）：
1. resolve_source：按 config 注入的 source_resolver（测试/可覆盖点）优先，
   否则 MinIO（生产）-> local_dir（本地开发）-> None（占位）。
2. 解析复用 Stage 5 的 load_document + split_document——条款偏移的事实来源，
   不重新发明；writer 侧重建的坐标系必须与解析层逐块相等（自校验，不等即
   RenderError，绝不把批注挂到错误位置）。
3. join：finding.clause_id -> ReviewState.clauses 的条款文本 -> 解析层条款
   span（文本等值匹配；同一文本多处出现取首处，确定性优先）。
4. write：lxml 挂批注（见 docx_writer）。
5. upload：StorageClient 端口（MinIO 预签名 / 本地落盘），返回带签名 URL。

为什么 parse 在 render 节点重跑而不把 offset 存进 ReviewState：状态字段名由
契约锁定，且解析是确定性纯函数——同一源文档重跑必得同一坐标系，比在状态里
维护一份易漂移的偏移副本更稳（Stage 5 已有 raw[start:end]==text 硬约束）。
"""

from __future__ import annotations

import asyncio
import json
import logging
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from langchain_core.runnables import RunnableConfig

from app.clients.minio_client import (
    DOCX_CONTENT_TYPE,
    ArtifactRef,
    MinioSettings,
    ResolvedSource,
    StorageClient,
    artifact_object_key,
    build_storage,
    resolve_source_from_local,
    resolve_source_from_minio,
)
from app.graph.state import Clause, Finding, active_findings
from app.parse.loader import load_document
from app.parse.splitter import split_document
from app.render.docx_writer import CommentSpec, DocxEditor, RenderError

logger = logging.getLogger("app.render.service")

# 占位结果前缀保持不变（Stage 9 语义）：源不可用/非 docx/未配置存储时显式占位，
# 保证图能抵达 END 并下发 run.completed，同时 reason 字段说清为什么没有产物。
PLACEHOLDER_PREFIX = "PLACEHOLDER_RENDER"


@dataclass(frozen=True)
class RenderOutcome:
    """一次 render 的结果：写回状态的字符串 + 产物引用 + 统计。"""

    status: str  # "rendered" | "placeholder"
    render_result: str
    artifact: ArtifactRef | None
    comments_count: int
    unmatched: int


async def resolve_source(
    contract_id: str, version: int, config: RunnableConfig
) -> ResolvedSource | None:
    """解析源合同字节；config 注入优先（运行态依赖走 config，铁律 4）。"""
    configurable = (config or {}).get("configurable") or {}
    resolver = configurable.get("source_resolver")
    if resolver is not None:
        resolved: ResolvedSource | None = await resolver(contract_id, version)
        return resolved
    settings = MinioSettings()
    source = await resolve_source_from_minio(settings, contract_id, version)
    if source is not None:
        return source
    return resolve_source_from_local(settings, contract_id, version)


def resolve_storage(config: RunnableConfig) -> StorageClient | None:
    """解析存储后端；config 注入优先，否则按 MinioSettings 选择。"""
    configurable = (config or {}).get("configurable") or {}
    storage = configurable.get("storage_client")
    if storage is not None:
        client: StorageClient = storage
        return client
    return build_storage(MinioSettings())


def build_comment_specs(
    findings: list[Finding],
    state_clauses: list[Clause],
    parse_clauses: list[Any],
) -> tuple[list[CommentSpec], int]:
    """finding -> CommentSpec 的连接；返回 (specs, 未匹配条款数)。

    匹配链：clause_id -> ReviewState.clauses 的条款文本 -> 解析层条款 span。
    文本等值（切分结果与入参条款同源时严格相等）；多处同文取首处，保证确定性。
    """
    text_to_span: dict[str, tuple[int, int]] = {}
    for clause in parse_clauses:
        text_to_span.setdefault(clause.text, (clause.start, clause.end))
    clause_text_by_id = {clause.clause_id: clause.text for clause in state_clauses}

    specs: list[CommentSpec] = []
    unmatched = 0
    for finding in findings:
        text = clause_text_by_id.get(finding.clause_id)
        span = text_to_span.get(text) if text else None
        if span is None:
            unmatched += 1
            logger.warning(
                "finding %s/%s 未匹配到源文档条款 span，跳过批注",
                finding.clause_id,
                finding.risk_type,
            )
            continue
        specs.append(
            CommentSpec(
                span=span,
                risk_level=finding.risk_level,
                risk_type=finding.risk_type,
                summary=finding.summary,
                suggestion=finding.suggestion,
                citations=tuple(finding.citations),
            )
        )
    return specs, unmatched


def _render_docx_sync(
    source: ResolvedSource, findings: list[Finding], state_clauses: list[Clause]
) -> tuple[bytes, int, int]:
    """同步回写管线（重 IO/CPU，由调用方放线程池执行）。

    坐标系自校验是硬门槛：writer 重建的块文本/raw 必须与 loader 逐块相等，
    否则抛 RenderError——批注挂错位置比不挂更糟。
    """
    with tempfile.TemporaryDirectory(prefix="clauseguard-render-") as tmp_dir:
        path = Path(tmp_dir) / "source.docx"
        path.write_bytes(source.data)
        document = load_document(path)
        parsed = split_document(document)

    editor = DocxEditor(source.data)
    anchor_map = editor.anchor_map
    if (
        anchor_map.text != document.raw
        or [block.text for block in anchor_map.blocks]
        != [block.text for block in document.blocks]
    ):
        raise RenderError("锚点坐标系与解析层不一致（块文本或 raw 不匹配），拒绝回写")

    specs, unmatched = build_comment_specs(findings, state_clauses, parsed.clauses)
    result = editor.write_comments(specs)
    return result.data, result.comments_written, unmatched


def _placeholder(state: Mapping[str, Any], reason: str) -> RenderOutcome:
    """显式占位：不假装修写成功，reason 说明缺口（红线：不做假实现）。"""
    approved = bool(state.get("approved"))
    findings = len(active_findings(list(state.get("findings") or [])))
    return RenderOutcome(
        status="placeholder",
        render_result=(
            f"{PLACEHOLDER_PREFIX}: approved={approved}, "
            f"findings={findings}, reason={reason}"
        ),
        artifact=None,
        comments_count=0,
        unmatched=0,
    )


async def render_for_state(
    state: Mapping[str, Any], config: RunnableConfig
) -> RenderOutcome:
    """render 节点的服务入口：解析 -> 校验 -> 挂批注 -> 上传 -> 产物引用。

    state 用 Mapping 而非 dict[str, Any]：调用方既传图状态（ReviewState TypedDict），
    也传测试构造的普通 dict——Mapping 对两者都协变兼容。
    """
    contract_id = str(state.get("contract_id") or "")
    version = int(state.get("version") or 0)
    findings = active_findings(list(state.get("findings") or []))
    state_clauses = list(state.get("clauses") or [])

    source = await resolve_source(contract_id, version, config)
    if source is None:
        return _placeholder(state, "source-unavailable")
    if source.doc_type != "docx":
        # TODO(Stage 11+): PDF 合同回写（转 docx 或原生 PDF 批注格式）。
        return _placeholder(state, f"non-docx-source:{source.doc_type}")
    storage = resolve_storage(config)
    if storage is None:
        return _placeholder(state, "storage-unconfigured")

    try:
        data, written, unmatched = await asyncio.to_thread(
            _render_docx_sync, source, findings, state_clauses
        )
    except RenderError:
        raise
    except Exception as exc:  # 解析/切分异常统一包成 RenderError，带上下文
        raise RenderError(f"回写管线失败: {exc}") from exc

    ref = await storage.upload(
        artifact_object_key(contract_id, version), data, DOCX_CONTENT_TYPE
    )
    payload = {
        "status": "rendered",
        "object_key": ref.object_key,
        "download_url": ref.download_url,
        "comments_count": written,
        "size_bytes": ref.size_bytes,
        "unmatched_clauses": unmatched,
    }
    logger.info(
        "render 完成: contract=%s v=%s comments=%d unmatched=%d bytes=%d",
        contract_id,
        version,
        written,
        unmatched,
        ref.size_bytes,
    )
    return RenderOutcome(
        status="rendered",
        render_result=json.dumps(payload, ensure_ascii=False),
        artifact=ref,
        comments_count=written,
        unmatched=unmatched,
    )
