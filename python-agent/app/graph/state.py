"""审查运行状态与领域模型。

为什么用 TypedDict 承载运行态：ReviewState 是 LangGraph 的状态载体，所有节点只通过它读写；
图的可中断/可恢复依赖该状态可被 checkpoint 序列化（铁律 4），故字段名由契约锁定，禁止改名。
Clause/Finding/Citation/Budget 是节点间传递的纯数据结构，用 pydantic v2 保证校验与序列化。
"""

from typing import Annotated, TypedDict

from pydantic import BaseModel, Field


class Citation(BaseModel):
    """检索白名单中的引用条目；引用只能来自这里，禁止模型自由生成法条编号（红线）。"""

    source_id: str = Field(..., description="白名单条目唯一 ID，可追溯来源。")
    title: str = Field(..., description="法规/条款标题。")
    excerpt: str = Field(..., description="命中摘录片段，用于前端展示与审计留痕。")
    url: str | None = Field(default=None, description="可选来源链接，便于人工跳转核对。")


class Clause(BaseModel):
    """合同经切分后的单个条款，是审查与审批回写的最小单位。"""

    clause_id: str = Field(..., description="条款唯一 ID，审批回写 Word 批注时定位用。")
    index: int = Field(..., description="条款在合同中的顺序位，决定展示与批注顺序。")
    text: str = Field(..., description="条款原文。")
    risk_level: str = Field(default="unknown", description="low/medium/high/unknown，初切分时未知")


class Finding(BaseModel):
    """单个专家 Agent 对某条款的审查发现。"""

    clause_id: str = Field(..., description="关联条款 ID。")
    agent: str = Field(..., description="产出该发现的专家 Agent 名。")
    risk_level: str = Field(..., description="low/medium/high，驱动后续人工审批阈值。")
    summary: str = Field(..., description="发现摘要，面向业务人员的结论。")
    citations: list[Citation] = Field(
        default_factory=list, description="支撑结论的白名单引用，不可为空才能过关。"
    )
    suggestion: str = Field(default="", description="修改建议，回写 Word 批注用。")


class Budget(BaseModel):
    """本次运行的成本与时限约束，超阈值触发熔断/降级。"""

    max_tokens: int = Field(..., description="token 硬上限，超限即停。")
    max_cost_cny: float = Field(..., description="花费硬上限（人民币元），超限熔断。")
    deadline_ts: int = Field(..., description="截止时间戳（epoch 秒），超时降级。")


def merge_findings(left: list[Finding], right: list[Finding]) -> list[Finding]:
    """LangGraph reducer：节点返回的新发现追加到已有列表，不覆盖历史发现。"""
    return left + right


class ReviewState(TypedDict):
    """LangGraph 运行态；字段名由契约锁定，禁止改名（保证 checkpoint 兼容）。"""

    contract_id: str
    version: int
    clauses: list[Clause]
    findings: Annotated[list[Finding], merge_findings]
    budget: Budget
    critic_failures: int
    approved: bool
    degraded: bool
