"""Stage 6/7/8 评测数据集：20 条款单专家集 + 50 条款并行语料 + 打回重语料。

为什么放在 tests 而不是 app：已知答案是评测资产，不属于生产代码。scripted 模型的行为表
由本文件的 expect_finding 推导：模拟一个「合格但不完美」的模型——召回 8/10、误报 2/10，
这样验收指标（召回≥0.7、误报≤3）检验的是整条管线（解析、白名单、降级、指标），
而不是恒真断言。

risk_type 是跨专家合并的去重键另一半（(clause_id, risk_type)），Stage 7 起每条
spec 必须显式声明；stage7_corpus() 另含 2 条跨专家重叠加名条款（同 clause_id 同
risk_type 被两个专家各产出一次），用于验证 merge_findings 幂等去重。

Stage 8 起 rework_evidence_kw 声明「收到 Critic 重审指令后改用哪个证据关键词」：
让 scripted 模型可以模拟「首轮证据不落地、被打回后修正」与「永远不修正」两种行为，
分别验证打回-重审闭环与轮数上限收敛。
"""

from __future__ import annotations

from dataclasses import dataclass

from app.graph.state import Clause


@dataclass(frozen=True)
class ClauseSpec:
    """单条评测条款的已知答案。"""

    clause_id: str
    text: str
    expect_finding: bool
    risk_level: str = ""
    risk_type: str = ""
    summary: str = ""
    suggestion: str = ""
    evidence_kw: str = ""
    cite_mode: str = "valid"  # valid | fabricated | mixed
    # Stage 8：收到 Critic 重审指令后改用的证据关键词；空 = 不修正（永远失败场景）。
    rework_evidence_kw: str = ""


RISK_CLAUSES: list[ClauseSpec] = [
    ClauseSpec(
        clause_id="risk-01",
        risk_type="payment_risk",
        text="买方应在合同签订后3个工作日内支付合同总价的100%作为预付款，该款项在任何情况下不予退还。",
        expect_finding=True,
        risk_level="high",
        summary="预付100%且无返还与扣回机制，买方资金风险极高。",
        suggestion="改为「预付30%+到货验收合格后凭增值税专用发票支付尾款」的分期结构，并约定预付款扣回机制。",
        evidence_kw="100%",
    ),
    ClauseSpec(
        clause_id="risk-02",
        risk_type="payment_risk",
        text="货款的具体支付时间由双方另行协商确定，协商不成的，买方有权无限期顺延支付。",
        expect_finding=True,
        risk_level="medium",
        summary="付款时间约定不明且买方可无限期顺延，回款无保障。",
        suggestion="约定明确付款节点（如验收合格后30日内），删除无限期顺延表述。",
        evidence_kw="另行协商",
    ),
    ClauseSpec(
        clause_id="risk-03",
        risk_type="delivery_risk",
        text="卖方将尽快安排生产并交付货物，具体交付时间以卖方书面通知为准，买方应予配合。",
        expect_finding=True,
        risk_level="medium",
        summary="交付期限使用不确定表述，卖方履约无硬约束。",
        suggestion="改为具体交付日期或可计算的确定性条件（如某日前分批交付）。",
        evidence_kw="尽快",
    ),
    ClauseSpec(
        clause_id="risk-04",
        risk_type="breach_risk",
        text="卖方逾期交付的，每逾期一日按合同总价的0.001%向买方支付违约金。",
        expect_finding=True,
        risk_level="medium",
        summary="逾期违约金比例过低，对卖方无约束力。",
        suggestion="提高至不低于日万分之三，并设置以合同总价为基准的责任上限。",
        evidence_kw="0.001%",
    ),
    ClauseSpec(
        clause_id="risk-05",
        risk_type="acceptance_risk",
        text="货物到达后3日内，买方未提出书面异议的，视为验收合格，买方不得再行主张任何质量权利。",
        expect_finding=True,
        risk_level="high",
        summary="检验期仅3日且绝对化视为条款，买方质量救济被剥夺。",
        suggestion="检验期延长至不少于10个工作日，视为条款保留隐蔽瑕疵例外并显著提示。",
        evidence_kw="3日",
    ),
    ClauseSpec(
        clause_id="risk-06",
        risk_type="acceptance_risk",
        text="验收标准由买方根据自身需要单方确定并适时调整，卖方无权参与亦不得异议。",
        expect_finding=True,
        risk_level="medium",
        summary="验收标准由买方单方确定，卖方处于被动地位。",
        suggestion="改为按国家标准及双方书面确认的技术协议执行，变更须双方书面同意。",
        evidence_kw="单方确定",
    ),
    ClauseSpec(
        clause_id="risk-07",
        risk_type="invoice_risk",
        text="卖方应开具增值税普通发票，税率为3%，品名按买方要求填写。",
        expect_finding=True,
        risk_level="high",
        summary="发票类型与品名约定违规，存在税务与结算风险。",
        suggestion="改为开具税率13%的增值税专用发票，品名与业务实质一致。",
        evidence_kw="增值税普通发票",
    ),
    ClauseSpec(
        clause_id="risk-08",
        risk_type="payment_risk",
        text="买方有权以其对卖方的其他债权直接抵销本合同价款，无需征得卖方同意。",
        expect_finding=True,
        risk_level="medium",
        summary="买方单方抵销权无对等限制，卖方收款确定性受损。",
        suggestion="约定互负债务经确认后方可抵销，或设置争议债权不得抵销的例外。",
        evidence_kw="抵销",
    ),
    ClauseSpec(
        clause_id="risk-09",
        text="买方逾期付款的，卖方有权解除合同并没收买方已支付的全部款项。",
        expect_finding=False,
    ),
    ClauseSpec(
        clause_id="risk-10",
        text="发票开具方式由买卖双方根据交易需要另行约定，卖方应按买方指示开具。",
        expect_finding=False,
    ),
]

