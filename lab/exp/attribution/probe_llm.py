"""归因实验 · LLM 连通性探针（只读、单次调用）。

目的：确认本沙箱能否调用真模型（qwen-plus / DashScope compatible-mode），
决定 E2（无检索纯识别）与 E3（弱化证据）能否实跑。

不修改任何 app/ 行为：仅用环境变量把密钥注入到一次独立 HTTP 调用。
"""
from __future__ import annotations

import httpx
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def _read_env() -> dict[str, str]:
    env: dict[str, str] = {}
    p = ROOT / ".env"
    if not p.exists():
        return env
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip()
    return env


def main() -> int:
    env = _read_env()
    key = env.get("LLM_API_KEY", "")
    base = env.get("LLM_BASE_URL") or "https://dashscope.aliyuncs.com/compatible-mode/v1"
    model = env.get("LLM_MODEL") or "qwen-plus"
    print(f"[probe] key_len={len(key)} base={base} model={model}")
    if not key:
        print("[probe] NO_KEY: .env 无 LLM_API_KEY，E2/E3 无法跑真模型")
        return 2
    url = base.rstrip("/") + "/chat/completions"
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {key}"}
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "用一句话回答：1+1 等于几？"}],
        "temperature": 0,
        "max_tokens": 50,
    }
    try:
        r = httpx.post(url, json=payload, headers=headers, timeout=30)
        print(f"[probe] status={r.status_code}")
        if r.status_code == 200:
            data = r.json()
            print("[probe] OK:", data["choices"][0]["message"]["content"][:80])
            return 0
        print("[probe] BODY:", r.text[:300])
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"[probe] FAIL {type(exc).__name__}: {str(exc)[:200]}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
