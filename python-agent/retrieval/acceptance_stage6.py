"""阶段 6 可执行验收：真实法条进 citations / 降级不 500 / YAML 改完重启生效。

用法（必须显式给 DSN，防止误连 5432 上的 checkpoint 库）：
    CLAUSEGUARD_RETRIEVAL_PG_DSN='postgresql://clauseguard@127.0.0.1:55432/clauseguard_retrieval' \
        python -m retrieval.acceptance_stage6

三个场景都用真实代码路径：完整 LangGraph 审查、真实 pgvector 检索、真实 HTTP 端点；
唯一替身是 LLM 后端（mock），否则验收要依赖外部模型配额。
"""

# 验收输出即交付物（要贴进报告），CLI 打印是意图而非疏漏；全局 T20 规则在此豁免。
# ruff: noqa: T201

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
YAML = Path(__file__).resolve().parents[1] / "config" / "retrieval.yaml"
CLAUSE_TEXT = (
    "买方逾期付款的，应按日向卖方支付合同总价千分之三的违约金，"
    "并赔偿卖方因此产生的损失；双方另有约定的除外。"
)
QUERY_HINT = "违约金"

# 端口不可达 + 1s 连接超时：模拟「Python 检索后端关掉」，同时不让验收被 libpq
# 默认 20~30s 超时拖慢（生产 DSN 已带 connect_timeout=2，行为一致）。
DEAD_DSN = "postgresql://clauseguard@127.0.0.1:55999/clauseguard_retrieval?connect_timeout=1"


class _WarningCapture(logging.Handler):
    """把降级 WARN 留成可断言的数据，而不是只让它滚过控制台。"""

    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


def _reset_runtime(dsn: str) -> None:
    """切场景时把所有进程级缓存清干净，否则上一个场景的 DSN/服务实例会串味。"""
    os.environ["CLAUSEGUARD_RETRIEVAL_PG_DSN"] = dsn
    from app.tools import retrieval_backend
    from retrieval import api as retrieval_api
    from retrieval.config import get_retrieval_settings

    get_retrieval_settings.cache_clear()
    retrieval_backend._query_vector.cache_clear()
    retrieval_api.set_service(None)


def _run_full_review(dsn: str, contract_id: str) -> tuple[int, list[dict[str, Any]], list[str]]:
    _reset_runtime(dsn)
    from fastapi.testclient import TestClient

    import app.main as main_module
    from app.graph.state import Clause

    clause = Clause(
        clause_id="C-585",
        index=0,
        text=CLAUSE_TEXT,
        risk_level="unknown",
        no="第一条",
        hash="",
    )
    original = main_module._make_initial_state

    def injected(*args: Any, **kwargs: Any) -> Any:
        state = original(*args, **kwargs)
        # 直接注入条款：ingest 的路径 1（已带 clauses）是既有测试与断点恢复使用的
        # 正规入口，避免为了验收再去搭一个 MinIO/Java 文件下载服务。
        state["clauses"] = [clause]
        return state

    main_module._make_initial_state = injected
    capture = _WarningCapture()
    backend_logger = logging.getLogger("app.tools.retrieval_backend")
    backend_logger.addHandler(capture)
    try:
        client = TestClient(main_module.create_app())
        response = client.post("/v1/runs", json={"contract_id": contract_id})
        events = [
            json.loads(line[len("data:") :].strip())
            for line in response.text.splitlines()
            if line.startswith("data:")
        ]
        return response.status_code, events, capture.messages
    finally:
        backend_logger.removeHandler(capture)
        main_module._make_initial_state = original