NORMAL_CLAUSES: list[ClauseSpec] = [
    ClauseSpec(
        clause_id="ok-01",
        text="买方应在每批次货物验收合格后30日内，凭卖方开具的增值税专用发票支付该批次货款。",
        expect_finding=False,
    ),
    ClauseSpec(
        clause_id="ok-02",
        text="卖方应于2027年3月31日前将全部货物一次性交付至买方指定地点，运输费用由卖方承担。",
        expect_finding=False,
    ),
    ClauseSpec(
        clause_id="ok-03",
        risk_type="acceptance_risk",
        text="买方应在货物到达后10个工作日内完成验收，验收合格后出具书面验收单。",
        expect_finding=True,
        risk_level="low",
        summary="验收期10个工作日处于制度下限，建议补充验收标准清单。",
        suggestion="附验收标准清单并约定逾期未验收的书面确认机制。",
        evidence_kw="10个工作日",
    ),
    ClauseSpec(
        clause_id="ok-04",
        text="卖方逾期交付的，每逾期一日按合同总价的0.5‰向买方支付违约金，累计不超过合同总价的10%。",
        expect_finding=False,
    ),
    ClauseSpec(
        clause_id="ok-05",
        text="买方逾期付款的，每逾期一日按未付金额的0.5‰向卖方支付违约金。",
        expect_finding=False,
    ),
    ClauseSpec(
        clause_id="ok-06",
        text="本合同价款为含税价，增值税税率为13%，卖方应开具增值税专用发票。",
        expect_finding=False,
    ),
    ClauseSpec(
        clause_id="ok-07",
        risk_type="delivery_risk",
        text="货物毁损、灭失的风险自卖方将货物交付买方时起转移。",
        expect_finding=True,
        risk_level="low",
        summary="风险转移时点约定清晰，建议同步明确交付地点以降低争议。",
        suggestion="在交付条款中明确交付地点与交付凭证。",
        evidence_kw="毁损、灭失的风险",
    ),
    ClauseSpec(
        clause_id="ok-08",
        text="验收标准按国家标准及双方书面确认的技术协议执行，标准不一致时以较高者为准。",
        expect_finding=False,
    ),
    ClauseSpec(
        clause_id="ok-09",
        text="卖方应在买方每期付款前开具合法有效的增值税专用发票，发票不合规的买方可暂缓付款且不承担违约责任。",
        expect_finding=False,
    ),
    ClauseSpec(
        clause_id="ok-10",
        text="预付款为合同总价的30%，在每期进度款中按20%比例扣回，验收合格后结清尾款。",
        expect_finding=False,
    ),
]

