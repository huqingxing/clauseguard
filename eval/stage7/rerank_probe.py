"""阶段 7 补充证据：rerank 端点的一次真实调用，只测「代价」不测「收益」。

为什么单独测：阶段 5 的 rerank on/off 收益报告从未产出（lab/out 下没有
rerank.json），因此配置里 use_rerank=false。但要写「取舍」就得有量化的延迟/成本，
不能只写「可能比较慢」。本脚本发一次真实 gte-rerank-v2 请求，把 API 回传的
token 与实测延迟落盘，报告只把它当「上线代价」引用，绝不当成效果结论。
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import httpx
import psycopg

REPO_ROOT = Path(__file__).resolve().parents[2]
OUT = REPO_ROOT / "eval" / "out" / "stage7" / "rerank_probe.json"
ENDPOINT = (
    "https://dashscope.aliyuncs.com/api/v1/services/rerank/text-rerank/text-rerank"
)
DSN = "postgresql://clauseguard@127.0.0.1:55432/clauseguard_retrieval?connect_timeout=2"
QUERY = "当事人约定的违约金过分高于造成的损失，可以请求人民法院予以适当减少吗"


def main() -> None:
    key = os.environ.get("CLAUSEGUARD_AGENT_LLM_API_KEY", "")
    if not key:
        raise RuntimeError("缺少 CLAUSEGUARD_AGENT_LLM_API_KEY，不做静默降级")
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT text FROM law_chunk WHERE strategy = 'S5_context' "
            "AND embedding IS NOT NULL ORDER BY chunk_id LIMIT 20"
        )
        docs = [row[0][:1024] for row in cur.fetchall()]
    if len(docs) < 20:
        raise RuntimeError(f"候选只有 {len(docs)} 条，不足 20 条")

    payload = {
        "model": "gte-rerank-v2",
        "input": {"query": QUERY, "documents": docs},
        "parameters": {"return_documents": False, "top_n": 20},
    }
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    os.environ["HTTP_PROXY"] = ""
    os.environ["HTTPS_PROXY"] = ""
    started = time.perf_counter()
    with httpx.Client(timeout=60.0) as client:
        response = client.post(ENDPOINT, headers=headers, json=payload)
    elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
    response.raise_for_status()
    body = response.json()
    result = {
        "model": "gte-rerank-v2",
        "documents": len(docs),
        "query_chars": len(QUERY),
        "document_chars": sum(len(doc) for doc in docs),
        "http_status": response.status_code,
        "latency_ms": elapsed_ms,
        "results": len(body["output"]["results"]),
        "total_tokens": (body.get("usage") or {}).get("total_tokens"),
        "disclaimer": "仅端点代价实测；rerank on/off 收益报告未产出，不构成效果结论",
    }
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
