# ruff: noqa: T201
# 本脚本的用途就是「贴四步真实输出」到验收报告，裸 print 是交付物而非调试残留；
# 项目其它 CLI 脚本（eval/、deploy/）同样用 print，均不在 lint 门禁内。
"""阶段 4 验收脚本（可独立运行，贴四步真实输出）。

用法：cd python-agent && .venv/Scripts/python -m tests.thread_scope.acceptance_resume

四步：
  1. 创建：POST /v1/runs（高风险语料）→ run.started
  2. interrupt：human_approval 挂起 → interrupt 事件，断点落在 run:{run_id}
  3. 审批 resume：POST /v1/runs/{run_id}/resume → run.completed
  4. 重启 python-agent 后再 resume 一次：新 app 实例复用同一 checkpointer/registry，
     证明仍能定位同一 thread（thread 由 run_id 派生，不依赖 contract_id/version）。

禁止项守卫：不清 checkpoint、不在 run 前清空 findings、不改事件契约。
"""

from __future__ import annotations

from typing import Any

from app.graph.threads import review_thread_id
from tests.thread_scope.harness import HIGH_RISK_DOCX_INDEX, ThreadScopeHarness, run


def _types(events: list[dict[str, Any]]) -> list[str]:
    return [str(e.get("type")) for e in events]


def _seq_range(events: list[dict[str, Any]]) -> tuple[int, int]:
    seqs = [int(e["seq"]) for e in events]
    return (min(seqs), max(seqs)) if seqs else (0, 0)


async def main() -> None:
    h = ThreadScopeHarness()
    cid, version = "c-resume-e2e", 1

    # ---- 步骤 1+2：创建 → interrupt ----
    first = await h.run_once(cid, version, docx_index=HIGH_RISK_DOCX_INDEX)
    assert first.completed is False and first.interrupted, "必须挂起等待人工审批"
    expected_thread = review_thread_id(first.run_id)
    pause_lo, pause_hi = _seq_range(first.events)
    print(f"[1] 创建       : POST /v1/runs -> run_id={first.run_id} thread={expected_thread}")
    print(f"                事件={_types(first.events)[:3]}... seq={pause_lo}..{pause_hi}")
    print(
        f"[2] interrupt  : type=interrupt | completed=False "
        f"| threads={sorted(h.checkpoint_threads())}"
    )
    assert h.checkpoint_threads() == {expected_thread}, "断点必须落在 run:{run_id}"

    # ---- 步骤 3：审批 resume → completed ----
    resumed = await h.resume(first.run_id, approvals=[])
    assert resumed.completed, f"resume 未完成：{_types(resumed.events)}"
    res_lo, res_hi = _seq_range(resumed.events)
    print(
        f"[3] 审批 resume: POST /v1/runs/{first.run_id}/resume -> run.completed "
        f"| findings={len(resumed.findings)} unverified={len(resumed.unverified)}"
    )
    print(
        f"                seq={res_lo}..{res_hi} (pause_max={pause_hi}, 续接={res_lo > pause_hi})"
    )
    assert res_lo > pause_hi, "resume 事件序号必须续接而非重置"
    assert len(resumed.findings) > 0, "断点恢复后 findings 必须仍在（没从头重跑）"

    # ---- 步骤 4：重启 python-agent 后再 resume 一次 ----
    h.restart()
    after_restart_threads = h.checkpoint_threads()
    assert expected_thread in after_restart_threads, "重启后同一 thread 的 checkpoint 必须仍在"
    again = await h.resume(first.run_id, approvals=[])
    ag_lo, ag_hi = _seq_range(again.events)
    print(
        f"[4] 重启后 resume: 新 app 实例 | threads={sorted(after_restart_threads)} "
        f"-> run.completed={again.completed} findings={len(again.findings)}"
    )
    print(f"                 seq={ag_lo}..{ag_hi} | 仍定位同一 thread={expected_thread}")
    assert again.completed, "重启后续跑必须仍能定位同一 thread 并完成"
    assert len(again.findings) == len(resumed.findings), "重启不应改变 findings 数量"
    print("四步全部通过：审批续跑、seq 续接、重启后 thread 定位均不退化。")


if __name__ == "__main__":
    run(main())
