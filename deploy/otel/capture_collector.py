"""本地 OTLP/HTTP 采集器：把 span 收下来落成 JSON，用于验证「上报链路真的通」。

为什么需要它：Langfuse 是外部 SaaS，验收/CI 里不能依赖公网与账号。本采集器监听同一个
OTLP/HTTP 协议（POST /v1/traces，protobuf body），把收到的 ExportTraceServiceRequest
解成扁平 JSON。`OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` 指向这里还是 Langfuse，双栈代码
一行不用改——这也正是「换后端只改环境变量」的证明。

不做的部分：不做采样、不做存储、不做 UI。它只是链路探针，不是可观测性后端。
"""

from __future__ import annotations

import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest

logger = logging.getLogger("deploy.otel.capture")

# 采集到的 span 统一用这套键名（与 OTLP 中的 bytes id 对齐成 hex，便于人读与断言）。
SPAN_KEYS = (
    "trace_id",
    "span_id",
    "parent_span_id",
    "name",
    "kind",
    "start_unix_nano",
    "end_unix_nano",
    "attributes",
    "status_code",
    "service_name",
)


class _Handler(BaseHTTPRequestHandler):
    collector: CaptureCollector

    def do_POST(self) -> None:  # noqa: N802 (http.server 的命名约定)
        if not self.path.startswith("/v1/traces"):
            self.send_error(404, "only /v1/traces is implemented")
            return
        length = int(self.headers.get("Content-Length") or 0)
        payload = self.rfile.read(length)
        try:
            request = ExportTraceServiceRequest()
            request.ParseFromString(payload)
            self.collector.ingest(request)
        except Exception as exc:  # 解析失败也让 exporter 拿到 200，避免其无限重试刷日志
            logger.warning("failed to decode OTLP payload: %s", exc)
            self.send_error(400, "bad otlp payload")
            return
        body = b""  # ExportTraceServiceResponse 各字段均为默认值，空 body 即合法响应
        self.send_response(200)
        self.send_header("Content-Type", "application/x-protobuf")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        # 供调试：GET /spans 直接看已采集结果。
        if self.path.startswith("/spans"):
            body = json.dumps(self.collector.spans(), ensure_ascii=False, indent=2).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_error(404, "not found")

    def log_message(self, fmt: str, *args: Any) -> None:
        # 默认实现会往 stderr 狂打访问日志，验证脚本的输出会被噪声淹没。
        logger.debug("otlp collector: " + fmt, *args)


class CaptureCollector:
    """OTLP/HTTP 采集器：起在后台线程，主线程可查询已收到的 span。"""

    def __init__(self, port: int = 0, output_path: Path | None = None) -> None:
        # port=0 让系统分配空闲端口：并行跑多个验证脚本时不抢端口。
        handler = type("_BoundHandler", (_Handler,), {"collector": self})
        self._server = ThreadingHTTPServer(("127.0.0.1", port), handler)
        self._thread: threading.Thread | None = None
        self._spans: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self.output_path = output_path

    @property
    def port(self) -> int:
        return int(self._server.server_address[1])

    @property
    def endpoint(self) -> str:
        return f"http://127.0.0.1:{self.port}/v1/traces"

    def start(self) -> CaptureCollector:
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        logger.info("otlp capture collector listening on %s", self.endpoint)
        return self

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)
        if self.output_path is not None:
            self.output_path.write_text(
                json.dumps(self.spans(), ensure_ascii=False, indent=2), encoding="utf-8"
            )

    def spans(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._spans)

    def ingest(self, request: ExportTraceServiceRequest) -> None:
        batch: list[dict[str, Any]] = []
        for resource_spans in request.resource_spans:
            service_name = _service_name(resource_spans.resource.attributes)
            for scope_spans in resource_spans.scope_spans:
                for span in scope_spans.spans:
                    batch.append(
                        {
                            "trace_id": span.trace_id.hex(),
                            "span_id": span.span_id.hex(),
                            "parent_span_id": span.parent_span_id.hex() or None,
                            "name": span.name,
                            "kind": int(span.kind),
                            "start_unix_nano": int(span.start_time_unix_nano),
                            "end_unix_nano": int(span.end_time_unix_nano),
                            "status_code": int(span.status.code),
                            "service_name": service_name,
                            "attributes": _attributes(span.attributes),
                        }
                    )
        with self._lock:
            self._spans.extend(batch)
        logger.debug("received %d spans (total=%d)", len(batch), len(self._spans))


def _attributes(items: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for item in items:
        value = item.value
        field = value.WhichOneof("value")
        if field is None:
            out[item.key] = None
        elif field == "array_value":
            out[item.key] = [_single(v) for v in value.array_value.values]
        else:
            out[item.key] = _single(value)
    return out


def _single(value: Any) -> Any:
    field = value.WhichOneof("value")
    if field is None:
        return None
    if field == "int_value":
        return int(value.int_value)
    if field == "double_value":
        return float(value.double_value)
    if field == "bool_value":
        return bool(value.bool_value)
    if field == "bytes_value":
        return value.bytes_value.hex()
    return getattr(value, field)


def _service_name(attributes: Any) -> str:
    for item in attributes:
        if item.key == "service.name":
            return str(item.value.string_value)
    return ""


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    collector = CaptureCollector(port=4318).start()
    logger.info("endpoint=%s  GET /spans 查看已采集结果，Ctrl+C 退出", collector.endpoint)
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        collector.stop()