# 伪造法条注入场景：在 5 条已知风险条款上让模型只引用不存在的 id。
FABRICATED_CLAUSE_IDS: tuple[str, ...] = ("risk-01", "risk-02", "risk-03", "risk-04", "risk-05")

CONTRACT_META: dict[str, object] = {"contract_id": "contract-stage6", "version": 1}


def all_specs() -> list[ClauseSpec]:
    """20 条验收条款（10 风险 + 10 正常），顺序即条款序号。"""
    return [*RISK_CLAUSES, *NORMAL_CLAUSES]


def to_clause(spec: ClauseSpec, index: int) -> Clause:
    """ClauseSpec -> ReviewState.clauses 的元素。"""
    return Clause(clause_id=spec.clause_id, index=index, text=spec.text)


def specs_by_id(specs: list[ClauseSpec]) -> dict[str, ClauseSpec]:
    return {spec.clause_id: spec for spec in specs}


# ---------------------------------------------------------------------------
# Stage 7 并行验收语料：50 条款（6 专家各 8 条 + 2 条跨专家重叠加名条款）。
# 条款文本按各专家范畴关键词构造（绝大多数单归属）；x-01/x-02 故意同时命中两个
# 专家的范畴，用于验证 merge_findings 对「同 clause_id 同 risk_type」的幂等去重。
# 行格式：(clause_id, text, expect_finding, risk_level, risk_type, summary, suggestion, evidence_kw)
# ---------------------------------------------------------------------------

