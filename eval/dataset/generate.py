"""Stage 13 数据集生成器：120 份 docx 合同 + dataset.jsonl + gold_citations.jsonl。

为什么独立脚本：评测资产不属于生产代码，但必须与生产切分器（app.parse）同一条解析链路，
否则金标 clause_id / span 与系统产出对不上、指标失真。生成流程：

1. 按固定种子构造 120 份合同：每份 N 条「第X条」条款，混合 风险 / 陷阱 / 正常 三类；
2. 写出 docx（python-docx），再用生产 loader+splitter 真实解析，拿到 clause_id / 偏移；
3. 金标按「真实解析结果」回填：风险条款的 clause_id、span_hint=[start,end] 取自切分器输出，
   保证与系统 findings 的 clause_id 完全可比（span IoU 才有意义）。

风险条款只含单一风险关键词（且只命中该专家范畴），陷阱条款故意含关键词但语义安全
（被 naive examiner 误报，用于让误报率指标非平凡），正常条款不含任何 scope_keywords。
生成前用 production 的 scope_keywords 做断言，杜绝关键词串味导致的意外路由。
"""

from __future__ import annotations

import json
import random
from pathlib import Path

from docx import Document

from app.agents.registry import EXPERTS
from app.parse.loader import load_document
from app.parse.splitter import split_document

from .risk_catalog import (
    ENABLED_RISK_TYPES,
    RISK_BY_TYPE,
    RiskDef,
    source_for,
)

SEED = 20261026
N_CONTRACTS = 120
DATASET_DIR = Path(__file__).resolve().parent
CONTRACTS_DIR = DATASET_DIR / "contracts"
DATASET_JSONL = DATASET_DIR / "dataset.jsonl"
GOLD_JSONL = DATASET_DIR / "gold_citations.jsonl"

# 全部专家 scope_keywords 并集：正常/陷阱/风险句子不得在非预期处命中它（除自身关键词）。
ALL_SCOPE: set[str] = {kw for spec in EXPERTS for kw in spec.scope_keywords}

CONTRACT_TITLES: tuple[str, ...] = (
    "货物采购合同",
    "服务外包合同",
    "设备租赁合同",
    "技术开发合同",
    "仓储物流服务合同",
    "咨询服务合同",
)

PREAMBLE = (
    "甲乙双方本着平等、自愿、公平、诚实信用的原则，经友好协商，"
    "就下列事项达成一致意见，订立本合同，以资共同遵守。"
)

