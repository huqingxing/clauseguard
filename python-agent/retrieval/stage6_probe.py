"""配置重启生效的独立探针：每次以全新进程读一次 retrieval.yaml 并发一次检索。

为什么必须是独立进程：get_retrieval_settings 是 lru_cache，同进程改文件不会重读；
验收「改完重启生效」就得用真重启，不能用 cache_clear 假装。
"""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from app.main import create_app
from retrieval.config import get_retrieval_settings

QUERY = "当事人约定的违约金过分高于造成的损失，可以请求人民法院予以适当减少吗"


def main() -> None:
    settings = get_retrieval_settings()
    client = TestClient(create_app())
    # 不传 embedding：走真实 query 向量，两次探针的 top1 应当一致，
    # 只有条数随 YAML 的 top_k 变化——比喂假向量更能证明「参数生效、检索没被改坏」。
    response = client.post(
        "/v1/retrieval/search",
        json={"query": QUERY},
    )
    response.raise_for_status()
    body = response.json()
    # stdout 是父进程 acceptance_stage6 解析的 JSON 通道；换成 logger 会混进日志行，
    # 验收脚本就再也拿不到结构化的 before/after 结果。
    print(  # noqa: T201
        json.dumps(
            {
                "yaml_top_k": settings.default_k,
                "effective_top_k": body["config"]["top_k"],
                "strategy": body["config"]["strategy"],
                "hits": len(body["hits"]),
                "top1": body["hits"][0]["chunk_id"] if body["hits"] else "",
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