_STAGE7_ROWS: tuple[tuple[str, str, bool, str, str, str, str, str], ...] = (
    # ---- commercial：付款/交付/验收/发票（high）----
    (
        "c-01",
        "买方应在合同签订后3个工作日内支付合同总价的100%作为预付款，该款项在任何情况下不予退还。",
        True, "high", "payment_risk",
        "预付100%且无返还与扣回机制，买方资金风险极高。",
        "改为「预付30%+到货验收合格后凭增值税专用发票支付尾款」的分期结构，并约定预付款扣回机制。",
        "100%",
    ),
    (
        "c-02",
        "买方应在每批次货物验收合格后30日内，凭卖方开具的增值税专用发票支付该批次货款。",
        False,
        "",
        "",
        "",
        "",
        "",
    ),
    (
        "c-03",
        "货款的具体支付时间由双方另行协商确定，协商不成的，买方有权无限期顺延支付。",
        True, "medium", "payment_risk",
        "付款时间约定不明且买方可无限期顺延，回款无保障。",
        "约定明确付款节点（如验收合格后30日内），删除无限期顺延表述。",
        "另行协商",
    ),
    (
        "c-04",
        "卖方应于2027年3月31日前将全部货物一次性交付至买方指定地点，运输费用由卖方承担。",
        False,
        "",
        "",
        "",
        "",
        "",
    ),
    (
        "c-05",
        "货物到达后3日内，买方未提出书面异议的，视为验收合格，买方不得再行主张任何质量权利。",
        True, "high", "acceptance_risk",
        "检验期仅3日且绝对化视为条款，买方质量救济被剥夺。",
        "检验期延长至不少于10个工作日，视为条款保留隐蔽瑕疵例外并显著提示。",
        "3日",
    ),
    (
        "c-06",
        "买方应在货物到达后10个工作日内完成验收，验收合格后出具书面验收单。",
        False,
        "",
        "",
        "",
        "",
        "",
    ),
    (
        "c-07",
        "卖方应开具增值税普通发票，税率为3%，品名按买方要求填写。",
        True, "high", "invoice_risk",
        "发票类型与品名约定违规，存在税务与结算风险。",
        "改为开具税率13%的增值税专用发票，品名与业务实质一致。",
        "增值税普通发票",
    ),
    (
        "c-08",
        "本合同价款为含税价，增值税税率为13%，卖方应开具增值税专用发票。",
        False,
        "",
        "",
        "",
        "",
        "",
    ),
    # ---- subject：主体资格/授权/签署（high）----
    (
        "s-01",
        "甲方签约主体资格存在瑕疵，其营业执照登记的经营范围不包含本合同项下业务。",
        True, "high", "qualification_risk",
        "甲方签约主体资格与经营范围不匹配，合同效力存在瑕疵风险。",
        "签约前核验营业执照与资质文件，并要求甲方出具经营范围包含本业务的承诺。",
        "营业执照",
    ),
    (
        "s-02",
        "甲方应确保其签约主体资格合法有效，并在本合同生效前提供最新营业执照复印件。",
        False,
        "",
        "",
        "",
        "",
        "",
    ),
    (
        "s-03",
        "签署本合同的授权代表未取得甲方书面授权，其签章对甲方不产生约束力。",
        True, "high", "authorization_risk",
        "授权代表权限不明，签署行为可能对甲方不发生效力。",
        "要求补充授权委托书并明确代表权限范围与期限。",
        "签章",
    ),
    ("s-04", "双方法定代表人应在合同文本上签字并加盖公司公章。", False, "", "", "", "", ""),
    (
        "s-05",
        "甲方名称、地址、联系方式以本合同首部所载为准，变更应提前书面通知乙方。",
        False,
        "",
        "",
        "",
        "",
        "",
    ),
    (
        "s-06",
        "甲方发生分立、合并或承继的，未约定本合同权利义务的承继安排。",
        True, "medium", "succession_risk",
        "主体变动时权利义务承继安排缺失，合同履行存在断档风险。",
        "约定分立合并时合同权利义务自动由承继方全额承继并书面通知对方。",
        "分立",
    ),
    ("s-07", "甲方应保持其签约主体资格在合同履行期内持续有效。", False, "", "", "", "", ""),
    (
        "s-08",
        "任何一方签约主体资格丧失的，另一方可书面通知停止履行本协议。",
        False,
        "",
        "",
        "",
        "",
        "",
    ),
    # ---- liability：违约/赔偿/免责（high）----
    (
        "l-01",
        "卖方逾期供货的，每逾期一日按合同总价的0.001%向买方计付违约金。",
        True, "medium", "breach_risk",
        "逾期违约金比例过低，对卖方无约束力。",
        "提高至不低于日万分之三，并设置以合同总价为基准的责任上限。",
        "0.001%",
    ),
    (
        "l-02",
        "卖方逾期供货的，每逾期一日按合同总价的0.5‰向买方计付违约金，累计不超过合同总价的10%。",
        False,
        "",
        "",
        "",
        "",
        "",
    ),
    (
        "l-03",
        "因不可抗力导致的损失双方互不承担赔偿责任，但卖方应在不可抗力消除后无限期免责。",
        True, "medium", "force_majeure_risk",
        "不可抗力免责范围过宽且无通知与减损义务约束。",
        "约定不可抗力通知时限、证明要求及消除后的继续履行义务。",
        "不可抗力",
    ),
    (
        "l-04",
        "买方中途退订的，已交纳款项不予退还，卖方有权继续履行并要求买方赔偿损失。",
        False,
        "",
        "",
        "",
        "",
        "",
    ),
    (
        "l-05",
        "卖方对任何损失均不承担赔偿责任，包括卖方重大过失造成的损失。",
        True, "high", "disclaimer_risk",
        "免责条款覆盖重大过失，依法可能被认定无效。",
        "排除故意与重大过失情形下的免责，明确责任上限与例外。",
        "任何损失",
    ),
    (
        "l-06",
        "任何一方严重违约的，另一方可书面通知解除合同，并要求违约方赔偿损失。",
        False,
        "",
        "",
        "",
        "",
        "",
    ),
    (
        "l-07",
        "违约金不足以弥补买方实际损失的，卖方还应就差额部分承担赔偿责任。",
        False,
        "",
        "",
        "",
        "",
        "",
    ),
    ("l-08", "卖方未按约定履行补救措施的，应承担由此产生的赔偿责任。", False, "", "", "", "", ""),
    # ---- ip：知识产权/保密（low，预算超限可跳过）----
    (
        "p-01",
        "甲方在履约过程中形成的一切改进成果及职务成果归乙方单独所有，甲方不得主张任何权利。",
        True, "high", "ip_ownership_risk",
        "履约改进成果全部归乙方，甲方无任何对价与保留。",
        "约定双方共同改进的权属比例或甲方背景技术的保留范围。",
        "职务成果",
    ),
    ("p-02", "双方在履约前已各自拥有的知识产权仍归原权利人所有。", False, "", "", "", "", ""),
    (
        "p-03",
        "甲方使用开源软件构建标的物，未约定开源组件清单与兼容性审查义务。",
        True, "medium", "open_source_risk",
        "开源组件合规义务缺失，存在许可证污染与披露风险。",
        "约定开源组件清单、许可证审查及传染性开源的使用限制。",
        "开源",
    ),
    (
        "p-04",
        "乙方对其提供的技术资料享有知识产权，甲方仅限于内部使用。",
        False,
        "",
        "",
        "",
        "",
        "",
    ),
    (
        "p-05",
        "双方应对履约过程中知悉的对方商业秘密严格保密，保密期限为合同终止后三年。",
        False,
        "",
        "",
        "",
        "",
        "",
    ),
    (
        "p-06",
        "甲方保留其背景专利的所有权，合同终止后乙方不得继续实施该专利。",
        False,
        "",
        "",
        "",
        "",
        "",
    ),
    ("p-07", "任何一方侵犯对方知识产权的，应承担由此产生的全部责任。", False, "", "", "", "", ""),
    ("p-08", "甲方保证标的物不侵犯任何第三方的商标权与著作权。", False, "", "", "", "", ""),
    # ---- data：数据/个人信息（low）----
    (
        "d-01",
        "甲方为履行本合同收集乙方员工个人信息的，未约定处理目的、期限与安全措施。",
        True, "high", "data_processing_risk",
        "个人信息处理的目的期限与安全措施均未约定。",
        "按最小必要原则约定处理目的、期限、加密与访问控制措施。",
        "个人信息",
    ),
    (
        "d-02",
        "双方涉及个人信息处理的，应遵守适用的信息保护法律法规并采取加密与脱敏措施。",
        False,
        "",
        "",
        "",
        "",
        "",
    ),
    (
        "d-03",
        "甲方可将履约过程中获取的原始数据跨境传输至境外服务器而未作安全评估。",
        True, "high", "cross_border_risk",
        "数据跨境传输未约定安全评估与合规路径。",
        "约定跨境传输的安全评估、标准合同条款及接收方保护义务。",
        "跨境",
    ),
    (
        "d-04",
        "甲方应建立数据备份与日志留存机制，日志保存期限不少于六个月。",
        False,
        "",
        "",
        "",
        "",
        "",
    ),
    (
        "d-05",
        "发生数据泄露事件的，责任方应在24小时内通知对方并启动应急处置。",
        False,
        "",
        "",
        "",
        "",
        "",
    ),
    (
        "d-06",
        "甲方以明文存储乙方账号与密码，未约定访问权限分级。",
        True, "medium", "credential_risk",
        "账号口令明文存储且无权限分级，泄露风险极高。",
        "约定加密存储、最小权限与定期审计要求。",
        "账号",
    ),
    (
        "d-07",
        "双方处理个人信息应取得相关个人的有效同意并告知处理规则。",
        False,
        "",
        "",
        "",
        "",
        "",
    ),
    (
        "d-08",
        "甲方应通过网络安全等级保护（等保）三级测评并承担相应费用。",
        False,
        "",
        "",
        "",
        "",
        "",
    ),
    # ---- compliance：争议解决/合规（low）----
    (
        "m-01",
        "本合同争议由甲方所在地法院管辖，未约定仲裁条款。",
        True, "medium", "jurisdiction_risk",
        "管辖约定偏向甲方所在地，乙方诉讼成本高。",
        "改为合同签订地或被告所在地中立管辖，或约定明确的仲裁机构。",
        "管辖",
    ),
    (
        "m-02",
        "因本合同引起的争议，双方应友好协商解决；协商不成的，提交合同签订地仲裁委员会仲裁。",
        False,
        "",
        "",
        "",
        "",
        "",
    ),
    (
        "m-03",
        "本合同适用法律约定不明，且争议解决方式同时约定了诉讼与仲裁。",
        True, "high", "applicable_law_risk",
        "适用法律不明且争议解决方式或裁或审并存，条款可能无效。",
        "明确适用法律，删除冲突的争议解决表述，保留单一确定路径。",
        "适用法律",
    ),
    (
        "m-04",
        "双方应遵守反垄断与反不正当竞争法律法规，不得实施垄断行为。",
        False,
        "",
        "",
        "",
        "",
        "",
    ),
    (
        "m-05",
        "任何一方违反反腐败与廉洁义务的，另一方可书面通知停止合作。",
        False,
        "",
        "",
        "",
        "",
        "",
    ),
    (
        "m-06",
        "甲方出口受管制物项未约定出口管制合规审查义务。",
        True, "medium", "export_control_risk",
        "受管制物项出口缺乏合规审查安排。",
        "约定出口管制清单核查、许可申请与合规承诺义务。",
        "出口管制",
    ),
    ("m-07", "双方应接受监管部门的监督检查，并配合提供相关材料。", False, "", "", "", "", ""),
    ("m-08", "因行政处罚产生的争议，双方约定先行行政复议。", False, "", "", "", "", ""),
    # ---- 跨专家重叠加名条款：ip+compliance / data+compliance 各命中一次 ----
    (
        "x-01",
        "双方应对履约过程中知悉的对方商业秘密承担保密义务，保密期限为合同终止后三年；因保密义务产生的争议提交合同签订地法院管辖。",
        True, "medium", "confidentiality_dispute_risk",
        "保密义务与争议解决条款叠加，归属与管辖均需明确。",
        "明确保密信息范围、违约责任及争议解决路径的唯一性。",
        "保密义务",
    ),
    (
        "x-02",
        "甲方处理履约数据应符合信息保护要求；因数据处理引发的争议由被告所在地法院管辖。",
        True, "medium", "data_dispute_risk",
        "数据处理合规与争议解决条款叠加，义务与管辖需对应。",
        "明确数据保护义务标准并统一争议解决路径。",
        "数据处理",
    ),
)


