# ClauseGuard

上传合同（PDF/DOCX）→ 条款级切分 → 6 个专家 Agent 并行审查 → 汇总去重 → 引用校验 →
高风险条款人工审批 → 回写带批注的 Word。

一个把「合同审查」做成可演进的工程系统，而不是一个 prompt 玩具：契约先行、节点可中断恢复、
引用不可伪造、每一步都能被评测与压测量出来。

## 架构

```
                    ┌──────────────────────────────────────────────┐
   Web (5173) ─────▶│  Java 8080 · 业务与工具                        │
   Vue3 + Vite      │  · 合同上传/版本/内容端点（本地 ./uploads）      │
                    │  · 审查任务 + 审批乐观锁 + 事件重放（Redis）     │
                    │  · SSE 网关（唯一对前端出口）                   │
                    │  · MCP 工具（法条/政策检索）                     │
                    └───────┬───────────────────────┬──────────────┘
                            │ MySQL 8.4             │ SSE 事件流 + traceparent
                            │ （唯一访问方）          │ （契约 contracts/events.schema.json）
                    ┌───────▼───────────────────────▼──────────────┐
                    │  Python 8000 · LangGraph 编排与推理            │
                    │  ingest → diff → dispatch ─┬→ review.<expert>×6│
                    │                            └→ critic → cache  │
                    │  → human_approval（interrupt）→ render(docx)   │
                    │  checkpoint → PostgreSQL 16（可恢复运行态）     │
                    │  OTel span → OTLP → Langfuse / Collector       │
                    └──────────────────────────────────────────────┘
```

- **Java 管业务**：合同/审批/审计是事实来源，需要事务、schema、可审计；同时是 MySQL 的
  唯一访问方（架构铁律 1）。
- **Python 管编排**：LangGraph 管图、断点、预算、引用校验；数据一律经 Java 的 REST/MCP 取。
- **前端只连 Java**：不直连 Python、不持密钥。

关键设计取舍见 [docs/ADR](docs/README.md)：双栈分工、并行拓扑、引用白名单、中断恢复。

## 快速开始（3 条命令）

需要：JDK 21、Node 20、Python 3.12 + uv、Docker、GNU make（Windows 可 `choco install make`）。

```bash
cp .env.example .env && make up                 # 1. 基础设施：mysql/postgres/redis/minio
make run-py MODEL_BACKEND=mock                  # 2. 编排层（无密钥也能跑；真实模型见下）
cd java-service && mvn spring-boot:run          # 3. 业务网关（另开终端）
```

**Windows 上没有 make** 也能跑，等价命令（已在本机逐条验证）：

| 目标 | 等价命令 |
| --- | --- |
| `make run-py` | `cd python-agent && CLAUSEGUARD_AGENT_MODEL_BACKEND=mock CLAUSEGUARD_AGENT_PG_DSN= .venv/Scripts/python.exe -m uvicorn app.main:app --port 8000` |
| `make trace-demo` | `python-agent/.venv/Scripts/python.exe deploy/otel/verify_trace.py` |
| `make eval` | `PYTHONPATH=python-agent:. python-agent/.venv/Scripts/python.exe -m eval.run --label run` |
| `make bench` | `python-agent/.venv/Scripts/python.exe -m locust -f deploy/locust/locustfile.py --headless -u 20 -r 4 -t 5m --host http://127.0.0.1:8000 --only-summary` |
| `make test-py` / `test-java` | `cd python-agent && .venv/Scripts/python.exe -m pytest` / `cd java-service && mvn test` |

验证：

```bash
curl localhost:8080/healthz                     # {"status":"ok"}
curl localhost:8000/healthz                     # {"status":"ok","service":"python-agent"}
cd web && npm install && npm run dev            # 前端 5173（可选）
```

接真实模型（去掉 mock 即可，无需改代码）：

```bash
# .env / 环境变量
CLAUSEGUARD_AGENT_MODEL_BACKEND=live
CLAUSEGUARD_AGENT_LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
CLAUSEGUARD_AGENT_LLM_MODEL=qwen-plus
CLAUSEGUARD_AGENT_LLM_API_KEY=sk-...            # 只走环境变量，绝不入库
```

### 一键可验证的三件事

| 命令 | 验证什么 | 需要什么 |
| --- | --- | --- |
| `make trace-demo` | 一条 trace 串起 Java 父上下文 + 6 个专家 span（本地 OTLP 采集器断言） | 无（自起静态服务 + 采集器） |
| `make eval` | 120 份合同 / 466 条标注的召回、误报、引用、定位、P95、成本，产出 `eval/out/report.md` | 无 |
| `make bench` | 20 并发 5 分钟压测（需先 `make run-py`），产出 `eval/out/bench*.csv` | 无（mock 后端） |

### 起不来时按顺序核对

| 症状 | 真实原因 | 处置 |
| --- | --- | --- |
| `class file version 65.0 ... up to 61.0` | `JAVA_HOME` 指向 JDK 17/11 | 指到 JDK 21 再 `mvn test` |
| `Access denied for user 'clauseguard'@'localhost'` | 库里没有这个账号（Flyway 建表，但不建库/账号） | 用管理员账号建库与账号，见下方 SQL |
| `Unsupported Database: MySQL 8.0` | Flyway 10+ 把 MySQL 方言拆成独立模块 | 需要 `flyway-mysql`（pom 已含，勿删） |
| `Tomcat initialized with port 49277` | 宿主进程注入了 `SERVER__PORT` 之类环境变量 | `env -u SERVER__PORT mvn spring-boot:run` |
| 事件流 500 / `RedisSystemException` | 本机 Redis < 5.0，不支持 Stream | 升级 Redis，或用 `--clauseguard.event-store=in-memory` 本地跑 |

