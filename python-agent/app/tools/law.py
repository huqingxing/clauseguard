"""法规检索工具。mock 模式返回固定样例；live 模式（默认）返回 pgvector 真实法条。

为什么保留 `mock_` 名字：工具名被 prompt、Inspector 与既有测试按字符串引用，改名等于
动拓扑之外的契约；阶段 6 用 `clauseguard.retrieval.mode` 决定实现，名字只描述能力。
接口 (query: str) -> JSON 字符串、候选字段 id/article/title/text/score 一律不变，
因此引用白名单校验（ADR-0003）零改动。
"""

from __future__ import annotations

from langchain_core.tools import tool

from app.tools.corpus import CorpusEntry, candidates_json
from app.tools.retrieval_backend import live_payload

_MOCK_LAW_CORPUS: list[CorpusEntry] = [
    {
        "id": "law-001",
        "article": "《民法典》第五百七十七条",
        "title": "违约责任",
        "text": (
            "当事人一方不履行合同义务或者履行合同义务不符合约定的，"
            "应当承担继续履行、采取补救措施或者赔偿损失等违约责任。"
        ),
        "keywords": ["违约", "逾期", "赔偿", "责任", "没收"],
    },
    {
        "id": "law-002",
        "article": "《民法典》第六百二十六条",
        "title": "价款的数额与支付方式",
        "text": (
            "买受人应当按照约定的数额和支付方式支付价款。"
            "对价款的数额和支付方式没有约定或者约定不明确的，"
            "适用本法第五百一十条、第五百一十一条的规定。"
        ),
        "keywords": ["付款", "支付", "价款", "货款", "数额"],
    },
    {
        "id": "law-003",
        "article": "《民法典》第六百三十条",
        "title": "检验期间与质量异议",
        "text": (
            "买受人收到标的物时应当在约定的检验期间内检验；没有约定检验期间的，应当及时检验。"
            "买受人应当在检验期间内将标的物的数量或者质量不符合约定的情形通知出卖人。"
        ),
        "keywords": ["验收", "检验", "异议", "质量", "到货"],
    },
    {
        "id": "law-004",
        "article": "《民法典》第六百二十八条",
        "title": "付款时间",
        "text": (
            "买受人应当按照约定的时间支付价款。"
            "对支付时间没有约定或者约定不明确，依据本法第五百一十条的规定仍不能确定的，"
            "买受人应当在收到标的物或者提取标的物单证的同时支付。"
        ),
        "keywords": ["付款", "支付", "时间", "另行", "协商", "顺延"],
    },
    {
        "id": "law-005",
        "article": "《民法典》第五百一十条",
        "title": "约定不明的补充确定",
        "text": (
            "合同生效后，当事人就质量、价款或者报酬、履行地点等内容没有约定或者约定不明确的，"
            "可以协议补充；不能达成补充协议的，按照合同相关条款或者交易习惯确定。"
        ),
        "keywords": ["约定", "不明", "另行", "协商", "补充"],
    },
    {
        "id": "law-006",
        "article": "《发票管理办法》第十九条",
        "title": "发票开具义务",
        "text": (
            "销售商品、提供服务以及从事其他经营活动的单位和个人，"
            "对外发生经营业务收取款项，收款方应当向付款方开具发票；"
            "开具发票应当按照规定的时限、顺序、栏目，全部联次一次性如实开具。"
        ),
        "keywords": ["发票", "开具", "品名", "税率", "虚开"],
    },
    {
        "id": "law-007",
        "article": "《民法典》第六百零四条",
        "title": "风险转移",
        "text": (
            "标的物毁损、灭失的风险，在标的物交付之前由出卖人承担，交付之后由买受人承担，"
            "但是法律另有规定或者当事人另有约定的除外。"
        ),
        "keywords": ["风险", "毁损", "灭失", "交付", "转移"],
    },
    {
        "id": "law-008",
        "article": "《民法典》第五百八十五条",
        "title": "违约金及其调整",
        "text": (
            "当事人可以约定一方违约时应当根据违约情况向对方支付一定数额的违约金。"
            "约定的违约金低于造成的损失的，人民法院可以根据当事人的请求予以增加；"
            "约定的违约金过分高于造成的损失的，人民法院可以适当减少。"
        ),
        "keywords": ["违约金", "违约", "损失", "比例", "上限"],
    },
]


@tool
def mock_search_law(query: str) -> str:
    """检索与合同条款相关的法律法规候选。

    Args:
        query: 条款原文或关键词
    """
    payload = live_payload(query)
    return payload if payload is not None else candidates_json(_MOCK_LAW_CORPUS, query)
