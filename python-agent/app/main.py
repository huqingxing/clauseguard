"""FastAPI 入口。Stage 0 只提供存活探针；图注册与 SSE 端点在后续阶段接入。"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # 为什么不在这里建连接池：Stage 0 无图运行，避免空转资源；后续阶段在此装配
    # postgres checkpointer 与 Java 客户端连接池。
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="ClauseGuard Agent", version="0.1.0", lifespan=lifespan)

    @app.get("/healthz")
    async def healthz() -> JSONResponse:
        payload: dict[str, Any] = {"status": "ok", "service": "python-agent"}
        return JSONResponse(payload)

    return app


app = create_app()