def _completed(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    return next((e for e in events if e.get("type") == "run.completed"), None)


def _check_live_review(dsn: str) -> dict[str, Any]:
    status, events, _ = _run_full_review(dsn, "stage6-live")
    completed = _completed(events)
    assert status == 200, f"审查 HTTP {status}"
    assert completed is not None, f"未下发 run.completed，事件={[e.get('type') for e in events]}"
    findings = completed.get("findings") or []
    citations = [c for f in findings for c in (f.get("citations") or [])]
    source_ids = [c["source_id"] for c in citations]
    titles = [c.get("title") or "" for c in citations]
    fake = [sid for sid in source_ids if sid.startswith(("sim-", "law-", "policy-"))]
    real = [sid for sid in source_ids if sid.startswith("civil_code:")]
    assert findings, "完整审查没有产出 finding"
    assert real, f"citations 未出现真实 chunk_id: {source_ids}"
    assert not fake, f"citations 仍含假 ID: {fake}"
    assert any("民法典" in t for t in titles), f"引用标题没有真实法名: {titles}"
    assert any("第五百八十五条" in t for t in titles), f"未见民法典第585条: {titles}"
    return {
        "status": status,
        "findings": len(findings),
        "citations": len(citations),
        "sample_source_ids": source_ids[:3],
        "sample_titles": titles[:3],
        "unverified_count": completed.get("quality", {}).get("unverified_count"),
        "degraded": completed.get("degraded"),
    }


def _check_degrade(dsn: str) -> dict[str, Any]:
    status, events, warnings = _run_full_review(DEAD_DSN, "stage6-degraded")
    completed = _completed(events)
    assert status == 200, f"检索后端挂掉时审查不应 500，实际 {status}"
    assert completed is not None, f"未下发 run.completed: {[e.get('type') for e in events]}"
    findings = completed.get("findings") or []
    assert findings, "降级路径不应吞掉 finding（ADR-0003：保留并标 unverified）"
    assert all(not (f.get("citations") or []) for f in findings), "降级时应为空引用"
    assert all(f["risk_level"] == "unverified" for f in findings), "降级时应标 unverified"
    assert completed.get("quality", {}).get("unverified_count", 0) > 0
    assert any("unavailable" in w for w in warnings), f"缺少 WARN 留痕: {warnings}"
    _reset_runtime(dsn)
    return {
        "status": status,
        "findings": len(findings),
        "citations": sum(len(f.get("citations") or []) for f in findings),
        "risk_levels": sorted({f["risk_level"] for f in findings}),
        "unverified_count": completed.get("quality", {}).get("unverified_count"),
        "warn_sample": warnings[0][:120] if warnings else "",
    }


def _probe(dsn: str) -> dict[str, Any]:
    env = os.environ.copy()
    env.update(
        {
            "CLAUSEGUARD_RETRIEVAL_MODE": "live",
            "CLAUSEGUARD_RETRIEVAL_PG_DSN": dsn,
        }
    )
    proc = subprocess.run(
        [sys.executable, "-m", "retrieval.stage6_probe"],
        cwd=str(Path(__file__).resolve().parents[1]),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"probe 失败 rc={proc.returncode}: {proc.stdout}\n{proc.stderr[-1500:]}")
    return json.loads(proc.stdout.strip().splitlines()[-1])


def _check_config_reload(dsn: str) -> dict[str, Any]:
    original = YAML.read_text(encoding="utf-8")
    before = _probe(dsn)
    changed = original.replace("top_k: 5", "top_k: 2", 1)
    assert changed != original, "YAML 里找不到 top_k: 5，验收脚本需要同步更新"
    YAML.write_text(changed, encoding="utf-8")
    try:
        after = _probe(dsn)
    finally:
        YAML.write_text(original, encoding="utf-8")
    assert before["yaml_top_k"] == 5 and before["effective_top_k"] == 5
    assert after["yaml_top_k"] == 2 and after["effective_top_k"] == 2
    assert before["hits"] == 5 and after["hits"] == 2, (before, after)
    assert before["strategy"] == after["strategy"], "top_k 不应影响 strategy"
    assert before["top1"] == after["top1"], "top_k 只截断条数，不应改变排序"
    restored = _probe(dsn)
    assert restored["effective_top_k"] == 5, "YAML 未恢复"
    return {"before": before, "after": after, "restored": restored}


def main() -> int:
    dsn = os.environ.get("CLAUSEGUARD_RETRIEVAL_PG_DSN", "")
    if not dsn or ":5432" in dsn:
        print("FATAL: 必须显式设置 CLAUSEGUARD_RETRIEVAL_PG_DSN，且不能指向 5432")
        return 2
    # 模型后端固定 mock：验收的是检索链路与白名单，不该消耗外部 LLM 配额。
    os.environ["CLAUSEGUARD_AGENT_MODEL_BACKEND"] = "mock"
    os.environ["CLAUSEGUARD_RETRIEVAL_MODE"] = "live"

    checks: list[tuple[str, dict[str, Any]]] = []
    for name, fn in (
        ("[1] 完整审查 citations 为真实法条", lambda: _check_live_review(dsn)),
        ("[2] Python 检索后端关停仍完成审查", lambda: _check_degrade(dsn)),
        ("[3] retrieval.yaml 改完重启生效", lambda: _check_config_reload(dsn)),
    ):
        try:
            result = fn()
        except Exception as exc:  # 验收要把每个失败都摊开，不要在第一项就中断
            print(f"{name} ... FAIL: {type(exc).__name__}: {exc}")
            checks.append((name, {"error": str(exc)}))
            continue
        print(f"{name} ... PASS")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        checks.append((name, result))

    passed = sum(1 for _, r in checks if "error" not in r)
    print(f"\n===== 阶段 6 验收：{passed}/{len(checks)} PASS =====")
    return 0 if passed == len(checks) else 1


if __name__ == "__main__":
    sys.exit(main())
