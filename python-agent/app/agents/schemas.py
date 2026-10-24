"""商务专家 Agent 的结构化输出契约（与 prompts/commercial/v1.md 的 JSON Schema 一一对应）。

为什么单独立模：prompt 是给人看的说明，这里是给 pydantic 校验的硬契约；两侧一致性由
tests/agents/test_prompt.py 保证。extra="forbid"：模型私加字段即判解析失败并走重试，
防止「看起来能跑」的漂移输出流入后续阶段。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class RawCitation(BaseModel):
    """模型输出的引用；source_id 必须在本次检索白名单内（由 validation 强制）。"""

    model_config = ConfigDict(extra="forbid")

    source_id: str = Field(..., description="工具返回候选的 id")
    article: str = Field(default="", description="该候选的法条编号，须与工具返回一致")


class RawFinding(BaseModel):
    """模型输出的单条发现；risk_level 只允许三档，unverified 由校验层降级产生。

    risk_type 是风险类别标签（如 payment_risk / breach_risk），跨专家合并时按
    (clause_id, risk_type) 去重——同一条款同一风险被多个专家命中只保留一条。
    """

    model_config = ConfigDict(extra="forbid")

    clause_id: str
    risk_type: str = Field(..., min_length=1, description="风险类别标签，跨专家去重键的一部分。")
    risk_level: Literal["high", "medium", "low"]
    summary: str
    evidence: str
    suggestion: str = ""
    citations: list[RawCitation] = Field(default_factory=list)


class RawAgentOutput(BaseModel):
    """Agent 最终输出的顶层结构；clause_id 必须回显输入条款 id。"""

    model_config = ConfigDict(extra="forbid")

    clause_id: str
    findings: list[RawFinding] = Field(default_factory=list)