建库（一次性，`<admin>` 换成你的 MySQL 管理员）：

```sql
CREATE DATABASE IF NOT EXISTS clauseguard CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
CREATE USER IF NOT EXISTS 'clauseguard'@'localhost' IDENTIFIED BY 'clauseguard';
GRANT ALL PRIVILEGES ON clauseguard.* TO 'clauseguard'@'localhost';
```

## 目录

| 目录 | 内容 |
| --- | --- |
| `contracts/` | `openapi.yaml`（对外 API）+ `events.schema.json`（跨服务事件，`additionalProperties=false`）+ 代码生成脚本 |
| `java-service/` | Spring Boot 3.5 / JDK 21，包 `com.clauseguard`：contract / review / eval / admin / events / mcp / observability |
| `python-agent/` | Python 3.12 + uv，包 `app`：graph（节点与拓扑）/ agents（6 专家）/ tools / prompts（版本化）/ parse / render / incremental / observability |
| `web/` | Vue3 + Vite + TS + Pinia + Element Plus（只连 Java） |
| `deploy/` | docker-compose、nginx、`otel/`（OTLP 采集器 + 链路验证）、`locust/`（压测）、`demo/`（演示合同生成） |
| `eval/` | 评测数据集生成、runner、指标、报告（`eval/README.md`） |
| `docs/` | ADR、阶段验收报告、压测报告、演示脚本（`docs/README.md`） |

协议端口：java 8080 · python 8000 · web 5173 · mysql 3306(`clauseguard`) ·
postgres 5432(`clauseguard_agent`) · redis 6379 · minio 9000/9001。

## 评测结果（stage 13，120 份合同 / 466 条标注）

| 指标 | 实测 | 口径 |
| --- | --- | --- |
| 风险召回率 | **100.00%**（466/466） | 金标标注按 (条款, risk_type) 命中 |
| 误报率 | 39.01%（298/764） | 关键词替身对「陷阱条款」的固有误报，作为基线供真实模型对照 |
| 引用准确率 | **96.00%**（48/50） | 50 条人工金标，确定性比对（非 LLM-as-judge） |
| 条款定位准确率 | 100.00% | span IoU > 0.8 |
| P95 端到端时延 | 4886 ms | 单份合同全链路（替身模型 + 固定延迟） |
| 单份成本 | ¥0.0039 | 按同一价格表折算 token |

复现性：同一 commit 跑两次，质量/成本指标**逐位一致**，P50/P95 漂移 0.99% / 0.08%。
改一处 prompt（删掉 `invoice_risk` 词表标签）→ invoice 召回 100% → 0%、整体召回 1.0 → 0.9657，
恢复后逐位回到基线。详见 [eval/out/report.md](eval/out/report.md)。

压测（20 并发 / 5 分钟）：见 [docs/benchmark.md](docs/benchmark.md)。

## 可观测性

Java 用 Micrometer Tracing 生成当前 span 的 W3C `traceparent` → HTTP 头传给 Python →
Python 以它为父上下文，每个图节点建 span（`clauseguard.node`，专家带 `gen_ai.agent.name`
与 `gen_ai.usage.*`）→ OTLP/HTTP 上报。

```bash
# 指向 Langfuse（同一个 OTLP 协议，改环境变量即可）
export CLAUSEGUARD_TRACING_ENABLED=true
export CLAUSEGUARD_OTLP_TRACES_ENDPOINT=https://cloud.langfuse.com/api/public/otel/v1/traces
export CLAUSEGUARD_OTLP_AUTHORIZATION="Basic $(printf '%s:%s' "$LANGFUSE_PUBLIC_KEY" "$LANGFUSE_SECRET_KEY" | base64)"
export OTEL_ENABLED=true
export OTEL_EXPORTER_OTLP_TRACES_ENDPOINT=$CLAUSEGUARD_OTLP_TRACES_ENDPOINT
export OTEL_EXPORTER_OTLP_HEADERS="Authorization=$CLAUSEGUARD_OTLP_AUTHORIZATION"
```

不配 Langfuse 也能自证链路：`make trace-demo` 起本地 OTLP 采集器，断言「根 span 的父 = Java
span、6 个专家 span 同 trace 且挂在根 span 下、各自带 tokens」。

## 已知限制

1. **检索是 mock**：`app/tools/*` 返回固定语料（显式 `mock_` 前缀 + TODO），未接 pgvector 真实检索。
2. **评测 examiner 是确定性替身**：100% 召回证明的是链路无损耗，不是模型能力；换真模型只需换
   `build_eval_agent_factory` 里的模型。
3. **条款定位 IoU 恒 1.0**：合成语料里一条款=一句，切分后 span 恰等于标注 span，区分度有限。
4. **Web 工作台未完成**：Stage 12 的 6 条路由 / 三栏工作台 / SSE 断线重连 / 375px 审批页仍是
   待办（后端接口已就绪并测试通过）。
5. **压测跑在 mock 后端**：数值反映编排层（下载/切分/6 专家 fan-out/critic/回写），
   不含真实供应商的模型时延与限流。
6. **Python 侧 resume 是同一条 trace 的第二段根 span**（不是同一个 span 的延续），
   Langfuse 里会显示为两段，用 `clauseguard.resumed` 区分。

## 测试与门禁

```bash
make test-py     # pytest（293 passed）
make test-java   # mvn test（61 tests，需 JDK ≥ 21）
cd python-agent && uv run ruff check app tests && uv run mypy app
cd web && npm run build
```

跑 Java 测试前先 `java -version` 确认是 21：若报
`class file version 65.0 ... only recognizes up to 61.0`，是 `JAVA_HOME` 还指着 JDK 17，
不是代码问题（`export JAVA_HOME=<jdk-21>` 后重跑即可）。

## 许可

内部项目，未授权勿外传。