def stage7_corpus() -> list[ClauseSpec]:
    """50 条款并行验收语料（顺序即条款序号）。"""
    return [_spec_from_row(row) for row in _STAGE7_ROWS]


def _spec_from_row(row: tuple[str, str, bool, str, str, str, str, str]) -> ClauseSpec:
    clause_id, text, expect, level, risk_type, summary, suggestion, kw = row
    return ClauseSpec(
        clause_id=clause_id,
        text=text,
        expect_finding=expect,
        risk_level=level,
        risk_type=risk_type,
        summary=summary,
        suggestion=suggestion,
        evidence_kw=kw,
    )


# ---------------------------------------------------------------------------
# Stage 8 Critic 打回重审语料：三条路径各有单据归属（subject 一家），
# 避免多专家同键产出干扰断言——
#   c-01/s-01/l-05：首轮即通过（不应被打回）；
#   r-02：首轮 evidence 不落地（关键词不在原文）-> 被 Critic b) 打回 ->
#         重审改用真实关键词 -> 修正通过（闭环修复路径）；
#   r-03：首轮与重审的关键词都不在原文 -> 永远失败 -> 2 轮后标 unverified
#         放行（轮数上限收敛路径，验证不挂死）。
# 行格式：(clause_id, text, expect_finding, risk_level, risk_type, summary,
#          suggestion, evidence_kw, rework_evidence_kw)
# ---------------------------------------------------------------------------

