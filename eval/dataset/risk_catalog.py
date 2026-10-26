"""Stage 13 评测风险目录：22 个风险类型 + 22 条法律来源池 + 确定性检测规则。

为什么独立成文件：评测资产不属于生产代码，但必须与 6 个专家 prompt / scope_keywords
对齐——每条 risk_type 的检测关键词都取自对应专家 ExpertSpec.scope_keywords（见
app/agents/*.py），保证 dispatch 会把含该关键词的条款路由到正确的专家，examiner 才会
被调用（否则条款根本不会进该专家审查）。

设计要点：
- ENABLED 控制 examiner 是否产出该风险；含 3 个默认禁用的 demo 目标（penalty/trade_secret/
  anti_corruption），代表「尚未接入检测」的类别，评测不植入、不计召回。
- FRAGILE 标记的风险类型会被 examiner 故意引用「相邻但错误」的法条（仍在检索结果池内，
  能通过 critic 白名单，但与金标不一致）——用于让引用准确率指标 < 100% 并真实暴露错误。
- 默认 risk_level 同时被生成器与 examiner 使用，保证金标与系统产出在等级上确定性一致。
"""

from __future__ import annotations

from dataclasses import dataclass

# ---------------------------------------------------------------------------
# 法律来源池（22 条）：mock 检索工具的候选返回；source_id 即引用白名单条目 id。
# excerpt 为短摘录，url 为公开法规库占位（仅供人工跳转核对，评测不访问网络）。
# ---------------------------------------------------------------------------
SOURCES: dict[str, dict[str, str]] = {
    "src-civil-509": {
        "article": "《民法典》第五百零九条",
        "title": "合同履行原则",
        "excerpt": "当事人应当按照约定全面履行自己的义务。",
        "url": "https://flk.npc.gov.cn/#/detail?bid=flk_122",
    },
    "src-civil-580": {
        "article": "《民法典》第五百八十条",
        "title": "非金钱债务违约责任",
        "excerpt": "当事人一方不履行非金钱债务或者履行不符合约定的，对方可以请求履行。",
        "url": "https://flk.npc.gov.cn/#/detail?bid=flk_123",
    },
    "src-civil-590": {
        "article": "《民法典》第五百九十条",
        "title": "不可抗力",
        "excerpt": "不可抗力是不能预见、不能避免且不能克服的客观情况。",
        "url": "https://flk.npc.gov.cn/#/detail?bid=flk_124",
    },
    "src-civil-563": {
        "article": "《民法典》第五百六十三条",
        "title": "合同法定解除",
        "excerpt": "在履行期限届满前，当事人一方明确表示或者以自己的行为表明不履行主要债务，对方可以解除合同。",  # noqa: E501
        "url": "https://flk.npc.gov.cn/#/detail?bid=flk_125",
    },
    "src-civil-533": {
        "article": "《民法典》第五百三十三条",
        "title": "情势变更",
        "excerpt": "合同成立后，合同的基础条件发生了当事人在订立合同时无法预见的重大变化，受不利影响的当事人可以请求重新协商。",  # noqa: E501
        "url": "https://flk.npc.gov.cn/#/detail?bid=flk_126",
    },
    "src-vat": {
        "article": "《增值税暂行条例》第二条",
        "title": "增值税税率",
        "excerpt": "纳税人销售货物、劳务、有形动产租赁服务或者进口货物，税率为百分之十三。",
        "url": "https://flk.npc.gov.cn/#/detail?bid=flk_vat",
    },
    "src-invoice": {
        "article": "《发票管理办法》第二十二条",
        "title": "发票开具要求",
        "excerpt": "开具发票应当按照规定的时限、顺序、栏目，全部联次一次性如实开具。",
        "url": "https://flk.npc.gov.cn/#/detail?bid=flk_invoice",
    },
    "src-company-12": {
        "article": "《公司法》第十二条",
        "title": "公司经营范围",
        "excerpt": "公司的经营范围由公司章程规定，并依法登记。",
        "url": "https://flk.npc.gov.cn/#/detail?bid=flk_company",
    },
    "src-company-13": {
        "article": "《公司法》第十三条",
        "title": "法定代表人",
        "excerpt": "公司法定代表人依照公司章程的规定，由代表公司执行公司事务的董事或者经理担任。",
        "url": "https://flk.npc.gov.cn/#/detail?bid=flk_company_13",
    },
    "src-ip-123": {
        "article": "《专利法》第六条",
        "title": "职务发明创造",
        "excerpt": "执行本单位的任务或者主要是利用本单位的物质技术条件所完成的发明创造为职务发明创造。",  # noqa: E501
        "url": "https://flk.npc.gov.cn/#/detail?bid=flk_patent",
    },
    "src-copyright": {
        "article": "《著作权法》第十一条",
        "title": "著作权归属",
        "excerpt": "著作权属于作者，本法另有规定的除外。",
        "url": "https://flk.npc.gov.cn/#/detail?bid=flk_copyright",
    },
    "src-anti-unfair": {
        "article": "《反不正当竞争法》第二条",
        "title": "不正当竞争",
        "excerpt": "经营者在生产经营活动中，应当遵循自愿、平等、公平、诚信的原则，遵守法律和商业道德。",  # noqa: E501
        "url": "https://flk.npc.gov.cn/#/detail?bid=flk_unfair",
    },
    "src-trade-secret": {
        "article": "《反不正当竞争法》第九条",
        "title": "商业秘密",
        "excerpt": "本法所称的商业秘密，是指不为公众所知悉、具有商业价值并经权利人采取相应保密措施的技术信息、经营信息等商业信息。",  # noqa: E501
        "url": "https://flk.npc.gov.cn/#/detail?bid=flk_secret",
    },
    "src-pipl-13": {
        "article": "《个人信息保护法》第十三条",
        "title": "个人信息处理合法性基础",
        "excerpt": "符合下列情形之一的，个人信息处理者方可处理个人信息：（一）取得个人的同意。",
        "url": "https://flk.npc.gov.cn/#/detail?bid=flk_pipl",
    },
    "src-pipl-38": {
        "article": "《个人信息保护法》第三十八条",
        "title": "个人信息跨境提供",
        "excerpt": "个人信息处理者因业务等需要，确需向境外提供个人信息的，应当具备相应条件并通过安全评估。",  # noqa: E501
        "url": "https://flk.npc.gov.cn/#/detail?bid=flk_pipl_38",
    },
    "src-dsl": {
        "article": "《数据安全法》第三条",
        "title": "数据安全管理",
        "excerpt": "数据处理活动以及数据安全的管理、保障应当建立健全数据安全管理制度。",
        "url": "https://flk.npc.gov.cn/#/detail?bid=flk_dsl",
    },
    "src-csl": {
        "article": "《网络安全法》第二十一条",
        "title": "网络安全等级保护",
        "excerpt": "国家实行网络安全等级保护制度，网络运营者应当按照要求履行安全保护义务。",
        "url": "https://flk.npc.gov.cn/#/detail?bid=flk_csl",
    },
    "src-arbitration-16": {
        "article": "《仲裁法》第十六条",
        "title": "仲裁协议",
        "excerpt": "仲裁协议应当具有请求仲裁的意思表示、仲裁事项和选定的仲裁委员会。",
        "url": "https://flk.npc.gov.cn/#/detail?bid=flk_arbitration",
    },
    "src-civil-34": {
        "article": "《民事诉讼法》第三十五条",
        "title": "协议管辖",
        "excerpt": "合同或者其他财产权益纠纷的当事人可以书面协议选择与被告住所地、合同履行地等人民法院管辖。",  # noqa: E501
        "url": "https://flk.npc.gov.cn/#/detail?bid=flk_civil_34",
    },
    "src-export": {
        "article": "《出口管制法》第五条",
        "title": "出口管制",
        "excerpt": "国家对两用物项、军品、核以及其他与维护国家安全和利益相关的货物、技术等实施出口管制。",  # noqa: E501
        "url": "https://flk.npc.gov.cn/#/detail?bid=flk_export",
    },
    "src-anti-monopoly": {
        "article": "《反垄断法》第十七条",
        "title": "滥用市场支配地位",
        "excerpt": "禁止具有市场支配地位的经营者从事滥用市场支配地位的行为。",
        "url": "https://flk.npc.gov.cn/#/detail?bid=flk_monopoly",
    },
    "src-anti-corruption": {
        "article": "《反不正当竞争法》第七条",
        "title": "商业贿赂",
        "excerpt": "经营者不得采用财物或者其他手段贿赂单位或者个人，以谋取交易机会或者竞争优势。",
        "url": "https://flk.npc.gov.cn/#/detail?bid=flk_bribery",
    },
}

