"""端到端链路验证：Java 父上下文 → Python 节点 span → OTLP 上报 → 采集器收到。

为什么需要这个脚本：任务验收要求「Langfuse 里能看到一条 trace 串起 Java 与 6 个 agent span」。
Langfuse 需要账号与外网，CI/本地无法断言。本脚本把链路拆成可断言的三段：
  1. 用一段伪造的「Java traceparent」发起真实 /v1/runs（httpx + ASGI，走真实中间件与图）；
  2. Python 侧按该父上下文建 run/节点 span，并经真实 OTLP/HTTP 上报到本地采集器；
  3. 断言：根 span 的父 = Java span、6 个专家 span 同 trace 且挂在根 span 下、带 tokens。
把 OTEL_EXPORTER_OTLP_TRACES_ENDPOINT 换成 Langfuse 地址（+ Authorization 头）即真实上报，
这正是 `make trace-demo` 与生产同一条路径。

用法（在仓库根）：
    python-agent/.venv/Scripts/python.exe deploy/otel/verify_trace.py
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DEMO_DIR = ROOT / "deploy" / "demo"
DEFAULT_CONTRACT = "demo-contract.docx"

# 让脚本可直接运行（无需手工设 PYTHONPATH）：仓库根供 deploy.*，python-agent 供 app.*。
for extra in (ROOT / "python-agent", ROOT):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

# 伪造的 Java 侧上下文（真实环境由 TraceContextSupport 从当前 span 生成）。
PARENT_TRACE_ID = "7a1f3c9d2b8e4f60a5c7d1e9b3f80246"
PARENT_SPAN_ID = "9c2b1d4e6f8a0b3c"
TRACEPARENT = f"00-{PARENT_TRACE_ID}-{PARENT_SPAN_ID}-01"


def _serve_contracts(port: int = 0) -> tuple[ThreadingHTTPServer, str]:
    """起一个只读静态文件服务，模拟 Java 的 /contracts/{id}/content 下载通道。

    为什么要真起 HTTP：ingest 的下载+解析是真实路径（Stage 12），用 file:// 或直接注入
    clauses 会绕过它，链路里就看不到 ingest 的真实耗时与失败语义。
    """
    handler = partial(SimpleHTTPRequestHandler, directory=str(DEMO_DIR))
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


def _ensure_demo_contract() -> None:
    """演示合同缺失时现场生成（docx 是二进制，不入库；脚本可复现）。"""
    if (DEMO_DIR / DEFAULT_CONTRACT).exists():
        return
    print(f"[trace] 演示合同缺失，生成到 {DEMO_DIR / DEFAULT_CONTRACT}")
    from deploy.demo.make_demo_contract import build

    build(DEMO_DIR / DEFAULT_CONTRACT)


def _parse_sse(text: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for chunk in text.split("\n\n"):
        for line in chunk.splitlines():
            if not line.startswith("data:"):
                continue
            payload = line[len("data:") :].strip()
            if payload:
                events.append(json.loads(payload))
    return events


async def _post_run(app: Any, body: dict[str, Any]) -> list[dict[str, Any]]:
    """发一次真实运行请求并读完 SSE（ASGITransport 只支持异步客户端）。"""
    import httpx

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://python-agent", timeout=120
    ) as client:
        response = await client.post("/v1/runs", headers={"traceparent": TRACEPARENT}, json=body)
        response.raise_for_status()
        return _parse_sse(response.text)


async def _post_resume(app: Any, run_id: str) -> list[dict[str, Any]]:
    """审批后恢复：空审批数组表示「全部维持原判」，与 Stage 9 验收同一口径。"""
    import httpx

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://python-agent", timeout=120
    ) as client:
        response = await client.post(
            f"/v1/runs/{run_id}/resume", headers={"traceparent": TRACEPARENT}, json=[]
        )
        response.raise_for_status()
        return _parse_sse(response.text)


def _wait_for_spans(collector: Any, trace_id: str, timeout_s: float = 10.0) -> list[dict[str, Any]]:
    """等采集器收到该 trace 的全部 span（BatchSpanProcessor 异步批量导出）。"""
    import time

    from app import observability

    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        # 强制冲刷：调用 shutdown 让 BatchSpanProcessor 立刻导出剩余 span。
        observability.shutdown_tracing()
        spans = [span for span in collector.spans() if span["trace_id"] == trace_id]
        if spans:
            return spans
        time.sleep(0.2)
    return []


def main() -> int:
    parser = argparse.ArgumentParser(description="验证 Java→Python 的 OTel 链路")
    parser.add_argument("--contract", default=DEFAULT_CONTRACT)
    parser.add_argument("--out", default=str(ROOT / "eval" / "out" / "trace-spans.json"))
    args = parser.parse_args()

    _ensure_demo_contract()

    # 环境变量必须在 import app.config 之前就位：Settings 是进程级单例（lru_cache）。
    collector = None
    from deploy.otel.capture_collector import CaptureCollector

    collector = CaptureCollector(output_path=Path(args.out)).start()
    os.environ["OTEL_ENABLED"] = "true"
    os.environ["OTEL_EXPORTER_OTLP_TRACES_ENDPOINT"] = collector.endpoint
    os.environ["OTEL_SERVICE_NAME"] = "clauseguard-python"
    os.environ["CLAUSEGUARD_AGENT_MODEL_BACKEND"] = "mock"
    os.environ["CLAUSEGUARD_AGENT_MOCK_LATENCY_S"] = "0"
    os.environ["CLAUSEGUARD_AGENT_PG_DSN"] = ""

    # import 顺序敏感：先清缓存再导入 app，确保读到上面的环境变量。
    from app.config import get_settings
    from app.agents.settings import get_agent_settings

    get_settings.cache_clear()
    get_agent_settings.cache_clear()

    from app.main import create_app
    from app.observability import setup_tracing

    settings = get_settings()
    setup_tracing(settings)

    static_server, base_url = _serve_contracts()
    app = create_app()
    status = 1
    try:
        body = {
            "contract_id": "trace-demo",
            "version": 1,
            "mode": "full",
            "source": {
                "download_url": f"{base_url}/{args.contract}",
                "content_type": (
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                ),
                "version": 1,
                "filename": args.contract,
            },
        }
        events = asyncio.run(_post_run(app, body))
        # 演示合同含高风险条款 -> 图在 human_approval 断点挂起。恢复是同一笔操作的续跑，
        # 沿用同一 traceparent，因此链路里会多出「审批后续跑」这一段（正是演示要展示的）。
        if not any(event["type"] == "run.completed" for event in events):
            run_id = next(
                (event["run_id"] for event in events if event["type"] == "run.started"), None
            )
            if run_id is not None:
                events.extend(asyncio.run(_post_resume(app, run_id)))
        completed = [event for event in events if event["type"] == "run.completed"]
        if not completed:
            print("[trace] 运行未完成，事件尾部：" + json.dumps(events[-3:], ensure_ascii=False))
            return 1
        experts = sorted(
            {
                str(event.get("agent"))
                for event in events
                if event["type"] == "node.started"
                and str(event.get("node", "")).startswith("review.")
            }
        )
        spans = _wait_for_spans(collector, PARENT_TRACE_ID)
        status = _report(spans, experts, completed[0])
    finally:
        status_text = "PASS" if status == 0 else "FAIL"
        print(f"[trace] {status_text}；span 明细已写入 {args.out}")
        static_server.shutdown()
        collector.stop()
    return status


def _print_children(spans: list[dict[str, Any]], parent: dict[str, Any]) -> None:
    children = sorted(
        [span for span in spans if span["parent_span_id"] == parent["span_id"]],
        key=lambda s: s["start_unix_nano"],
    )
    for index, span in enumerate(children):
        token_tag = ""
        if span["attributes"].get("gen_ai.usage.input_tokens"):
            token_tag = (
                f"  tokens={span['attributes']['gen_ai.usage.input_tokens']}"
                f"/{span['attributes'].get('gen_ai.usage.output_tokens')}"
            )
        marker = "└─" if index == len(children) - 1 else "├─"
        print(f"   {marker} {span['name']}{token_tag}")


def _report(spans: list[dict[str, Any]], experts: list[str], completed: dict[str, Any]) -> int:
    if not spans:
        print("[trace] 采集器没收到任何 span：OTLP 上报链路断了")
        return 1
    # 演示合同含高风险条款，会产生两段：首次运行（在 human_approval 挂起）+ 审批后续跑。
    # 两段都以 Java 的 span 为父、共享同一 trace_id——这正是「一条 trace」的含义。
    roots = [span for span in spans if span["name"] == "run.contract-review"]
    if not roots:
        print("[trace] 没有根 span run.contract-review")
        return 1
    failures: list[str] = []
    root_ids = {root["span_id"] for root in roots}
    for root in roots:
        if root["parent_span_id"] != PARENT_SPAN_ID:
            failures.append(
                f"根 span 父 id 应为 Java 的 {PARENT_SPAN_ID}，实际 {root['parent_span_id']}"
            )
    root = roots[0]
    agents = {
        span["attributes"].get("gen_ai.agent.name"): span
        for span in spans
        if span["name"].startswith("node.review.")
    }
    missing = [expert for expert in experts if expert not in agents]
    if missing:
        failures.append(f"缺少专家 span: {missing}")
    for expert, span in agents.items():
        if span["parent_span_id"] not in root_ids:
            failures.append(f"专家 {expert} 的父不是运行根 span")
        if not span["attributes"].get("gen_ai.usage.input_tokens"):
            failures.append(f"专家 {expert} 的 span 没带 tokens")

    print("")
    print(f"trace_id = {root['trace_id']}  (Java 生成的父上下文)")
    print(f"service  = {root['service_name']}")
    print("")
    for index, item in enumerate(sorted(roots, key=lambda s: s["start_unix_nano"])):
        segment = "审批后续跑" if item["attributes"].get("clauseguard.resumed") else "首次运行"
        tokens = (
            f"  tokens={item['attributes'].get('gen_ai.usage.input_tokens')}"
            f"/{item['attributes'].get('gen_ai.usage.output_tokens')}"
            if item["attributes"].get("gen_ai.usage.input_tokens")
            else ""
        )
        print(f"└─ {item['name']}  [{item['attributes'].get('langfuse.session.id')}]"
              f"  ({segment}){tokens}")
        _print_children(spans, item)
    print("")
    print(
        f"根 span {len(roots)} 段（首次运行 + 审批后续跑），专家 span {len(agents)} 个："
        f"{sorted(agents)}"
    )
    print(
        "run.completed: findings={} degraded={} usage={}".format(
            completed.get("findings_count"), completed.get("degraded"), completed.get("usage")
        )
    )
    if failures:
        for failure in failures:
            print(f"[trace] ✗ {failure}")
        return 1
    print("[trace] ✓ 一条 trace 串起 Java 父上下文与全部专家 span，OTLP 上报成功")
    return 0


if __name__ == "__main__":
    sys.exit(main())
