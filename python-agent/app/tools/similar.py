"""历史相似条款检索工具（mock）。

TODO(Stage 7): 替换为真实相似条款检索（pgvector 条款库或 Java MCP 工具封装）；接口与
候选字段保持不变，白名单校验逻辑无需改动。
"""

from __future__ import annotations

from langchain_core.tools import tool

from app.tools.corpus import CorpusEntry, candidates_json

_MOCK_SIMILAR_CORPUS: list[CorpusEntry] = [
    {
        "id": "sim-001",
        "article": "历史采购合同-付款条款",
        "title": "分期付款结构",
        "text": (
            "预付款30%，到货验收合格后30日内凭增值税专用发票支付尾款，"
            "预付按进度款20%比例扣回。"
        ),
        "keywords": ["付款", "支付", "预付", "尾款", "发票", "货款"],
    },
    {
        "id": "sim-002",
        "article": "历史采购合同-交付条款",
        "title": "分批交付与逾期责任",
        "text": (
            "卖方于约定日期前分批交付至指定地点，逾期按日万分之五支付违约金，"
            "并以合同总价10%为上限。"
        ),
        "keywords": ["交付", "交货", "逾期", "分批", "日期", "运输"],
    },
    {
        "id": "sim-003",
        "article": "历史采购合同-验收条款",
        "title": "验收期与视为通过",
        "text": (
            "到货后15个工作日内完成验收并出具书面验收单；"
            "逾期视为通过的，须以显著方式提示买方。"
        ),
        "keywords": ["验收", "检验", "到货", "视为", "异议", "标准"],
    },
    {
        "id": "sim-004",
        "article": "历史采购合同-发票条款",
        "title": "专用发票与暂缓付款",
        "text": (
            "卖方开具税率为13%的增值税专用发票；"
            "发票不合规的，买方有权暂缓付款且不承担违约责任。"
        ),
        "keywords": ["发票", "税率", "专用发票", "开具", "付款"],
    },
    {
        "id": "sim-005",
        "article": "历史采购合同-违约条款",
        "title": "违约金上限",
        "text": (
            "任何一方违约的，违约金以合同总价的20%为上限，"
            "押金不退条款须经审批后方可写入。"
        ),
        "keywords": ["违约", "违约金", "上限", "没收", "抵销"],
    },
]


@tool
def mock_search_similar_clauses(query: str) -> str:
    """检索与合同条款相似的历史条款候选。

    Args:
        query: 条款原文或关键词
    """
    return candidates_json(_MOCK_SIMILAR_CORPUS, query)
