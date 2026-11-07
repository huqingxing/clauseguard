"""E5 · 检索覆盖度测量（用户指令：纯检索指标，不依赖 LLM key）

目的：测 "gold 标注点的相关法条是否进检索 top-20" 的命中率 —— 纯检索指标，不涉及模型行为。
方法：绕开生产向量检索（需要 DashScope key），用同一份 pgvector 语料的 **BM25 稀疏检索**
（jieba + rank_bm25，零 embedding 调用）取 top-20。BM25 是生产混合检索的一路（retrieval.yaml
use_bm25=true），故其覆盖度直接反映"检索能否为某风险点找到相关法条"这一纯检索能力。

口径：对每个 gold 点（contract_id, clause_id, risk_type），用 risk_type 构造查询，BM25 top-20 中
只要有一条候选文本/标题命中该 risk_type 的领域关键词，即记一次命中。命中率 = 命中数 / 采样数。

输出：20 / 50 / 100 个标注点的命中率（确定性前缀采样），以及 per_risk 覆盖。

环境：只需 PG 在线（默认 DSN 见 config/retrieval.yaml，S5_context 策略），不需要 embeddings key。
     若想对比生产混合（dense+bm25）覆盖，本机有 key 时加 --use-dense（会调 embeddings）。

命令（沙箱 / 免 key）：
  PYTHONPATH='python-agent;.' python-agent/.venv/Scripts/python.exe lab/exp/attribution/e5_retrieval_coverage.py
本机有 key 想跑混合：
  PYTHONPATH='python-agent;.' CLAUSEGUARD_AGENT_LLM_API_KEY=xxx python-agent/.venv/Scripts/python.exe \
      lab/exp/attribution/e5_retrieval_coverage.py --use-dense
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
DS_PATH = ROOT / "eval" / "dataset" / "dataset.jsonl"
SAMPLE_SIZES = (20, 50, 100)
TOP_K = 20

# risk_type -> (中文名, 领域关键词)。关键词刻意收紧为"具体词"，避免"责任"等泛词虚高命中。
RISK_META = {
    "acceptance_risk": ("验收风险", ["验收标准", "单方", "异议", "抗辩", "验收"]),
    "applicable_law_risk": ("适用法律风险", ["适用法律", "准据法", "管辖法律", "法律适用"]),
    "authorization_risk": ("授权风险", ["授权", "权限", "代理", "代表权限", "签字权限"]),
    "breach_risk": ("违约风险", ["违约", "违约金", "违约责任", "损害赔偿"]),
    "credential_risk": ("资质风险", ["资质", "资格许可", "证照", "资质证书", "经营许可"]),
    "cross_border_risk": ("跨境风险", ["跨境", "境外", "涉外", "外汇", "海关", "进出口"]),
    "data_processing_risk": ("数据处理风险", ["个人信息", "数据处理", "隐私", "账号密码", "泄露", "数据安全"]),
    "delivery_risk": ("交付风险", ["交付", "物流", "运输", "货期", "迟延交付", "交货"]),
    "disclaimer_risk": ("免责风险", ["免责", "责任限制", "免责条款", "豁免"]),
    "export_control_risk": ("出口管制风险", ["出口管制", "制裁", "两用物项", "军品", "禁运"]),
    "force_majeure_risk": ("不可抗力风险", ["不可抗力", "自然灾害", "罢工", "疫情", "不可抗"]),
    "invoice_risk": ("发票风险", ["发票", "开票", "税率", "增值税", "进项"]),
    "ip_ownership_risk": ("知识产权风险", ["知识产权", "著作权", "专利", "商标", "权属", "侵权"]),
    "jurisdiction_risk": ("管辖风险", ["管辖", "仲裁", "诉讼", "法院", "地域管辖"]),
    "open_source_risk": ("开源风险", ["开源", "许可证", "GPL", "MIT", "传染性", "开源协议"]),
    "payment_risk": ("付款风险", ["付款", "预付", "进度款", "账期", "结算", "价款"]),
    "qualification_risk": ("主体资格风险", ["主体资格", "履约能力", "资质", "成立", "资格"]),
    "succession_risk": ("权利义务承继风险", ["承继", "权利义务转让", "主体变更", "并购", "概括转让"]),
    "termination_risk": ("解除终止风险", ["解除", "终止", "退出", "通知期", "解除条件"]),
}


def _load_gold_points() -> list[dict]:
    """取全部合同的 gold（is_risk 且 risk_type 非空），按 (contract_id, clause_id) 确定性排序。"""
    pts: list[dict] = []
    for line in open(DS_PATH, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        rec = json.loads(line)
        for gc in rec.get("gold_clauses", []):
            rt = gc.get("risk_type")
            if gc.get("is_risk") and rt:
                pts.append({"contract_id": rec.get("contract_id"),
                            "clause_id": gc.get("clause_id"), "risk_type": rt})
    pts.sort(key=lambda p: (p["contract_id"], p["clause_id"]))
    return pts


def _is_hit(hits: list, kws: list[str]) -> tuple[bool, str | None]:
    for h in hits:
        blob = (h.article or "") + " " + (h.text or "")
        for kw in kws:
            if kw in blob:
                return True, h.chunk_id
    return False, None


def _focused_query(cn: str, kws: list[str]) -> str:
    """领域聚焦查询：只用风险专属词。

    原因：模板带「法律法规/审查合同」等通用词后，BM25 被 合同/法律/法规 等
    ubiquitous 词主导，top-20 与查询无关（所有 risk_type 都返回同一批 chunk），
    指标失真。聚焦查询才真实反映「检索能否为某风险点找到相关法条」。
    """
    return f"{cn} " + " ".join(kws)


def _boilerplate_query(cn: str) -> str:
    """生产查询风格（含通用词），仅作对照，不参与主指标。"""
    return f"请检索与「{cn}」相关的法律法规与企业制度，用于审查合同中的{cn}风险。"


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--use-dense", action="store_true",
                    help="用生产混合检索(dense+bm25)，需要 embeddings key；默认仅 BM25(免key)")
    ap.add_argument("--k", type=int, default=TOP_K)
    args = ap.parse_args()

    logging.disable(logging.WARNING)
    from retrieval.api import get_service

    svc = get_service()
    strategy = svc.settings.index_strategy  # 生产默认 S5_context
    svc.warmup(strategy)

    pts = _load_gold_points()
    max_n = max(SAMPLE_SIZES)
    if len(pts) < max_n:
        print(f"[E5] 可用 gold 点仅 {len(pts)} < {max_n}，已用全部。")
    pts = pts[:max_n]

    rows = []
    per_risk: dict[str, list[int]] = {}      # [focused_covered, total]
    per_risk_boiler: dict[str, list[int]] = {}
    for p in pts:
        rt = p["risk_type"]
        cn, kws = RISK_META.get(rt, (rt, [rt]))
        q_focus = _focused_query(cn, kws)
        q_boiler = _boilerplate_query(cn)
        hits_f = svc.search(q_focus, k=args.k, use_bm25=True, use_dense=args.use_dense)
        hits_b = svc.search(q_boiler, k=args.k, use_bm25=True, use_dense=args.use_dense)
        hit_f, matched_f = _is_hit(hits_f, kws)
        hit_b, _ = _is_hit(hits_b, kws)
        per_risk.setdefault(rt, [0, 0]); per_risk[rt][0] += int(hit_f); per_risk[rt][1] += 1
        per_risk_boiler.setdefault(rt, [0, 0]); per_risk_boiler[rt][0] += int(hit_b); per_risk_boiler[rt][1] += 1
        rows.append({"contract_id": p["contract_id"], "clause_id": p["clause_id"],
                     "risk_type": rt, "top_k": len(hits_f),
                     "covered_focus": hit_f, "covered_boiler": hit_b,
                     "matched_chunk_id": matched_f})

    def _rate(n: int, key: str) -> dict:
        sub = rows[:n]
        cov = sum(1 for r in sub if r[key])
        return {"covered": cov, "total": n, "rate": round(cov / n, 4) if n else 0.0}

    coverage_at = {str(n): _rate(n, "covered_focus") for n in SAMPLE_SIZES if n <= len(rows)}
    coverage_boiler = {str(n): _rate(n, "covered_boiler") for n in SAMPLE_SIZES if n <= len(rows)}
    zero_rt = sorted(rt for rt, (c, t) in per_risk.items() if t > 0 and c == 0)

    method = "hybrid(dense+bm25)" if args.use_dense else "bm25-only(key-free)"
    out = {
        "method": f"{method} | strategy={strategy} | k={args.k}",
        "points_available": len(_load_gold_points()),
        "coverage_at_focus": coverage_at,
        "coverage_at_boilerplate": coverage_boiler,
        "per_risk_focus": {rt: {"covered": v[0], "total": v[1],
                                "rate": round(v[0] / v[1], 4) if v[1] else 0.0}
                            for rt, v in sorted(per_risk.items())},
        "zero_coverage_risk_types": zero_rt,
        "rows": rows,
    }

    out_dir = ROOT / "lab" / "exp" / "attribution" / "out" / "e5"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "coverage.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[E5] method={method} strategy={strategy} k={args.k}")
    for n in coverage_at:
        print(f"    命中率@ {n} (聚焦查询) = {coverage_at[n]['rate']*100:.1f}%  "
              f"({coverage_at[n]['covered']}/{coverage_at[n]['total']})  | "
              f"含通用词风格 = {coverage_boiler[n]['rate']*100:.1f}%")
    if zero_rt:
        print(f"    聚焦查询零覆盖风险类型({len(zero_rt)}): {', '.join(zero_rt)}")
    print(f"    -> {out_dir/'coverage.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
