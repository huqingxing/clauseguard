"""E5 · 检索覆盖度测量（用户校验 3：gold 条款是否进 top-20）

目的：区分「检索链路通」（已证 100% 真实引用、低延迟）与「检索找得对」（覆盖度）。
方法：抽 20 个 gold 标注点（contract_id, clause_id, risk_type），用 risk_type 构造查询，
经【生产检索路径】live_payload 取 top-20 候选，统计「gold 相关条款/法条进 top-20」的比例。

覆盖判定（faithful proxy）：top-20 候选中至少 1 条文本/标题命中该 risk_type 的领域关键词，
即视为「检索为该风险点返回了相关素材」。我们不预设"正确法条"，只测"检索是否给到相关候选"。

复用 app.tools.retrieval_backend.live_payload —— 与生产同一条 embed+pgvector 路径，
故在可达环境测出的覆盖度直接反映生产检索质量。

环境：需要可用 embeddings（DashScope）与 PG 在线。密钥缺失/端点不可达时优雅退出（exit 2），
不静默跑 mock 冒充真检索。本沙箱 DashScope 返 401 → 阻塞，待密钥环境重跑。

命令：PYTHONPATH='python-agent;.' python-agent/.venv/Scripts/python.exe lab/exp/attribution/e5_retrieval_coverage.py
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

DS_PATH = ROOT / "eval" / "dataset" / "dataset.jsonl"
N_SAMPLE = 20

# risk_type -> (中文名, 领域关键词) ；查询文本由中文名拼出，覆盖判定用关键词。
RISK_META = {
    "acceptance_risk": ("验收风险", ["验收", "标准", "单方", "异议", "抗辩"]),
    "applicable_law_risk": ("适用法律风险", ["适用法律", "准据法", "管辖法律"]),
    "authorization_risk": ("授权风险", ["授权", "权限", "代表", "签字", "代理"]),
    "breach_risk": ("违约风险", ["违约", "赔偿", "违约金", "责任"]),
    "credential_risk": ("资质风险", ["资质", "资格", "许可", "证照", "证书"]),
    "cross_border_risk": ("跨境风险", ["跨境", "境外", "涉外", "外汇", "海关"]),
    "data_processing_risk": ("数据处理风险", ["个人信息", "数据", "隐私", "账号", "密码", "泄露"]),
    "delivery_risk": ("交付风险", ["交付", "物流", "运输", "货期", "迟延"]),
    "disclaimer_risk": ("免责风险", ["免责", "责任限制", "排除", "豁免"]),
    "export_control_risk": ("出口管制风险", ["出口管制", "制裁", "两用物项", "军品"]),
    "force_majeure_risk": ("不可抗力风险", ["不可抗力", "自然灾害", "疫情", "罢工"]),
    "invoice_risk": ("发票风险", ["发票", "开票", "税率", "增值税"]),
    "ip_ownership_risk": ("知识产权风险", ["知识产权", "著作权", "专利", "商标", "权属"]),
    "jurisdiction_risk": ("管辖风险", ["管辖", "法院", "仲裁", "诉讼", "地域"]),
    "open_source_risk": ("开源风险", ["开源", "许可证", "GPL", "MIT", "传染性"]),
    "payment_risk": ("付款风险", ["付款", "预付", "进度款", "账期", "结算"]),
    "qualification_risk": ("主体资格风险", ["资质", "主体资格", "履约能力", "成立"]),
    "succession_risk": ("权利义务承继风险", ["承继", "转让", "主体变更", "并购", "权利义务"]),
    "termination_risk": ("解除终止风险", ["解除", "终止", "退出", "通知期", "条件"]),
}


def _load_gold_points() -> list[dict]:
    """取前 30 份合同的 gold（is_risk 且 risk_type 非空），确定性抽 20。"""
    pts: list[dict] = []
    seen_contracts = 0
    last_cid = None
    for line in open(DS_PATH, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        rec = json.loads(line)
        cid = rec.get("contract_id")
        if cid != last_cid:
            seen_contracts += 1
            last_cid = cid
        if seen_contracts > 30:
            break
        for gc in rec.get("gold_clauses", []):
            rt = gc.get("risk_type")
            if gc.get("is_risk") and rt:
                pts.append({"contract_id": cid, "clause_id": gc.get("clause_id"), "risk_type": rt})
    # 确定性抽样：按 (contract_id, clause_id) 排序后取前 N_SAMPLE
    pts.sort(key=lambda p: (p["contract_id"], p["clause_id"]))
    return pts[:N_SAMPLE]


def _preflight_embed() -> bool:
    """单发一次 embeddings 探测；返回是否可达（200）。"""
    import httpx

    base = os.environ.get("CLAUSEGUARD_AGENT_LLM_BASE_URL") or "https://dashscope.aliyuncs.com/compatible-mode/v1"
    key = os.environ.get("CLAUSEGUARD_AGENT_LLM_API_KEY") or os.environ.get("LLM_API_KEY", "")
    if not key:
        return False
    try:
        r = httpx.post(
            base.rstrip("/") + "/embeddings",
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
            json={"model": "text-embedding-v3", "input": "ping"},
            timeout=30,
        )
        return r.status_code == 200
    except Exception:  # noqa: BLE001
        return False


def main() -> int:
    key = os.environ.get("CLAUSEGUARD_AGENT_LLM_API_KEY") or os.environ.get("LLM_API_KEY", "")
    if not key:
        print("[E5] BLOCKED: 未配置 LLM_API_KEY / CLAUSEGUARD_AGENT_LLM_API_KEY，无法跑 embeddings/检索。")
        return 2
    if not _preflight_embed():
        print("[E5] UNREACHABLE: embeddings 端点非 200（本沙箱 DashScope 返 401）。"
              " 在可达环境配置密钥后重跑本脚本。")
        return 2

    from app.tools.retrieval_backend import live_payload

    points = _load_gold_points()
    if not points:
        print("[E5] 未取到 gold 点，检查 dataset 路径。")
        return 1

    covered = 0
    per_type: dict[str, list[int]] = {}
    rows = []
    for p in points:
        rt = p["risk_type"]
        cn, kws = RISK_META.get(rt, (rt, [rt]))
        query = f"请检索与「{cn}」相关的法律法规与企业制度，用于审查合同中的{cn}风险。"
        raw = live_payload(query) or "{}"
        try:
            payload = json.loads(raw) if isinstance(raw, str) else raw
        except (json.JSONDecodeError, ValueError):
            payload = {}
        cands = payload.get("candidates") if isinstance(payload, dict) else None
        cands = cands if isinstance(cands, list) else []
        hit = False
        for c in cands:
            blob = " ".join(str(c.get(k, "")) for k in ("title", "text", "excerpt", "article"))
            if any(kw in blob for kw in kws):
                hit = True
                break
        covered += int(hit)
        per_type.setdefault(rt, [0, 0])
        per_type[rt][0] += int(hit)
        per_type[rt][1] += 1
        rows.append({"contract_id": p["contract_id"], "clause_id": p["clause_id"],
                     "risk_type": rt, "top_k": len(cands), "covered": hit})

    rate = covered / len(points) if points else 0.0
    out = {
        "sample": len(points),
        "covered": covered,
        "coverage_rate": round(rate, 4),
        "per_risk": {rt: {"covered": v[0], "total": v[1],
                          "rate": round(v[0] / v[1], 4) if v[1] else 0.0}
                     for rt, v in sorted(per_type.items())},
        "rows": rows,
    }
    out_dir = ROOT / "lab" / "exp" / "attribution" / "out" / "e5"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "coverage.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[E5] coverage={out['coverage_rate']} ({covered}/{len(points)}) -> {out_dir/'coverage.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
