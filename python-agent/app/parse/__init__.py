"""合同解析包：PDF/DOCX -> 确定性条款树。

对外唯一入口是 parse_contract()：加载（loader）-> 切分（splitter），返回 ParseResult。
持久化由 repository 提供（落 Postgres 供后续节点），本包不在此处直接入库，留给调用方按需保存。
"""

from __future__ import annotations

from pathlib import Path

from app.parse.loader import load_document
from app.parse.models import (
    Block,
    Clause,
    ClauseType,
    ParseError,
    ParseResult,
    RawDocument,
)
from app.parse.repository import (
    CLAUSES_DDL,
    ClauseStore,
    InMemoryClauseStore,
    PostgresClauseStore,
    create_clauses_table,
)
from app.parse.splitter import split_document

__all__ = [
    "Block",
    "Clause",
    "ClauseType",
    "CLAUSES_DDL",
    "ClauseStore",
    "InMemoryClauseStore",
    "ParseError",
    "ParseResult",
    "PostgresClauseStore",
    "RawDocument",
    "create_clauses_table",
    "load_document",
    "parse_contract",
    "split_document",
]


def parse_contract(path: str | Path, doc_id: str | None = None) -> ParseResult:
    """端到端解析：加载文档并切分为条款树；doc_id 缺省取文件名干。"""
    doc = load_document(path)
    result = split_document(doc)
    if doc_id:
        result.doc_id = doc_id
    return result
