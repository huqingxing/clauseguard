"""审查工具取候选的唯一分流点：live 走 pgvector 真实法条，mock 走固定样例。

为什么单独一层而不是让三个工具各自实现：白名单由 ToolMessage 的
`{"candidates":[...]}` 还原（ADR-0003），三个工具必须共用同一份 YAML 参数与同一个
服务实例，否则「改了 retrieval.yaml 只有部分专家生效」这种漂移在报告里几乎看不出来。
工具名保留 `mock_` 前缀是既有契约（prompt、测试、Inspector 都按名字引用），
它描述的是「具备 mock 能力」，不代表当前一定返回假数据。

阶段 6 只建了法条库；制度库 / 历史相似条款库尚未建，因此三个工具在 live 下都指向
同一份真实法条索引——宁可让不同类型的检索返回同一批可核验的真法条，也不能让
白名单里混入 `sim-00x` 这种模型无法证伪的假 ID。
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from typing import Any

from retrieval.config import get_retrieval_settings

logger = logging.getLogger("app.tools.retrieval_backend")


@lru_cache(maxsize=256)
def _query_vector(query: str) -> tuple[float, ...]:
    """同一条款会被多个工具用同一 query 反复检索，缓存住 embedding 避免重复付费调用。

    缓存键只有 query：配置（模型/维度）在进程内不会变，切模型必须重启服务，
    而重启会清空 lru_cache，不会出现「新配置配旧向量」的静默错配。
    """
    from retrieval.embedder import Embedder

    with Embedder(get_retrieval_settings()) as embedder:
        return tuple(embedder.embed([query]).vectors[0])


def _hit_to_candidate(hit: Any) -> dict[str, Any]:
    # id 必须是 chunk_id：白名单、Critic 快照、报告回查都靠它定位到语料原文。
    label = f"《{hit.law}》{hit.article}"
    return {
        "id": hit.chunk_id,
        "article": label,
        # title/excerpt 会被 ADR-0003 的产出闸原样重建进 citation，模型自带的
        # 文本一律不采信；这里直接给可核验的法名+条号与条文原文。
        "title": label,
        "text": hit.text,
        "score": round(float(hit.score), 8),
    }


def live_payload(query: str) -> str | None:
    """返回 ToolMessage 用的 JSON 字符串；None 表示当前是 mock 模式，调用方回落样例。

    检索后端不可用时返回**空候选集**而不是抛异常：白名单为空 → 引用被丢弃 →
    finding 降级 unverified（ADR-0003 既定行为），审查链路绝不能因为检索挂掉而 500。
    """
    settings = get_retrieval_settings()
    if settings.retrieval_mode != "live":
        return None
    try:
        from retrieval.api import get_service

        service = get_service()
        hits = service.search(
            query=query,
            k=settings.default_k,
            embedding=_query_vector(query),
            use_bm25=settings.use_bm25_default,
            use_rerank=settings.use_rerank_default,
        )
    except Exception as exc:
        logger.warning("live retrieval unavailable, degrade to empty candidates: %s", exc)
        return json.dumps({"candidates": []}, ensure_ascii=False)
    logger.info("live retrieval hits=%d strategy=%s", len(hits), settings.index_strategy)
    return json.dumps({"candidates": [_hit_to_candidate(hit) for hit in hits]}, ensure_ascii=False)