# 每条法律来源对应的「相邻错误引用」：fragile 风险类型会引用它，而非正确的来源。
ADJACENT_SOURCE: dict[str, str] = {
    "src-vat": "src-civil-509",
    "src-civil-34": "src-arbitration-16",
    "src-civil-590": "src-civil-533",
    "src-pipl-38": "src-dsl",
    "src-trade-secret": "src-anti-unfair",
}


def source_list() -> list[dict[str, str]]:
    """mock 检索工具返回的候选全集（每条含 id/article/title/text/url）。"""
    items: list[dict[str, str]] = []
    for source_id, meta in SOURCES.items():
        items.append(
            {
                "id": source_id,
                "article": meta["article"],
                "title": meta["title"],
                "text": meta["excerpt"],
                "url": meta["url"],
            }
        )
    return items


# ---------------------------------------------------------------------------
# 22 个风险类型：expert 名（registry 键）/ 检测关键词（必须 ∈ 该专家 scope_keywords）/
# 默认等级 / 正确来源 / 摘要与建议模板 / 是否默认启用 / 是否脆弱（引用错误来源）。
# ---------------------------------------------------------------------------
EXPERTS: tuple[str, ...] = (
    "commercial",
    "subject",
    "liability",
    "ip",
    "data",
    "compliance",
)


