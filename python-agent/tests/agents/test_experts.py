"""Stage 7 验收：6 专家注册表、范畴过滤与 agent 组装。

锁定：6 专家齐备且优先级分组正确（high=commercial/subject/liability 必审，
low=ip/data/compliance 可被预算跳过）；每个专家的范畴过滤把范围外条款挡在模型
调用之外（输入只喂子集）；agent 以 create_agent 组装且 system prompt 来自文件；
工具子集按专家隔离（ip/data 不拿政策检索，subject 不拿政策检索）。
"""

from __future__ import annotations

import asyncio
import json

from langchain_core.messages import SystemMessage

from app.agents.registry import (
    EXPERTS,
    EXPERTS_BY_NAME,
    HIGH_PRIORITY_EXPERTS,
    LOW_PRIORITY_EXPERTS,
    experts_by_priority,
    get_expert,
    iter_agent_names,
)
from app.agents.spec import ExpertSpec, build_expert_agent
from app.prompts import load_prompt
from tests.agents.mock_model import MockScriptedChatModel

SAMPLE_BY_EXPERT: dict[str, str] = {
    "commercial": "买方应在验收合格后30日内凭增值税专用发票支付货款。",
    "subject": "甲方签约主体资格合法有效，应提供营业执照复印件。",
    "liability": "卖方逾期供货的，每逾期一日按合同总价的0.5‰计付违约金。",
    "ip": "双方在履约前已各自拥有的知识产权仍归原权利人所有。",
    "data": "甲方应建立数据备份与日志留存机制。",
    "compliance": "因本合同引起的争议，双方提交合同签订地仲裁委员会仲裁。",
}

OUT_OF_SCOPE = "本合同一式两份，甲乙双方各执一份，具有同等法律效力。"


def test_registry_contents() -> None:
    assert len(EXPERTS) == 6
    assert set(EXPERTS_BY_NAME) == {
        "commercial",
        "subject",
        "liability",
        "ip",
        "data",
        "compliance",
    }
    assert [spec.name for spec in HIGH_PRIORITY_EXPERTS] == [
        "commercial",
        "subject",
        "liability",
    ]
    assert [spec.name for spec in LOW_PRIORITY_EXPERTS] == ["ip", "data", "compliance"]
    assert experts_by_priority("low") == LOW_PRIORITY_EXPERTS
    assert len(set(iter_agent_names())) == 6


def test_get_expert_unknown_fails_loudly() -> None:
    try:
        get_expert("nonexistent")
    except KeyError as exc:
        assert "未注册的专家" in str(exc)
    else:  # pragma: no cover - 失败路径
        raise AssertionError("未注册专家必须显式失败")


def test_each_expert_claims_its_own_sample_and_rejects_others() -> None:
    for name, text in SAMPLE_BY_EXPERT.items():
        spec = get_expert(name)
        assert spec.in_scope(text), f"{name} 应认领本范畴条款: {text}"
        others = [other.name for other in EXPERTS if other.name != name and other.in_scope(text)]
        assert not others, f"{text} 被 {others} 意外认领（语料/keywords 漂移）"


def test_out_of_scope_text_claimed_by_nobody() -> None:
    assert not any(spec.in_scope(OUT_OF_SCOPE) for spec in EXPERTS)


def test_tool_subsets_are_isolated_per_expert() -> None:
    commercial = {tool.name for tool in get_expert("commercial").tools}
    subject = {tool.name for tool in get_expert("subject").tools}
    ip = {tool.name for tool in get_expert("ip").tools}
    # 工具子集按专家隔离（每个专家只拿自己范畴需要的检索工具）：
    # commercial/compliance 全量三件套；subject/liability 无政策检索；ip/data 无相似条款。
    assert commercial == {
        "mock_search_law",
        "mock_search_policy",
        "mock_search_similar_clauses",
    }
    assert subject == {"mock_search_law", "mock_search_similar_clauses"}
    assert {t.name for t in get_expert("liability").tools} == subject
    assert ip == {"mock_search_law", "mock_search_policy"}
    assert {t.name for t in get_expert("data").tools} == ip
    assert {t.name for t in get_expert("compliance").tools} == commercial


def test_build_expert_agent_uses_file_prompt_and_name() -> None:
    for name, text in SAMPLE_BY_EXPERT.items():
        spec: ExpertSpec = get_expert(name)
        model = MockScriptedChatModel()
        agent = build_expert_agent(spec, model)
        assert agent.name == spec.agent_name
        message = json.dumps(
            {
                "contract_meta": {"contract_id": "x", "version": 1},
                "clause": {"clause_id": "c-1", "index": 1, "text": text},
                "instruction": "审查该条款。",
            },
            ensure_ascii=False,
        )
        result = asyncio.run(agent.ainvoke({"messages": [message]}))
        system_messages = [m for m in model.last_messages if isinstance(m, SystemMessage)]
        assert system_messages, f"{name} 必须携带 system prompt"
        assert system_messages[0].text == load_prompt(spec.name, spec.prompt_version)
        assert result["messages"], "agent 应产出消息"