# 每个启用风险类型 2 条含关键词的句子；只含该关键词，避免串味到其它专家范畴。
RISK_SENTENCES: dict[str, tuple[str, str]] = {
    "payment_risk": (
        "买方应支付合同总价100%作为预付款且该款项在任何情况下不予退还。",
        "买方应于签约后支付预付款项但无需提供任何扣回或返还保障。",
    ),
    "delivery_risk": (
        "卖方将尽快安排生产并交付货物，具体交付时间以卖方书面通知为准。",
        "货物交付地点由卖方自行确定，买方应到指定地点自行提货。",
    ),
    "acceptance_risk": (
        "货物到达后3日内买方未提出书面异议的视为验收合格且不得再主张质量权利。",
        "验收标准由买方单方确定并可适时调整，卖方不得提出异议。",
    ),
    "invoice_risk": (
        "卖方应开具增值税普通发票税率为3%且品名按买方要求填写。",
        "发票开具方式由卖方按买方指示开具，可不提供增值税专用发票。",
    ),
    "qualification_risk": (
        "甲方签约主体经营范围不包含本合同项下业务但仍以自身名义签署。",
        "甲方超出经营范围从事本合同业务且未补正相应资质文件。",
    ),
    "authorization_risk": (
        "签署本合同的授权代表未取得甲方书面授权，其签章对甲方不生效。",
        "甲方授权代表权限不明，超出权限签署的条款由甲方自行追认。",
    ),
    "succession_risk": (
        "甲方发生分立合并或承继的未约定本合同权利义务由承继方全额承继。",
        "甲方主体发生承继变动的未通知对方确认承继安排与责任归属。",
    ),
    "breach_risk": (
        "卖方逾期履约的每逾期一日按合同总价0.001%向买方计付违约金。",
        "任何一方轻微违约的对方即可主张全额赔偿且不考虑实际损失。",
    ),
    "force_majeure_risk": (
        "因不可抗力导致的损失双方互不承担赔偿责任且卖方免除全部责任无限期。",
        "发生不可抗力后卖方免除责任且无需在合理期限内通知买方或减损。",
    ),
    "disclaimer_risk": (
        "卖方对任何损失均不承担赔偿责任且主张免责包括重大过失造成的损失。",
        "本合同项下卖方责任全部免除且主张免责，买方不得就任何损害请求赔偿。",
    ),
    "termination_risk": (
        "买方可随时单方解除合同且无需说明理由亦不承担任何补偿责任。",
        "卖方有权在任意时间解除合同并不退还已收取的任何款项。",
    ),
    "ip_ownership_risk": (
        "甲方履约中形成的职务成果及改进成果归乙方单独所有甲方无权利。",
        "乙方提供的职务成果权属归乙方所有甲方不得主张任何权利。",
    ),
    "open_source_risk": (
        "甲方使用开源软件构建标的物未约定开源组件清单与许可证审查义务。",
        "乙方提供的成果物包含开源代码但未披露许可证类型及兼容性要求。",
    ),
    "data_processing_risk": (
        "甲方为履行本合同收集乙方员工个人信息未约定处理目的期限与安全措施。",
        "双方处理个人信息未取得有效同意且未告知处理规则与保存期限。",
    ),
    "cross_border_risk": (
        "甲方可将履约获取的数据跨境传输至境外服务器且未作安全评估。",
        "本合同约定数据可跨境提供而未约定标准合同与接收方义务。",
    ),
    "credential_risk": (
        "甲方以明文存储乙方账号与密码且未约定访问权限分级管理措施。",
        "双方系统账号口令以明文保存于共享文档中且无定期审计要求。",
    ),
    "jurisdiction_risk": (
        "本合同争议由甲方所在地法院管辖且未约定其他争议解决方式。",
        "因本合同引起的诉讼由甲方住所地法院专属管辖乙方诉讼成本高。",
    ),
    "applicable_law_risk": (
        "本合同适用法律约定不明且同时约定了诉讼与仲裁两种争议解决方式。",
        "本合同争议解决应明确适用法律并保留单一确定的解决路径。",
    ),
    "export_control_risk": (
        "甲方出口受管制物项未约定出口管制合规审查与许可申请义务。",
        "本合同涉及受出口管制的技术出口但未约定合规承诺与清单核查。",
    ),
}

