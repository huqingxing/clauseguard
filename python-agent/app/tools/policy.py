"""企业内部制度检索工具（mock）。

TODO(Stage 7): 替换为真实制度库检索（pgvector 或 Java MCP 工具封装）；接口与候选字段
保持不变，白名单校验逻辑无需改动。
"""

from __future__ import annotations

from langchain_core.tools import tool

from app.tools.corpus import CorpusEntry, candidates_json

_MOCK_POLICY_CORPUS: list[CorpusEntry] = [
    {
        "id": "policy-001",
        "article": "《采购合同管理规范》预付款比例条款",
        "title": "预付款比例与扣回机制",
        "text": (
            "预付款须与履约进度挂钩，不得超过合同总价的30%，"
            "并应约定按进度款比例扣回的机制。"
        ),
        "keywords": ["预付", "预付款", "30%", "扣回", "退还"],
    },
    {
        "id": "policy-002",
        "article": "《交付与验收管理办法》验收期条款",
        "title": "验收期下限",
        "text": (
            "验收期自到货之日起不得少于10个工作日；"
            "验收标准须以书面形式确认，禁止单方确定。"
        ),
        "keywords": ["验收", "检验", "到货", "标准", "异议"],
    },
    {
        "id": "policy-003",
        "article": "《财务报销与发票管理办法》发票合规条款",
        "title": "发票类型与品名",
        "text": (
            "发票类型与税率须与业务实质一致，开票品名不得按交易对方要求变更，"
            "普通发票不得替代专用发票结算。"
        ),
        "keywords": ["发票", "税率", "品名", "专用发票", "普通发票"],
    },
    {
        "id": "policy-004",
        "article": "《合同审查指引》交付期限条款",
        "title": "交付期限确定性",
        "text": (
            "交付期限应约定具体日期或可计算的确定性条件，"
            "禁止使用「尽快」「另行通知」等不确定表述。"
        ),
        "keywords": ["交付", "期限", "尽快", "通知", "时间"],
    },
    {
        "id": "policy-005",
        "article": "《供应商管理规范》违约责任条款",
        "title": "违约金比例下限",
        "text": (
            "逾期违约金比例原则上不低于日万分之三，"
            "并应设置以合同总价为基准的责任上限。"
        ),
        "keywords": ["违约金", "逾期", "比例", "上限", "违约"],
    },
]


@tool
def mock_search_policy(query: str) -> str:
    """检索与合同条款相关的企业内部制度候选。

    Args:
        query: 条款原文或关键词
    """
    return candidates_json(_MOCK_POLICY_CORPUS, query)
