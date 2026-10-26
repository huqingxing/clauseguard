"""单并发基线：不走 Locust，串行跑 N 次「提交 →（必要时）审批恢复」，量单跑真实耗时。

为什么要它：Locust 的 P 值混了 20 个并发会话在一台机器上抢 CPU 的排队时间，
不能直接说「一次审查要 25 秒」。压测报告需要一条「1 并发基线」做对照，
才能把「业务本身多慢」和「并发排队多慢」分开讲。

用法（Python 服务已在 8000 跑 mock 后端）：
    python-agent/.venv/Scripts/python.exe deploy/locust/single_run.py --times 3
"""

from __future__ import annotations

import argparse
import json
import statistics
import threading
import time
import uuid
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
DEMO_DIR = ROOT / "deploy" / "demo"
DEMO_CONTRACT = "demo-contract.docx"


def _start_static_server() -> str:
    handler = partial(SimpleHTTPRequestHandler, directory=str(DEMO_DIR))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{server.server_address[1]}"


def _parse_events(text: str) -> tuple[dict | None, list[dict]]:
    completed: dict | None = None
    parsed: list[dict] = []
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
            parsed.append(event)
            if event.get("type") == "run.completed":
                completed = event
    return completed, parsed


def one_run(base: str, static_base: str) -> dict:
    body = {
        "contract_id": f"single-{uuid.uuid4().hex[:12]}",
        "version": 1,
        "mode": "full",
        "source": {
            "download_url": f"{static_base}/{DEMO_CONTRACT}",
            "content_type": (
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            ),
            "version": 1,
            "filename": DEMO_CONTRACT,
        },
    }
    headers = {"traceparent": f"00-{uuid.uuid4().hex}-{uuid.uuid4().hex[:16]}-01"}
    started = time.perf_counter()
    with httpx.Client(timeout=180.0) as client:
        first = client.post(f"{base}/v1/runs", json=body, headers=headers)
        first.raise_for_status()
        completed, stream = _parse_events(first.text)
        first_s = time.perf_counter() - started
        resume_s = 0.0
        interrupted = completed is None
        if completed is None:
            run_id = next(
                (e.get("run_id") for e in stream if e.get("type") == "run.started"), None
            )
            if run_id is None:
                raise RuntimeError(f"no run_id: {[e.get('type') for e in stream][:6]}")
            mark = time.perf_counter()
            second = client.post(f"{base}/v1/runs/{run_id}/resume", json=[], headers=headers)
            second.raise_for_status()
            resume_s = time.perf_counter() - mark
            completed, _ = _parse_events(second.text)
    if completed is None:
        raise RuntimeError("stream ended without run.completed")
    usage = completed.get("usage") or {}
    findings = completed.get("findings") or []
    high = [f for f in findings if f.get("risk_level") == "high"]
    clauses = {
        e.get("clause_id") for e in stream if e.get("type", "").startswith("node.")
    }
    return {
        "total_s": round(time.perf_counter() - started, 2),
        "submit_s": round(first_s, 2),
        "resume_s": round(resume_s, 2),
        "interrupted": interrupted,
        "findings": len(findings),
        "high_risk": len(high),
        "clause_ids_seen": len(clauses),
        "input_tokens": usage.get("input_tokens"),
        "output_tokens": usage.get("output_tokens"),
        "cost_cny": usage.get("cost_cny"),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8000")
    parser.add_argument("--times", type=int, default=3)
    args = parser.parse_args()

    static_base = _start_static_server()
    totals: list[float] = []
    for index in range(args.times):
        result = one_run(args.base, static_base)
        totals.append(result["total_s"])
        print(f"[single] #{index + 1} {result}")
    print(
        f"[single] n={len(totals)} total_s: "
        f"p50={statistics.median(totals):.2f} min={min(totals):.2f} max={max(totals):.2f}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
