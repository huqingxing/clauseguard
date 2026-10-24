"""解析层领域模型（确定性，不依赖 LLM）。

为什么独立成包：Stage 5 只负责「合同 -> 条款树」，是后续审查节点的纯输入；模型与运行态
ReviewState 解耦，避免提前耦合编排层（铁律：只做当前阶段）。`Block`/`RawDocument` 是解析内部
结构，`Clause` 是切分后的对外最小单位，所有偏移语义以 `raw[start:end] == text` 为准（硬约束）。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import StrEnum

from pydantic import BaseModel, Field


class ClauseType(StrEnum):
    """条款类型；表格/附件/签章必须独立成条款，不并入正文（任务要求）。"""

    CLAUSE = "clause"
    TABLE = "table"
    ATTACHMENT = "attachment"
    SIGNATURE = "signature"


class Clause(BaseModel):
    """切分后的单个条款节点；是审查、引用校验、审批回写的最小单位。"""

    id: str = Field(..., description="条款唯一 ID，跨节点引用与回写 Word 批注用。")
    parent_id: str | None = Field(default=None, description="父条款 ID；顶层条款为 None。")
    no: str = Field(default="", description="编号原文（如 第一条 / 1.1 / （一） / Article 5）。")
    title: str = Field(default="", description="标题（首行去编号后的文本）。")
    text: str = Field(..., description="条款原文；必须满足 raw[start:end] == text。")
    level: int = Field(..., ge=1, le=4, description="层级 1-4，由编号样式+字号+缩进启发式决定。")
    page: int = Field(..., ge=1, description="起始页（跨页条款取起始页）。")
    start: int = Field(..., ge=0, description="在 raw 中的起偏移（含）。")
    end: int = Field(..., description="在 raw 中的止偏移（不含）。")
    hash: str = Field(..., description="text 的 sha256 前 16 位，便于去重与缓存校验。")
    type: ClauseType = Field(default=ClauseType.CLAUSE, description="条款类型。")


@dataclass
class Block:
    """解析中间产物：一段带版式线索的文本/表格/图片块，偏移指向整文档 raw。

    为什么用 dataclass 而非 pydantic：块是高频内部对象（80 页可达数千个），避免序列化开销；
    text 始终等于 raw[start:end]，是偏移一致性的唯一事实源。
    """

    page: int
    text: str
    start: int
    end: int
    font_size: float = 0.0
    bold: bool = False
    indent: float = 0.0
    kind: str = "text"  # text | table | attachment | image
    style_level: int | None = None  # DOCX 标题样式暗示的层级（1-4），无则为 None


@dataclass
class RawDocument:
    """整文档的原始文本与块序列；raw 是偏移的唯一坐标系。"""

    raw: str
    blocks: list[Block] = field(default_factory=list)
    doc_type: str = "pdf"  # pdf | docx
    source: str = ""


class ParseResult(BaseModel):
    """一次解析的完整产出；raw 保留以支持后续按偏移回溯原文。"""

    doc_id: str
    source: str
    doc_type: str
    raw: str
    clauses: list[Clause] = Field(default_factory=list)


class ParseError(Exception):
    """解析失败一律带位置（页号）抛出，禁止静默吞掉（任务红线）。"""

    def __init__(self, message: str, *, page: int | None = None) -> None:
        self.page = page
        located = f"[page={page}] {message}" if page is not None else message
        super().__init__(located)


def clause_hash(text: str) -> str:
    """text 的 sha256 前 16 位；用作去重键与切片完整性校验。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
