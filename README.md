# ClauseGuard

上传合同（PDF/DOCX）→ 条款级切分 → 6 个专家 Agent 并行审查 → 汇总去重 → 引用校验 → 高风险条款人工审批 → 回写带批注的 Word。

## 架构

```
                 ┌────────────┐   upload / review / approve   ┌──────────────────┐
   Web (5173) ───▶│  Java      │◀──── SSE events ────┐         │  MySQL 8.4       │
                 │  8080      │                     │         │  (business data) │
                 │ business + ├─── REST / MCP ──────┼────────▶│  MinIO (docs)     │
                 │ SSE gateway│                     │         └──────────────────┘
                 └────────────┘
                       ▲  events.schema.json (additionalProperties=false)
                       │
                 ┌─────┴─────┐   LangGraph 编排 (ReviewState 仅存运行态)
                 │  Python   │   checkpoint → PostgreSQL 16 + pgvector
                 │  8000     │   Redis 7 (缓存/锁)
                 └───────────┘
```

- Java（8080）：业务与工具，唯一 MySQL 访问方，SSE 网关。Python 绝不直连业务库（铁律 1）。
- Python（8000）：LangGraph 编排与推理，数据一律经 Java REST/MCP 获取。
- Web（5173）：Vue3 + Vite + TS，只连 Java（前端不直连 Python）。

## 快速开始

```bash
cp .env.example .env          # 填入 LLM_API_KEY 等本地变量
make up                       # 一键拉起 mysql/postgres/redis/minio（4 容器 healthy）
make test-py                  # Python 空测试
make test-java                # Java 空测试（需 JDK 21）
curl localhost:8080/healthz   # Java 探针 -> {"status":"ok"}
curl localhost:8000/healthz   # Python 探针 -> {"status":"ok","service":"python-agent"}
```

## 目录

见 `AGENTS.md`。阶段计划与 ADR 见 `docs/`。
