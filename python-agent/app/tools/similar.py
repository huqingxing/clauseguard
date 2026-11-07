"""历史相似条款检索工具。阶段 6 尚未建历史条款库，live 模式暂共用真实法条索引。

为什么必须一起切：MockChatModel 取「最后一条 ToolMessage 的首个候选」当引用，工具
绑定顺序里 similar 往往排在 law 之后；只接 law 会让验收看到的仍是 `sim-00x`。
"""

from __future__ import annotations

from langchain_core.tools import tool

from app.tools.corpus import CorpusEntry, candidates_json
from app.tools.retrieval_backend import live_payload

_MOCK_SIMILAR_CORPUS: list[CorpusEntry] = [
    {
        "id": "sim-001",
        "article": "历史采购合同-付款条款",
        "title": "分期付款结构",
        "text": (
            "预付款30%，到货验收合格后30日内凭增值税专用发票支付尾款，预付按进度款20%比例扣回。"
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
            "到货后15个工作日内完成验收并出具书面验收单；逾期视为通过的，须以显著方式提示买方。"
        ),
        "keywords": ["验收", "检验", "到货", "视为", "异议", "标准"],
    },
    {
        "id": "sim-004",
        "article": "历史采购合同-发票条款",
        "title": "专用发票与暂缓付款",
        "text": (
            "卖方开具税率为13%的增值税专用发票；发票不合规的，买方有权暂缓付款且不承担违约责任。"
        ),
        "keywords": ["发票", "税率", "专用发票", "开具", "付款"],
    },
    {
        "id": "sim-005",
        "article": "历史采购合同-违约条款",
        "title": "违约金上限",
        "text": ("任何一方违约的，违约金以合同总价的20%为上限，押金不退条款须经审批后方可写入。"),
        "keywords": ["违约", "违约金", "上限", "没收", "抵销"],
    },
]


@tool
def mock_search_similar_clauses(query: str) -> str:
    """检索与合同条款相似的历史条款候选。

    Args:
        query: 条款原文或关键词
    """
    payload = live_payload(query)
    return payload if payload is not None else candidates_json(_MOCK_SIMILAR_CORPUS, query)
