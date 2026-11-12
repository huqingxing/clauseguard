"""阶段 4 固化回归：审批续跑 + 重启后仍能定位同一 thread。

两条路径都要过（用户禁令「未验证续跑不得宣布完成」）：
- 完整四步：创建 → interrupt → 审批 resume → completed；再重启后 resume 一次。
- 更硬的路径：**重启发生在 interrupt 与 resume 之间**（审批可能隔夜/跨发布），
  重启后的新 app 实例必须仍能定位同一 thread 并把断点续跑到 completed。
"""

from __future__ import annotations

from typing import Any

from app.graph.threads import review_thread_id
from tests.thread_scope.harness import HIGH_RISK_DOCX_INDEX, ThreadScopeHarness, run


def _seqs(events: list[dict[str, Any]]) -> list[int]:
    return [int(e["seq"]) for e in events]


def _has(events: list[dict[str, Any]], kind: str) -> bool:
    return any(e.get("type") == kind for e in events)


def test_four_step_create_interrupt_resume_completed_then_restart() -> None:
    async def scenario() -> dict[str, Any]:
        h = ThreadScopeHarness()
        first = await h.run_once("c-four-step", 1, docx_index=HIGH_RISK_DOCX_INDEX)
        thread = review_thread_id(first.run_id)
        resumed = await h.resume(first.run_id, approvals=[])
        h.restart()
        again = await h.resume(first.run_id, approvals=[])
        return {
            "first": first,
            "resumed": resumed,
            "again": again,
            "thread": thread,
            "threads": h.checkpoint_threads(),
        }

    out = run(scenario())
    first, resumed, again = out["first"], out["resumed"], out["again"]
    assert first.interrupted and not first.completed, "步骤 1-2：必须停在 interrupt"
    assert out["thread"] in out["threads"], "断点必须落在 run:{run_id}"
    assert _has(resumed.events, "run.completed"), "步骤 3：审批 resume 必须完成"
    assert resumed.findings and len(resumed.unverified) == 0
    assert min(_seqs(resumed.events)) > max(_seqs(first.events)), "seq 必须续接不重置"
    assert _has(again.events, "run.completed"), "步骤 4：重启后 resume 仍须完成"
    assert len(again.findings) == len(resumed.findings), "重启不得改变 findings"
    assert out["thread"] in out["threads"], "重启后同一 thread 的 checkpoint 必须仍在"


def test_restart_between_interrupt_and_resume_still_completes() -> None:
    """最硬的一条：审批期间 python-agent 重启，续跑仍定位同一 thread。"""

    async def scenario() -> dict[str, Any]:
        h = ThreadScopeHarness()
        first = await h.run_once("c-restart-mid", 1, docx_index=HIGH_RISK_DOCX_INDEX)
        paused_thread = review_thread_id(first.run_id)
        h.restart()  # 审批未完成时重启：新 app、同一持久化层
        resumed = await h.resume(first.run_id, approvals=[])
        return {
            "first": first,
            "resumed": resumed,
            "paused_thread": paused_thread,
            "threads": h.checkpoint_threads(),
        }

    out = run(scenario())
    first, resumed = out["first"], out["resumed"]
    assert first.interrupted, "重启前必须已挂起"
    assert out["paused_thread"] in out["threads"], "重启后断点 thread 必须仍在"
    assert _has(resumed.events, "run.completed"), "重启后 resume 必须跑完（不能空跑/报错）"
    assert len(resumed.findings) > 0, "断点恢复后 findings 必须仍在本 thread 里"
    assert min(_seqs(resumed.events)) > max(_seqs(first.events)), "重启后 seq 仍须续接"


def test_legacy_registry_record_is_rejected_with_409_not_500() -> None:
    """ADR-0006「部署时排空」：旧格式注册表记录 resume 必须明确 409，
    不读旧 checkpoint、不跑空图、不报 500。"""

    async def scenario() -> tuple[int, str, set[str]]:
        h = ThreadScopeHarness()
        # 造一份旧格式 run_registry 记录（模拟升级前挂起的审查），thread 值仍是
        # contract:version 形式；不造任何 checkpoint，验证连空图都不会跑。
        await h.registry.put(
            "run-legacy",
            {
                "thread_id": "c-legacy:1",
                "contract_id": "c-legacy",
                "version": 1,
                "content_hash": "hash-v1",
                "trace_id": "trace-legacy",
                "event_seq": 12,
            },
        )
        status, body = await h.resume_raw("run-legacy", approvals=[])
        return status, body, h.checkpoint_threads()

    status, body, threads = run(scenario())
    assert status == 409, f"旧格式记录应明确 409 而非 {status}: {body[:200]}"
    assert "LEGACY_THREAD_SCOPE" in body, body[:200]
    assert threads == set(), "不得读取/写入任何旧 checkpoint（不自动迁移、不跑空图）"
