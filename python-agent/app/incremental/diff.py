"""版本 diff：按 clause_no + hash 比对两个版本的条款集合。

为什么键是「编号 + 哈希」而不是条款 ID：解析层生成的 ID 形如 doc#0003，是切分序号，
在中间插入一条条款时会整体错位，跨版本无法对齐；而编号（第三条 / 3.1）是合同作者给的
稳定身份，哈希才回答「编号没变，内容变没变」。无编号条款（前言段等）退化为按哈希配对：
内容相同即视为同一条，内容变了算新增（无法可靠判定为「改」——没有编号就没有身份）。

纯函数、无 IO、无模型调用：diff 结论必须是确定的、可复现、可单测，
它是「只花 5% 的钱」这条成本主张的事实基础。
"""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import BaseModel, Field

from app.graph.state import Clause


class ClauseDiff(BaseModel):
    """两个版本条款集合的比对结果；四类互斥且覆盖双方全部条款。"""

    changed: list[str] = Field(default_factory=list, description="编号相同、内容变化的条款 ID。")
    added: list[str] = Field(default_factory=list, description="新版本才出现的条款 ID。")
    removed: list[str] = Field(default_factory=list, description="基线有、新版本没有的条款 ID。")
    unchanged: list[str] = Field(
        default_factory=list, description="编号与内容都与基线一致的条款 ID（可走缓存复用）。"
    )

    @property
    def changed_count(self) -> int:
        return len(self.changed)

    @property
    def added_count(self) -> int:
        return len(self.added)

    @property
    def removed_count(self) -> int:
        return len(self.removed)

    @property
    def unchanged_count(self) -> int:
        return len(self.unchanged)


def clause_fingerprint(clause: Clause) -> str:
    """条款内容指纹：优先用自带 hash，缺失时按 text 现算（与 state.Clause 同口径）。"""
    return clause.hash or _fallback_hash(clause.text)


def _fallback_hash(text: str) -> str:
    from hashlib import sha256

    return sha256(text.encode("utf-8")).hexdigest()[:16]


def diff_clauses(baseline: Sequence[Clause], current: Sequence[Clause]) -> ClauseDiff:
    """比对基线版本与当前版本的条款，产出 changed/added/removed/unchanged。

    匹配规则（确定性）：
    1. 有编号：按编号配对 -> 哈希相同为 unchanged，哈希不同为 changed；
       编号在基线中不存在 -> added；
    2. 无编号：按哈希配对（同一哈希只配对一次）-> unchanged，否则 added；
    3. 基线中未被配对的条款 -> removed（ID 取基线侧的 ID，因为新版本里它已不存在）。

    重复编号（同一版本出现两个「第三条」）以最后一个为准并在文档中注明：
    重复编号本身是切分异常，diff 保持确定性优先，不做语义猜测。
    """
    baseline_by_no: dict[str, Clause] = {}
    baseline_by_hash: dict[str, Clause] = {}
    for clause in baseline:
        number = clause.no.strip()
        if number:
            baseline_by_no[number] = clause
        else:
            baseline_by_hash.setdefault(clause_fingerprint(clause), clause)

    matched_nos: set[str] = set()
    matched_hashes: set[str] = set()
    changed: list[str] = []
    added: list[str] = []
    unchanged: list[str] = []

    for clause in current:
        number = clause.no.strip()
        digest = clause_fingerprint(clause)
        if number:
            base = baseline_by_no.get(number)
            if base is None:
                added.append(clause.clause_id)
                continue
            matched_nos.add(number)
            if clause_fingerprint(base) == digest:
                unchanged.append(clause.clause_id)
            else:
                changed.append(clause.clause_id)
            continue
        if digest in baseline_by_hash and digest not in matched_hashes:
            matched_hashes.add(digest)
            unchanged.append(clause.clause_id)
        else:
            added.append(clause.clause_id)

    removed: list[str] = [
        base.clause_id for number, base in baseline_by_no.items() if number not in matched_nos
    ]
    removed.extend(
        base.clause_id
        for digest, base in baseline_by_hash.items()
        if digest not in matched_hashes
    )
    return ClauseDiff(changed=changed, added=added, removed=removed, unchanged=unchanged)
