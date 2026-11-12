"""LangGraph thread 作用域的唯一构造入口（ADR-0006）。

## 为什么需要这个模块

修复前 `thread_id = f"{contract_id}:{version}"`：同一合同同一版本的**两笔审查**
共用一份 checkpoint，第二笔从第一笔的终态续跑，`merge_findings` 把旧 finding 与新
finding 合并（实测 9 → 18，其中 9 条因条款已不存在被 Critic 降级为 unverified）。
根本原因是把「合同版本」误当成了「会话」。

## 决策

**一次审查 = 一个 thread**，thread 由该次审查的 `run_id` 唯一派生：

    review_thread_id(run_id) -> f"run:{run_id}"

`run_id` 是 Python 在 `POST /v1/runs` 时生成的跨服务追踪主键（见 events.schema.json
的 `RunStarted.run_id`），Java 从 `run.started` 捕获后落到 `review.run_id`，审批时用它
定位 `POST /v1/runs/{run_id}/resume`。因此以 run_id 派生 thread 后：

- 首跑与 resume 是**同一笔审查** → 同一个 run_id → 同一个 thread，断点自然续接；
- 同合同同版本再开一笔 → 新 run_id → 新 thread，状态不串；
- resume 由 run_id 直接派生，**不再依赖 contract_id/version，也不读旧格式的落盘值**；
- 事件契约（无 review_id 字段）、run_registry 表结构、Java 侧全部无需改动。

## 边界

- 这是**生产/评测链路唯一**合法的 thread 构造方式：`app/main.py`（首跑与 resume）、
  `eval/runner.py`（评测旁路）都必须调用本函数，禁止再写第二份 f-string。
- 测试里直接喂给图的 `config.configurable["thread_id"]` 是**注入值**而非构造点：
  纯单节点/纯图验收可用固定字面量隔离用例（如 `t-pause`）；但**凡经 HTTP
  `/v1/runs/{run_id}/resume` 端点续跑的用例，注入值必须等于 `review_thread_id(run_id)`**，
  否则新端点派生出的 thread 与断点所在 thread 不一致，会假失败。
  新增的 thread 作用域回归测试走真实首跑端点，不需注入。
- 旧 thread（`{contract_id}:{version}`）数据**保留只读、不清理、不自动迁移**；
  新键 `run:*` 与旧键天然不冲突，新代码不会命中旧 checkpoint。
- session 标签（`langfuse.session.id`）仍按 `contract_id:version` 聚合：它是观测维度，
  与状态隔离维度正交，详见 ADR-0006「代价」。
"""

from __future__ import annotations

THREAD_PREFIX = "run:"


def review_thread_id(run_id: str) -> str:
    """由一次审查的 run_id 派生唯一的 LangGraph thread_id。

    Args:
        run_id: 该次审查的跨服务运行 ID（非空）；resume 时复用同一个 run_id。

    Returns:
        `f"run:{run_id}"`。

    Raises:
        ValueError: run_id 为空——静默退化成 `run:` 会让所有审查重新串到一个
            thread 上，属于必须 fail-fast 的约定破坏。
    """
    if not run_id:
        raise ValueError("run_id 不能为空（thread 作用域由一次审查的 run_id 唯一派生）")
    return f"{THREAD_PREFIX}{run_id}"