@dataclass(frozen=True)
class RiskDef:
    """单条风险类型的确定性定义；examiner 与生成器共用，保证金标与产出一致。"""

    risk_type: str
    expert: str
    keyword: str
    default_level: str  # low | medium | high
    source_id: str
    summary: str
    suggestion: str
    enabled: bool = True
    fragile: bool = False
    label: str = ""


RISK_DEFS: tuple[RiskDef, ...] = (
    # ---- commercial（high 必审）----
    RiskDef("payment_risk", "commercial", "预付", "high",
            "src-civil-509", "预付款比例与返还机制失衡，资金风险极高。",
            "改为分期支付并约定预付款扣回与违约返还机制。", label="付款/预付款"),
    RiskDef("delivery_risk", "commercial", "交付", "medium",
            "src-civil-509", "交付期限或地点约定不明，履约无硬约束。",
            "明确交付日期、地点与交付凭证。", label="交付"),
    RiskDef("acceptance_risk", "commercial", "验收", "medium",
            "src-civil-509", "验收期或视为条款对买方质量救济不利。",
            "延长检验期并保留隐蔽瑕疵例外。", label="验收"),
    RiskDef("invoice_risk", "commercial", "发票", "low",
            "src-vat", "发票类型或税率约定不合规，存在税务风险。",
            "明确开具增值税专用发票及正确税率。", fragile=True, label="发票"),
    # ---- subject（high 必审）----
    RiskDef("qualification_risk", "subject", "经营范围", "high",
            "src-company-12", "签约主体经营范围与合同业务不匹配，效力存疑。",
            "核验营业执照与资质，要求出具经营范围承诺。", label="主体资格"),
    RiskDef("authorization_risk", "subject", "授权", "high",
            "src-company-13", "授权代表权限不明，签署可能对主体不生效。",
            "补充授权委托书并明确权限范围与期限。", label="授权签署"),
    RiskDef("succession_risk", "subject", "承继", "medium",
            "src-civil-563", "主体分立合并时权利义务承继安排缺失。",
            "约定承继方全额承继并书面通知。", label="主体承继"),
    # ---- liability（high 必审）----
    RiskDef("breach_risk", "liability", "违约", "medium",
            "src-civil-580", "违约金比例过低或责任上限缺失，约束力弱。",
            "提高违约金至日万分之三并设置责任上限。", label="违约"),
    RiskDef("force_majeure_risk", "liability", "不可抗力", "medium",
            "src-civil-590", "不可抗力免责范围过宽且无减损义务。",
            "约定通知时限、证明与消除后继续履行。", label="不可抗力"),
    RiskDef("disclaimer_risk", "liability", "免责", "high",
            "src-civil-580", "免责条款覆盖重大过失，依法可能无效。",
            "排除故意与重大过失下的免责。", label="免责"),
    RiskDef("termination_risk", "liability", "解除合同", "high",
            "src-civil-563", "单方解除权约定失衡，损害对方权益。",
            "约定对等解除条件与通知期限。", label="合同解除"),
    RiskDef("penalty_risk", "liability", "罚则", "low",
            "src-civil-580", "罚则表述模糊，可执行性存疑。",
            "量化罚金计算方式与上限。", enabled=False, label="罚则（demo 未启用）"),
    # ---- ip（low 可跳过）----
    RiskDef("ip_ownership_risk", "ip", "职务成果", "high",
            "src-ip-123", "履约改进成果权属约定对一方不公。",
            "约定权属比例或背景知识产权保留。", label="知识产权归属"),
    RiskDef("open_source_risk", "ip", "开源", "medium",
            "src-copyright", "开源组件合规义务缺失，存在污染风险。",
            "约定开源清单、许可证审查与传染性限制。", label="开源合规"),
    RiskDef("trade_secret_risk", "ip", "商业秘密", "medium",
            "src-trade-secret", "商业秘密保护义务与范围约定不清。",
            "明确保密信息范围、期限与违约责任。", enabled=False, label="商业秘密（demo 未启用）"),
    # ---- data（low 可跳过）----
    RiskDef("data_processing_risk", "data", "个人信息", "high",
            "src-pipl-13", "个人信息处理目的期限与安全措施未约定。",
            "按最小必要原则约定处理目的、期限与加密措施。", label="个人信息处理"),
    RiskDef("cross_border_risk", "data", "跨境", "high",
            "src-pipl-38", "数据跨境未约定安全评估与合规路径。",
            "约定跨境安全评估、标准合同与接收方义务。", label="数据跨境"),
    RiskDef("credential_risk", "data", "账号", "medium",
            "src-csl", "账号口令明文存储且无权限分级。",
            "约定加密存储、最小权限与定期审计。", label="账号凭据"),
    # ---- compliance（low 可跳过）----
    RiskDef("jurisdiction_risk", "compliance", "管辖", "medium",
            "src-civil-34", "管辖约定偏向一方，诉讼成本高。",
            "改为中立管辖或明确仲裁机构。", fragile=True, label="争议管辖"),
    RiskDef("applicable_law_risk", "compliance", "适用法律", "high",
            "src-civil-34", "适用法律不明或争议解决方式冲突。",
            "明确适用法律并保留单一争议解决路径。", label="适用法律"),
    RiskDef("export_control_risk", "compliance", "出口管制", "medium",
            "src-export", "受管制物项出口缺乏合规审查。",
            "约定出口管制清单核查与许可义务。", label="出口管制"),
    RiskDef("anti_corruption_risk", "compliance", "反腐败", "medium",
            "src-anti-corruption", "廉洁与反腐败义务约定缺失。",
            "约定反腐败承诺与违约停止合作机制。", enabled=False, label="反腐败（demo 未启用）"),
)


RISK_BY_TYPE: dict[str, RiskDef] = {r.risk_type: r for r in RISK_DEFS}
ENABLED_RISK_TYPES: tuple[str, ...] = tuple(r.risk_type for r in RISK_DEFS if r.enabled)


def risk_defs_for_expert(expert: str) -> tuple[RiskDef, ...]:
    """该专家拥有（且启用）的全部风险类型；examiner 只在这些类型里产出。"""
    return tuple(r for r in RISK_DEFS if r.expert == expert and r.enabled)


def source_for(risk_type: str, *, correct: bool) -> str:
    """返回该风险类型应引用的 source_id；correct=False 时返回相邻错误来源（fragile）。"""
    risk = RISK_BY_TYPE[risk_type]
    if correct or not risk.fragile:
        return risk.source_id
    return ADJACENT_SOURCE.get(risk.source_id, risk.source_id)