# 陷阱句子：含关键词但语义安全（被 naive examiner 误报）；用于让误报率非平凡。
TRAP_SENTENCES: dict[str, tuple[str, str]] = {
    "payment_risk": (
        "本合同不采用预付款方式，货款于验收合格后30日内凭发票支付。",
        "双方确认不存在预付款约定，所有款项均按进度分期支付。",
    ),
    "delivery_risk": (
        "卖方应于约定期限内交付货物，逾期按约定承担违约责任。",
        "交付时间与地点由双方在订单中明示约定，不得单方变更。",
    ),
    "acceptance_risk": (
        "买方应在货物到达后10个工作日内完成验收并出具书面验收单。",
        "验收标准按国家标准及双方确认的技术协议执行。",
    ),
    "invoice_risk": (
        "卖方应开具税率为13%的增值税专用发票，品名与业务实质一致。",
        "发票不合规的买方可暂缓付款且不承担违约责任。",
    ),
    "qualification_risk": (
        "甲方签约前应核验营业执照与资质文件并在生效前提供复印件。",
        "甲方保证其签约主体资格合法有效且在履约期内持续有效。",
    ),
    "authorization_risk": (
        "签署本合同的代表应出具书面授权委托书并明确权限范围。",
        "双方法定代表人应在合同文本上签字并加盖公司公章。",
    ),
    "succession_risk": (
        "甲方发生分立合并的其合同权利义务由承继方书面通知后全额承继。",
        "任何一方主体变动的应提前书面通知对方并确认义务承继安排。",
    ),
    "breach_risk": (
        "卖方逾期交付的每逾期一日按合同总价0.5‰计付违约金且设责任上限。",
        "任何一方严重违约的另一方可书面通知解除合同并要求赔偿。",
    ),
    "force_majeure_risk": (
        "不可抗力发生后受影响方应在合理期限内通知对方并减损。",
        "因不可抗力不能履行的应及时提供证明并部分或全部免除责任。",
    ),
    "disclaimer_risk": (
        "因故意或重大过失造成的损失责任不得事先免除或限制。",
        "责任上限以合同总价为基准，故意与重大过失情形除外。",
    ),
    "termination_risk": (
        "任何一方解除合同的应提前书面通知并按约定承担相应责任。",
        "本合同终止后双方仍应履行保密与结算等后续义务。",
    ),
    "ip_ownership_risk": (
        "双方履行前已各自拥有的知识产权仍归原权利人所有。",
        "甲方保留其背景专利所有权合同终止后乙方不得实施。",
    ),
    "open_source_risk": (
        "乙方对其提供的技术资料享有知识产权甲方仅限于内部使用。",
        "双方应对开源组件清单与许可证进行审查并合规使用。",
    ),
    "data_processing_risk": (
        "双方处理个人信息应取得有效同意并告知处理规则与期限。",
        "发生数据泄露的责任方应在24小时内通知对方并启动处置。",
    ),
    "cross_border_risk": (
        "双方涉及个人信息跨境的应按规完成安全评估并签标准合同。",
        "甲方处理数据应符合信息保护要求并采取加密与脱敏措施。",
    ),
    "credential_risk": (
        "甲方应建立账号权限分级与加密存储机制并定期审计。",
        "双方处理个人信息应取得相关个人的有效同意并告知规则。",
    ),
    "jurisdiction_risk": (
        "因本合同引起的争议双方协商不成提交合同签订地仲裁。",
        "本合同争议由合同签订地法院管辖并约定明确仲裁机构。",
    ),
    "applicable_law_risk": (
        "本合同适用中华人民共和国法律且争议解决路径唯一。",
        "因本合同发生的争议按约定明确适用的法律解决。",
    ),
    "export_control_risk": (
        "涉及受管制物项的出口应完成合规审查与许可申请义务。",
        "甲方出口业务应遵守出口管制清单核查与合规承诺。",
    ),
}

# 正常句子：不含任何 scope_keywords（生成前断言）。
NEUTRAL: tuple[str, ...] = (
    "双方应按照本合同约定的内容办理相关手续并相互配合。",
    "本合同文本一式两份双方各执一份具有同等效力。",
    "本合同未尽事宜由双方另行协商并以书面形式补充。",
    "双方确认已充分理解本合同全部条款的含义与后果。",
    "一方需要变更收件信息的应当提前以书面方式通知对方。",
    "双方应妥善保管本合同正本及相关往来资料。",
    "本合同的解释应当符合公平与诚信的原则。",
    "任何一方不得将本合同项下权利义务擅自转让给第三方。",
    "双方以书面方式进行沟通确认与文件交换。",
    "本合同各条款标题仅为方便阅读不影响条款解释。",
    "双方承诺向对方提供的资料真实准确且完整。",
    "本合同项下通知以专人送达或挂号信方式发出为有效。",
    "双方均应严格保守在履约过程中知悉的对方经营信息。",
    "本合同的订立应当采用书面形式并经双方签字确认。",
)


