"""专家注册表：6 个专家的唯一事实源。

并行拓扑按本表顺序分片：high 优先级（commercial/subject/liability）为必审专家，
low 优先级（ip/data/compliance）在预算超限时整体跳过（Stage 7 预算控制）。
新增专家 = 新建 app/agents/<name>.py 定义 SPEC + prompts/<name>/v1.md，并在此登记。
"""

from __future__ import annotations

from collections.abc import Iterator

from app.agents.commercial import COMMERCIAL_SPEC
from app.agents.compliance import COMPLIANCE_SPEC
from app.agents.data import DATA_SPEC
from app.agents.ip import IP_SPEC
from app.agents.liability import LIABILITY_SPEC
from app.agents.spec import ExpertSpec
from app.agents.subject import SUBJECT_SPEC

# 顺序即审计与报告的展示顺序；high 在前保证预算耗尽时先保必审专家。
EXPERTS: tuple[ExpertSpec, ...] = (
    COMMERCIAL_SPEC,
    SUBJECT_SPEC,
    LIABILITY_SPEC,
    IP_SPEC,
    DATA_SPEC,
    COMPLIANCE_SPEC,
)

EXPERTS_BY_NAME: dict[str, ExpertSpec] = {spec.name: spec for spec in EXPERTS}

HIGH_PRIORITY_EXPERTS: tuple[ExpertSpec, ...] = tuple(
    spec for spec in EXPERTS if spec.priority == "high"
)
LOW_PRIORITY_EXPERTS: tuple[ExpertSpec, ...] = tuple(
    spec for spec in EXPERTS if spec.priority == "low"
)


def get_expert(name: str) -> ExpertSpec:
    """按名取专家定义；未注册即显式失败，不静默返回 None。"""
    spec = EXPERTS_BY_NAME.get(name)
    if spec is None:
        raise KeyError(f"未注册的专家: {name!r}（可用: {sorted(EXPERTS_BY_NAME)}）")
    return spec


def get_expert_by_agent_name(agent_name: str) -> ExpertSpec | None:
    """按 Agent 名反查专家定义；Critic 打回时用它定位 finding 的产出专家。

    返回 None 而非抛错：调用方（Critic）对来源不明的 finding 走「无法路由即标记
    unverified」的降级路径，不让单条脏数据炸掉整轮 run。
    """
    for spec in EXPERTS:
        if spec.agent_name == agent_name:
            return spec
    return None


def experts_by_priority(priority: str) -> tuple[ExpertSpec, ...]:
    """取某一优先级的全部专家（顺序同注册表）。"""
    return tuple(spec for spec in EXPERTS if spec.priority == priority)


def iter_agent_names() -> Iterator[str]:
    """全部专家 Agent 名（事件 agent 字段、测试断言用）。"""
    for spec in EXPERTS:
        yield spec.agent_name
