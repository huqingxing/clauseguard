"""Locust 压测脚本：20 并发 / 5 分钟，量编排层的 P50/P95/P99、错误率、平均成本。

为什么压编排层（Python /v1/runs）：成本与延迟的大头在六个 Agent 的模型调用与工具检索上，
这一段才是需要横向扩容的部分；Java 网关只是转发与落库。`LOCUST_TARGET=java` 可切成
「整链路」模式（上传合同 → 建审查 → 消费 SSE），需要 MySQL/Redis 与两栈都起。

为什么要自己起静态文件服务：ingest 是真实下载+解析路径（Stage 12），source.download_url
必须可达；把演示合同挂在本机静态服务上，压测脚本就能一条命令自包含运行，不依赖 Java。

指标口径：
- 响应时间 = 从 POST /v1/runs 到收到 run.completed（含下载、切分、6 专家、critic、回写占位）；
- 错误 = HTTP 非 200、SSE 流里出现 run.failed、或直到流结束都没收到 run.completed；
- 成本 = run.completed.usage.cost_cny 累加；token 同理，按 run 求平均。
汇总写到 $LOCUST_SUMMARY（默认 eval/out/bench-tokens.json），report 脚本据此出表。

用法（仓库根，先起 Python 服务并开 mock 后端；或直接用 make bench 一条命令）：
    python-agent/.venv/Scripts/python.exe -m locust -f deploy/locust/locustfile.py \
        --headless -u 20 -r 4 -t 5m --host http://127.0.0.1:8000 \
        --csv eval/out/bench --only-summary
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from locust import HttpUser, constant, events, task

ROOT = Path(__file__).resolve().parents[2]
DEMO_DIR = ROOT / "deploy" / "demo"
DEMO_CONTRACT = "demo-contract.docx"
SUMMARY_PATH = Path(os.environ.get("LOCUST_SUMMARY", str(ROOT / "eval" / "out" / "bench-tokens.json")))
TARGET = os.environ.get("LOCUST_TARGET", "python").lower()

_LOCK = threading.Lock()
_TOTALS: dict[str, float] = {
    "runs": 0,
    "completed": 0,
    "failed": 0,
    "input_tokens": 0,
    "output_tokens": 0,
    "cost_cny": 0.0,
    "duration_ms": 0,
}


def _record(**delta: float) -> None:
    with _LOCK:
        for key, value in delta.items():
            _TOTALS[key] = _TOTALS.get(key, 0) + value


def _start_static_server() -> str:
    """把演示合同目录挂成静态服务，供 ingest 真实下载。"""
    handler = partial(SimpleHTTPRequestHandler, directory=str(DEMO_DIR))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    _SERVERS.append(server)
    return f"http://127.0.0.1:{server.server_address[1]}"


_SERVERS: list[ThreadingHTTPServer] = []
_STATIC_BASE = ""
# 压测真实墙钟起点：_TOTALS["duration_ms"] 是「219 次旅程各自耗时的累加」，
# 拿它当挂钟时长会得出 5694s 这种既不是 5 分钟也不是单次耗时的数字。
_TEST_STARTED_AT = 0.0


@events.test_start.add_listener
def _on_start(environment: Any, **_kwargs: Any) -> None:
    global _STATIC_BASE, _TEST_STARTED_AT
    _TEST_STARTED_AT = time.perf_counter()
    if not (DEMO_DIR / DEMO_CONTRACT).exists():
        from deploy.demo.make_demo_contract import build

        build(DEMO_DIR / DEMO_CONTRACT)
    _STATIC_BASE = _start_static_server()


@events.test_stop.add_listener
def _on_stop(environment: Any, **_kwargs: Any) -> None:
    stats = environment.stats.total
    # 分端点统计：run 与 resume 是两个量级的耗时，混在一起算 p50 会得出「15s」这种
    # 既不代表审查也不代表审批的数字。这里逐端点给出，汇总行只用主端点。
    by_endpoint: dict[str, dict[str, float]] = {}
    for entry in environment.stats.entries.values():
        entry_name = str(getattr(entry, "name", ""))
        if not entry.num_requests:
            continue
        by_endpoint[entry_name] = {
            "requests": int(entry.num_requests),
            "failures": int(entry.num_failures),
            "p50": int(entry.get_response_time_percentile(0.50) or 0),
            "p95": int(entry.get_response_time_percentile(0.95) or 0),
            "p99": int(entry.get_response_time_percentile(0.99) or 0),
            "avg": int(entry.avg_response_time or 0),
            "max": int(entry.max_response_time or 0),
        }
    primary = by_endpoint.get("POST /v1/runs (SSE)") or by_endpoint.get(
        "POST /api/v1/reviews", {}
    )
    payload = {
        "target": TARGET,
        "users": environment.runner.target_user_count if environment.runner else None,
        "runs": int(_TOTALS["runs"]),
        "completed": int(_TOTALS["completed"]),
        "failed": int(_TOTALS["failed"]),
        "error_rate": round(_TOTALS["failed"] / _TOTALS["runs"], 4) if _TOTALS["runs"] else 0.0,
        # 主端点（提交审查）的 P 值才是「用户等多久」；resume 单列在 by_endpoint 里。
        "latency_ms": {
            "p50": primary.get("p50", 0),
            "p95": primary.get("p95", 0),
            "p99": primary.get("p99", 0),
            "avg": primary.get("avg", 0),
            "max": primary.get("max", 0),
        },
        "by_endpoint": by_endpoint,
        "requests": int(stats.num_requests),
        "tokens": {
            "input_total": int(_TOTALS["input_tokens"]),
            "output_total": int(_TOTALS["output_tokens"]),
            "input_per_run": round(_TOTALS["input_tokens"] / _TOTALS["completed"], 1)
            if _TOTALS["completed"]
            else 0,
            "output_per_run": round(_TOTALS["output_tokens"] / _TOTALS["completed"], 1)
            if _TOTALS["completed"]
            else 0,
        },
        "cost_cny": {
            "total": round(_TOTALS["cost_cny"], 4),
            "per_run": round(_TOTALS["cost_cny"] / _TOTALS["completed"], 6)
            if _TOTALS["completed"]
            else 0,
        },
        # 墙钟时长（-t 5m 就是 300s 左右）；journey_s_total 是所有旅程耗时之和，不是时长。
        "wall_clock_s": round(time.perf_counter() - _TEST_STARTED_AT, 1)
        if _TEST_STARTED_AT
        else 0,
        "journey_s_total": round(_TOTALS["duration_ms"] / 1000, 1),
    }
    SUMMARY_PATH.parent.mkdir(parents=True, exist_ok=True)
    SUMMARY_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[bench] 汇总已写入 {SUMMARY_PATH}")


def _parse_events(text: str) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """SSE 文本 → (run.completed 事件或 None, 全部事件)。"""
    completed: dict[str, Any] | None = None
    events_list: list[dict[str, Any]] = []
    for chunk in text.split("\n\n"):
        for line in chunk.splitlines():
            if not line.startswith("data:"):
                continue
            raw = line[len("data:") :].strip()
            if not raw:
                continue
            try:
                event = json.loads(raw)
            except json.JSONDecodeError:
                continue
            events_list.append(event)
            if event.get("type") == "run.completed":
                completed = event
    return completed, events_list


class ReviewUser(HttpUser):
    """模拟一个前端会话：不断提交新合同审查并读完事件流。"""

    # 不加思考时间：压测要的是系统上限，而不是「用户喝咖啡」的吞吐。
    wait_time = constant(float(os.environ.get("LOCUST_WAIT_S", "0")))
    timeout = 180.0

    def _python_body(self) -> dict[str, Any]:
        return {
            # 每次新合同：thread_id = contract:version，复用会命中旧 checkpoint，
            # 测出来的就不是「新合同冷启动」的时延了。
            "contract_id": f"bench-{uuid.uuid4().hex[:12]}",
            "version": 1,
            "mode": "full",
            "source": {
                "download_url": f"{_STATIC_BASE}/{DEMO_CONTRACT}",
                "content_type": (
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                ),
                "version": 1,
                "filename": DEMO_CONTRACT,
            },
        }

    @task
    def run_python(self) -> None:
        """一次完整用户旅程：提交审查 → （必要时）审批恢复 → 收到 run.completed。

        为什么把审批恢复算进同一次旅程：演示合同里有高风险条款，图必然在 human_approval
        挂起（这是真实业务路径，不是异常）。把恢复也压进来，测到的才是「用户从提交到拿到
        报告」的完整体验；两段分别作为两个请求记录，能看出审批往返本身的代价。
        """
        started = time.perf_counter()
        headers = {"traceparent": f"00-{uuid.uuid4().hex}-{uuid.uuid4().hex[:16]}-01"}
        _record(runs=1)
        with self.client.post(
            "/v1/runs",
            json=self._python_body(),
            headers=headers,
            catch_response=True,
            timeout=self.timeout,
            name="POST /v1/runs (SSE)",
        ) as response:
            if response.status_code != 200:
                _record(failed=1)
                response.failure(f"http {response.status_code}: {response.text[:120]}")
                return
            completed, stream = _parse_events(response.text)
            if any(event.get("type") == "run.failed" for event in stream):
                _record(failed=1)
                response.failure("stream contains run.failed")
                return
            if completed is not None:
                self._record_usage(completed, started)
                response.success()
                return
            run_id = next(
                (event.get("run_id") for event in stream if event.get("type") == "run.started"),
                None,
            )
            if run_id is None:
                _record(failed=1)
                kinds = sorted({str(event.get("type")) for event in stream})
                response.failure(f"no run_id and no run.completed: {kinds}")
                return
            response.success()

        with self.client.post(
            f"/v1/runs/{run_id}/resume",
            json=[],
            headers=headers,
            catch_response=True,
            timeout=self.timeout,
            name="POST /v1/runs/{id}/resume (SSE)",
        ) as resumed:
            if resumed.status_code != 200:
                _record(failed=1)
                resumed.failure(f"resume http {resumed.status_code}: {resumed.text[:120]}")
                return
            completed, _ = _parse_events(resumed.text)
            if completed is None:
                _record(failed=1)
                resumed.failure("resume stream ended without run.completed")
                return
            self._record_usage(completed, started)
            resumed.success()

    @staticmethod
    def _record_usage(completed: dict[str, Any], started: float) -> None:
        usage = completed.get("usage") or {}
        _record(
            completed=1,
            input_tokens=float(usage.get("input_tokens") or 0),
            output_tokens=float(usage.get("output_tokens") or 0),
            cost_cny=float(usage.get("cost_cny") or 0),
            duration_ms=(time.perf_counter() - started) * 1000,
        )

    @task
    def run_java(self) -> None:
        """整链路模式：Java 上传 → 建审查 → 读 SSE（需 MySQL/Redis 与两栈都起）。

        与 Python 模式同一指标口径：从建审查到 run.completed 的墙钟时间。
        """
        contract_id = None
        with open(DEMO_DIR / DEMO_CONTRACT, "rb") as handle:
            with self.client.post(
                "/api/v1/contracts",
                files={
                    "file": (
                        DEMO_CONTRACT,
                        handle,
                        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    )
                },
                catch_response=True,
                name="POST /api/v1/contracts",
            ) as upload:
                if upload.status_code not in (200, 201):
                    upload.failure(f"upload http {upload.status_code}: {upload.text[:120]}")
                    _record(runs=1, failed=1)
                    return
                contract_id = (upload.json() or {}).get("contractId")
                upload.success()
        if not contract_id:
            _record(runs=1, failed=1)
            return

        started = time.perf_counter()
        with self.client.post(
            "/api/v1/reviews",
            json={"contractId": contract_id, "mode": "full"},
            catch_response=True,
            name="POST /api/v1/reviews",
        ) as created:
            if created.status_code not in (200, 201, 202):
                created.failure(f"create http {created.status_code}: {created.text[:120]}")
                _record(runs=1, failed=1)
                return
            stream_url = (created.json() or {}).get("streamUrl")
            created.success()
        if not stream_url:
            _record(runs=1, failed=1)
            return

        with self.client.get(
            stream_url, catch_response=True, timeout=self.timeout, name="GET /reviews/{id}/stream (SSE)"
        ) as streamed:
            duration_ms = (time.perf_counter() - started) * 1000
            _record(runs=1, duration_ms=duration_ms)
            completed, _ = _parse_events(streamed.text)
            if completed is None:
                _record(failed=1)
                streamed.failure("stream ended without run.completed")
                return
            usage = completed.get("usage") or {}
            _record(
                completed=1,
                input_tokens=float(usage.get("input_tokens") or 0),
                output_tokens=float(usage.get("output_tokens") or 0),
                cost_cny=float(usage.get("cost_cny") or 0),
            )
            streamed.success()


if TARGET != "java":
    # Python 模式只留一个任务（默认路径）；java 模式只跑整链路任务。
    ReviewUser.tasks = [ReviewUser.run_python]  # type: ignore[attr-defined]
else:
    ReviewUser.tasks = [ReviewUser.run_java]  # type: ignore[attr-defined]