def _assert_safe_neutral() -> None:
    """正常句子不得命中任何 scope_keywords（否则会意外路由到专家产生伪发现）。"""
    for text in NEUTRAL:
        hits = [kw for kw in ALL_SCOPE if kw in text]
        if hits:
            raise AssertionError(f"中性句子命中 scope_keyword {hits}: {text}")


def _assert_risk_sentences() -> None:
    """风险句子必须含自身关键词、且不含其它风险关键词（保证单一检测、召回干净）。"""
    for rtype, sentences in RISK_SENTENCES.items():
        kw = RISK_BY_TYPE[rtype].keyword
        for text in sentences:
            if kw not in text:
                raise AssertionError(f"风险句子缺关键词 {kw}: {text}")
            other = [
                r.keyword
                for r in (RISK_BY_TYPE[t] for t in ENABLED_RISK_TYPES)
                if r.keyword in text and r.keyword != kw
            ]
            if other:
                raise AssertionError(f"风险句子 {rtype} 串味关键词 {other}: {text}")


def _build_clauses(rng: random.Random, contract_no: int) -> list[dict[str, object]]:
    """构造一份合同的条款序列：风险 / 陷阱 / 正常 三类按概率分布排布。"""
    n = rng.randint(24, 34)
    risk_types_cycle = list(ENABLED_RISK_TYPES)
    rng.shuffle(risk_types_cycle)
    risk_count = rng.randint(3, 5)
    trap_count = min(rng.randint(2, 4), n - risk_count - 2)

    # 选 trap 用的风险类型（与 risk 不重复，保证陷阱是独立的误报来源）。
    trap_pool = [t for t in risk_types_cycle if t not in risk_types_cycle[:risk_count]]
    rng.shuffle(trap_pool)

    # 给每条 clause 分配角色，再按 1..n 编号。
    roles: list[dict[str, object]] = []
    risk_idx = 0
    trap_idx = 0
    for _ in range(n):
        if risk_idx < risk_count:
            rtype = risk_types_cycle[risk_idx]
            risk_idx += 1
            roles.append({"kind": "risk", "risk_type": rtype})
        elif trap_idx < trap_count:
            rtype = trap_pool[trap_idx % len(trap_pool)]
            trap_idx += 1
            roles.append({"kind": "trap", "risk_type": rtype})
        else:
            roles.append({"kind": "neutral", "risk_type": None})
    rng.shuffle(roles)

    clauses: list[dict[str, object]] = []
    for i, role in enumerate(roles, start=1):
        no = f"第{i}条"
        kind = role["kind"]
        rtype = role["risk_type"]
        if kind == "risk":
            body = rng.choice(RISK_SENTENCES[rtype])  # type: ignore[index]
        elif kind == "trap":
            body = rng.choice(TRAP_SENTENCES[rtype])  # type: ignore[index]
        else:
            body = rng.choice(NEUTRAL)
        clauses.append(
            {"no": no, "text": f"{no} {body}", "kind": kind, "risk_type": rtype}
        )
    return clauses


def _write_docx(contract_id: str, title: str, clauses: list[dict[str, object]]) -> Path:
    doc = Document()
    doc.add_paragraph(title)
    doc.add_paragraph(PREAMBLE)
    for clause in clauses:
        doc.add_paragraph(str(clause["text"]))
    out = CONTRACTS_DIR / f"{contract_id}.docx"
    doc.save(str(out))
    return out


def _parse_clauses(path: Path) -> dict[str, dict[str, object]]:
    """真实解析 docx，按 no（第X条）建索引；返回 no -> {clause_id, start, end, text}。"""
    document = load_document(path)
    result = split_document(document)
    by_no: dict[str, dict[str, object]] = {}
    for clause in result.clauses:
        by_no[clause.no] = {
            "clause_id": clause.id,
            "start": clause.start,
            "end": clause.end,
            "text": clause.text,
        }
    return by_no


