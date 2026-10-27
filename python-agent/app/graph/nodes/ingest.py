"""ingest 节点：取合同文件 → 解析 → 条款切分，填 contract_id/version/clauses。

Stage 12 真实化背景：此前本节点只返回 mock 元信息、从不加载文件，生产链路的
clauses 恒为空（既有测试全部直接注入 clauses，掩盖了这一点）。现在由 Java 上传时
随运行请求透传 source（「文件在哪、怎么取」），本节点经 Java REST 下载后真实解析。

三条路径，按优先级：
1. state 已带 clauses（测试注入 / 断点恢复重放）→ 原样保留，不重复加载；
2. source.download_url 可用 → 下载 → load_document → split_document；
   content_hash 取文件字节 sha256（人工审批乐观锁的基线，Stage 9）；
3. 都不可用 → 保持 mock 元信息 + 空 clauses，发 node.failed + degraded=true——
   显式降级而不是假装解析成功（红线：不做「看起来能跑」的假实现）。
"""

import asyncio
import hashlib
import logging
import tempfile
import time
from pathlib import Path
from typing import Any

import httpx
from langchain_core.runnables import RunnableConfig

from app.config import get_settings
from app.events import EventType, get_emitter
from app.graph.state import Clause, ReviewState
from app.parse.loader import load_document
from app.parse.models import ParseError
from app.parse.splitter import split_document

logger = logging.getLogger("app.graph.ingest")

# 下载超时：与 Java→Python 的 SSE 超时口径一致（30s），大文件慢网络下宁可失败重试
# 也不无限挂起——ingest 卡死会拖死整轮 run。
DOWNLOAD_TIMEOUT_S = 30.0


def mock_fetch_minio_meta(contract_id: str) -> dict[str, Any]:
    """TODO(Stage 12 后续): 替换为真实对象存储元信息查询；当前仅作无 source 时的兜底。"""
    return {"contract_id": contract_id, "version": 1}


def _state_source(state: ReviewState, config: RunnableConfig) -> dict[str, Any] | None:
    """运行源描述：优先取 state（可随 checkpoint 恢复），回落 config（按 run 注入）。"""
    from_state = state.get("source")
    if isinstance(from_state, dict) and from_state.get("download_url"):
        return from_state
    configurable = (config or {}).get("configurable") or {}
    from_config = configurable.get("contract_source")
    if isinstance(from_config, dict) and from_config.get("download_url"):
        return from_config
    return None


async def _download_source(source: dict[str, Any]) -> tuple[bytes, str]:
    """经 Java 暴露的 REST 通道拉取合同文件（铁律 1：Python 不直连业务库/存储）。"""
    url = str(source["download_url"])
    # Java 给的是相对路径（/api/v1/contracts/{id}/content）——它不知道 Python 眼里自己
    # 叫什么地址，所以补 base_url 是编排层的责任。httpx 只认绝对 URL，不补会直接报
    # "Request URL is missing an 'http://' or 'https://' protocol" 并降级成空条款。
    if url.startswith("/"):
        url = f"{get_settings().java_base_url.rstrip('/')}{url}"
    async with httpx.AsyncClient(timeout=DOWNLOAD_TIMEOUT_S) as client:
        response = await client.get(url)
        response.raise_for_status()
        filename = str(source.get("filename") or "contract")
        return response.content, filename


def _clauses_from_file(path: Path) -> list[Clause]:
    """解析 + 切分，并把解析层 Clause 映射为图状态 Clause（字段名对齐 + 顺序位）。

    偏移（start/end）不进状态：render 节点按源文档重解析取 span（Stage 10 设计），
    状态里维护偏移副本会与源文件漂移。
    """
    document = load_document(path)
    result = split_document(document)
    return [
        Clause(
            clause_id=clause.id,
            index=position,
            text=clause.text,
            risk_level="unknown",
            no=clause.no,
            hash=clause.hash,
        )
        for position, clause in enumerate(result.clauses)
    ]


async def ingest(state: ReviewState, config: RunnableConfig) -> dict[str, object]:
    emitter = get_emitter(config)
    await emitter.emit(EventType.NODE_STARTED, node="ingest", agent="ingest")
    start = time.monotonic()

    existing = list(state.get("clauses") or [])
    meta = mock_fetch_minio_meta(state["contract_id"])
    version = int(state.get("version") or 0) or int(meta["version"])

    if existing:
        # 调用方已提供条款：测试注入或断点恢复后的重放都不应重复下载解析。
        duration_ms = int((time.monotonic() - start) * 1000)
        await emitter.emit(
            EventType.NODE_FINISHED, node="ingest", duration_ms=duration_ms, tokens=0
        )
        logger.info("ingest 复用已注入条款 %d 条（version=%s）", len(existing), version)
        return {"contract_id": state["contract_id"], "version": version}

    source = _state_source(state, config)
    clauses: list[Clause] = []
    content_hash = str(state.get("content_hash") or "")
    degraded = False
    failure: tuple[str, str] | None = None

    if source is not None:
        try:
            payload, filename = await _download_source(source)
            content_hash = hashlib.sha256(payload).hexdigest()
            suffix = Path(filename).suffix or ".docx"
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                tmp.write(payload)
                tmp_path = Path(tmp.name)
            try:
                clauses = _clauses_from_file(tmp_path)
            finally:
                # 磁盘清理丢进线程池：async 函数里不做阻塞文件操作（ASYNC240）。
                await asyncio.to_thread(tmp_path.unlink, True)
            logger.info(
                "ingest 解析完成: contract=%s clauses=%d bytes=%d",
                state["contract_id"],
                len(clauses),
                len(payload),
            )
        except (httpx.HTTPError, ParseError, OSError, ValueError) as exc:
            # 显式降级：源不可达/解析失败都不假装成功——node.failed 进时间线，
            # degraded 进 run.completed，clauses 留空让下游自然收尾（零发现而非假发现）。
            degraded = True
            failure = ("INGEST_SOURCE_UNAVAILABLE", str(exc)[:200])
            logger.warning("ingest 源不可用，降级为空条款: %s", exc)
    else:
        degraded = True
        failure = ("INGEST_SOURCE_MISSING", "运行未携带合同源信息（source.download_url 缺失）")
        logger.warning("ingest 缺少 source，降级为空条款: contract=%s", state["contract_id"])

    if failure is not None:
        # 失败语义与 expert_review 对齐：node.failed 取代 node.finished（不凑对）。
        await emitter.emit(
            EventType.NODE_FAILED,
            node="ingest",
            agent="ingest",
            code=failure[0],
            message=failure[1],
        )
    else:
        duration_ms = int((time.monotonic() - start) * 1000)
        await emitter.emit(
            EventType.NODE_FINISHED, node="ingest", duration_ms=duration_ms, tokens=0
        )
    return {
        "contract_id": state["contract_id"],
        "version": version,
        "clauses": clauses,
        "content_hash": content_hash,
        "degraded": degraded,
    }


# 兼容名：早期调用方（测试）以 ingest_node 引用本节点。
ingest_node = ingest
