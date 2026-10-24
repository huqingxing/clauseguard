"""Stage 6 验收：mock 检索工具的确定性与候选集形状。

引用白名单校验依赖「同一 query -> 同一批候选（含 id/article/text/score）」，故这里
锁定确定性、字段完整性与无命中退化行为。
"""

from __future__ import annotations

import json

from app.tools.law import mock_search_law
from app.tools.policy import mock_search_policy
from app.tools.similar import mock_search_similar_clauses


def _candidates(tool_result: str) -> list[dict[str, object]]:
    payload = json.loads(tool_result)
    candidates = payload["candidates"]
    assert isinstance(candidates, list)
    return candidates


def test_mock_tools_return_deterministic_candidates() -> None:
    query = "买方逾期支付合同总价的100%作为预付款，发票税率13%"
    for tool in (mock_search_law, mock_search_policy, mock_search_similar_clauses):
        first = _candidates(tool.invoke({"query": query}))
        second = _candidates(tool.invoke({"query": query}))
        assert first == second, f"{tool.name} 必须确定性返回"
        assert 1 <= len(first) <= 3
        for candidate in first:
            assert {"id", "article", "title", "text", "score"} <= set(candidate)
            assert str(candidate["id"]).startswith(("law-", "policy-", "sim-"))


def test_keyword_hits_rank_relevant_candidates_first() -> None:
    candidates = _candidates(mock_search_law.invoke({"query": "违约金比例与上限如何约定"}))
    assert candidates[0]["id"] == "law-008", "违约金关键词应把违约金法条排到首位"


def test_policy_tool_ranks_policy_by_keywords() -> None:
    candidates = _candidates(mock_search_policy.invoke({"query": "预付款30%何时扣回"}))
    assert candidates[0]["id"] == "policy-001"


def test_no_hit_degrades_to_fixed_prefix_not_empty() -> None:
    # 无关键词命中时退化为语料前几条：白名单非空，模型的编造引用才会被丢弃并降级。
    candidates = _candidates(mock_search_law.invoke({"query": "zzz完全不相关zzz"}))
    assert len(candidates) == 3
    assert [c["id"] for c in candidates] == ["law-001", "law-002", "law-003"]


def test_tool_names_follow_mock_convention() -> None:
    # 红线：mock 必须显式命名 mock_ 前缀。
    for tool in (mock_search_law, mock_search_policy, mock_search_similar_clauses):
        assert tool.name.startswith("mock_")