_STAGE8_ROWS: tuple[tuple[str, str, bool, str, str, str, str, str, str], ...] = (
    (
        "c-01",
        "买方应在合同签订后3个工作日内支付合同总价的100%作为预付款，该款项在任何情况下不予退还。",
        True, "high", "payment_risk",
        "预付100%且无返还与扣回机制，买方资金风险极高。",
        "改为「预付30%+到货验收合格后凭增值税专用发票支付尾款」的分期结构，并约定预付款扣回机制。",
        "100%", "",
    ),
    (
        "s-01",
        "甲方签约主体资格存在瑕疵，其营业执照登记的经营范围不包含本合同项下业务。",
        True, "high", "qualification_risk",
        "甲方签约主体资格与经营范围不匹配，合同效力存在瑕疵风险。",
        "签约前核验营业执照与资质文件，并要求甲方出具经营范围包含本业务的承诺。",
        "营业执照", "",
    ),
    (
        "l-05",
        "卖方对任何损失均不承担赔偿责任，包括卖方重大过失造成的损失。",
        True, "high", "disclaimer_risk",
        "免责条款覆盖重大过失，依法可能被认定无效。",
        "排除故意与重大过失情形下的免责，明确责任上限与例外。",
        "任何损失", "",
    ),
    (
        "r-02",
        "甲方营业执照登记的经营范围不包含本合同项下业务，签约主体资格存在瑕疵。",
        True, "medium", "qualification_risk",
        "甲方经营范围与签约业务不匹配，主体资质存在瑕疵。",
        "签约前核验营业执照，并要求甲方出具经营范围包含本业务的承诺。",
        # 首轮证据关键词不在原文 -> Critic b) 打回；重审改用真实关键词即修正。
        "不存在的关键词", "营业执照",
    ),
    (
        "r-03",
        "甲方发生分立、合并或承继的，未约定本合同权利义务的承继安排。",
        True, "medium", "succession_risk",
        "主体变动时权利义务承继安排缺失，合同履行存在断档风险。",
        "约定分立合并时合同权利义务自动由承继方全额承继并书面通知对方。",
        # 首轮与重审的关键词都不在原文 -> 永远无法通过 b)，用于验证轮数上限。
        "不存在的关键词", "同样不存在的关键词",
    ),
)


def stage8_corpus() -> list[ClauseSpec]:
    """Stage 8 Critic 语料：3 条通过 + 1 条可修复 + 1 条永远失败。"""
    return [
        ClauseSpec(
            clause_id=clause_id,
            text=text,
            expect_finding=expect,
            risk_level=level,
            risk_type=risk_type,
            summary=summary,
            suggestion=suggestion,
            evidence_kw=kw,
            rework_evidence_kw=rework_kw,
        )
        for clause_id, text, expect, level, risk_type, summary, suggestion, kw, rework_kw
        in _STAGE8_ROWS
    ]