def generate(force: bool = False) -> None:
    """生成 120 份合同 + dataset.jsonl + gold_citations.jsonl。幂等（已存在且非 force 则跳过）。"""
    _assert_safe_neutral()
    _assert_risk_sentences()
    if DATASET_JSONL.exists() and not force:
        print(f"[generate] 已存在 {DATASET_JSONL.name}，跳过（用 --force 重建）")
        return
    CONTRACTS_DIR.mkdir(parents=True, exist_ok=True)

    rng = random.Random(SEED)
    records: list[dict[str, object]] = []
    all_risk_annotations: list[dict[str, object]] = []

    for c in range(1, N_CONTRACTS + 1):
        contract_id = f"cg-{c:04d}"
        title = f"{CONTRACT_TITLES[(c - 1) % len(CONTRACT_TITLES)]}（样本{c}）"
        clauses = _build_clauses(rng, c)
        path = _write_docx(contract_id, title, clauses)
        parsed = _parse_clauses(path)

        gold_clauses: list[dict[str, object]] = []
        annotations: list[dict[str, object]] = []
        for clause in clauses:
            no = str(clause["no"])
            parsed_info = parsed.get(no)
            if parsed_info is None:
                raise AssertionError(f"解析缺失条款 {contract_id} {no}")
            cid = str(parsed_info["clause_id"])
            span = [int(parsed_info["start"]), int(parsed_info["end"])]
            kind = clause["kind"]
            rtype = clause["risk_type"]
            is_risk = kind == "risk"
            # span_hint 取「风险句子」在全文中的区间（定位指标的严格口径）：
            # 系统侧定位是整条条款 span，句子是条款的主体但不含编号前缀，
            # IoU 因此非平凡而非恒等于 1。
            clause_start = span[0]
            clause_text = str(parsed_info["text"])
            body = str(clause["text"])[len(no) + 1 :]
            offset = clause_text.find(body)
            risk_span = (
                [clause_start + offset, clause_start + offset + len(body)]
                if offset >= 0
                else span
            )
            gold_clauses.append(
                {
                    "clause_id": cid,
                    "clause_no": no,
                    "is_risk": is_risk,
                    "risk_type": rtype,
                    "risk_level": (
                        RISK_BY_TYPE[rtype].default_level if is_risk else None
                    ),
                    "span_start": span[0],
                    "span_end": span[1],
                }
            )
            if is_risk:
                rd: RiskDef = RISK_BY_TYPE[rtype]  # type: ignore[assignment]
                annotations.append(
                    {
                        "clause_no": no,
                        "risk_type": rtype,
                        "risk_level": rd.default_level,
                        "span_hint": risk_span,
                    }
                )
                all_risk_annotations.append(
                    {
                        "contract_id": contract_id,
                        "clause_id": cid,
                        "risk_type": rtype,
                        "gold_source_id": source_for(rtype, correct=True),
                    }
                )
        records.append(
            {
                "contract_id": contract_id,
                "path": f"eval/dataset/contracts/{contract_id}.docx",
                "annotations": annotations,
                "gold_clauses": gold_clauses,
            }
        )

    DATASET_JSONL.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n",
        encoding="utf-8",
    )

    # 金标引用：从全部风险标注中均匀抽取 50 条（人工抽检口径），含正确 source_id。
    step = max(1, len(all_risk_annotations) // 50)
    gold: list[dict[str, object]] = []
    for idx in range(0, len(all_risk_annotations), step):
        if len(gold) >= 50:
            break
        gold.append(all_risk_annotations[idx])
    GOLD_JSONL.write_text(
        "\n".join(json.dumps(g, ensure_ascii=False) for g in gold) + "\n",
        encoding="utf-8",
    )

    total_risk = sum(len(r["annotations"]) for r in records)  # type: ignore[arg-type]
    print(
        f"[generate] 生成 {N_CONTRACTS} 份合同，风险标注 {total_risk} 条，"
        f"金标引用 {len(gold)} 条 -> {DATASET_JSONL.name} / {GOLD_JSONL.name}"
    )


if __name__ == "__main__":
    generate(force=True)
