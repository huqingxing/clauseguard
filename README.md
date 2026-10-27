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

需要：JDK 21、Node 22（`web/package.json` 的 `engines` 要求 ≥ 22.18：`node --test` 跑 `.ts`
靠内置类型剥离，低版本直接报语法错）、Python 3.12 + uv、Docker、GNU make（Windows 可 `choco install make`）。

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

### 第一次跑通的自检清单（约 30 分钟，逐条看输出再往下走）

| # | 命令 | 期望输出 | 不对怎么办 |
| --- | --- | --- | --- |
| 1 | `java -version` | 21.x | 不是 21 就 `export JAVA_HOME=<jdk-21>`，否则 `mvn` 报 `class file version 65.0` |
| 2 | `node -v` | ≥ 22.18 | 低版本 `npm run dev` 能起，但 `npm run test:unit` 会在 `.ts` 第一行报语法错 |
| 3 | `curl localhost:8080/healthz` | `{"status":"ok"}` | 起不来先看「起不来时按顺序核对」表 |
| 4 | `curl localhost:8000/healthz` | `{"status":"ok","service":"python-agent"}` | 报 502 多半是本机代理，用 `env -u http_proxy -u https_proxy ...` 起 |
| 5 | `curl -i localhost:8080/api/v1/admin/health` | `401` + `{"code":"UNAUTHORIZED",...}` | 200 说明 SecurityConfig 没生效 |
| 6 | `curl -u admin:<口令> localhost:8080/api/v1/admin/health` | `200` + `{"java":{"status":"up"},...}` | 401 说明口令不是 `application.yml` 里 `admin` 那一行的 `password` |
| 7 | `cd web && npm run test:unit && npm run build` | 31 测全绿 + `built in` | 类型错误在 `build` 这一步拦下 |
| 8 | 浏览器开 `http://127.0.0.1:5173` | 合同列表；上传 `deploy/demo/demo-contract.docx` | 见下「界面」小节的路由表 |
| 9 | 浏览器开 `http://127.0.0.1:5173/admin.html` | 登录卡（不是管理端内容） | 直接进内容说明登录闸门没生效 |

演示合同没有就 `make demo-contract` 生成（Windows 等价命令见上表）。管理端口令是
`application.yml` 里 `clauseguard.security.users` 的 `admin` 那一行——**上线前必须换**。

### 界面（web/，两个入口：用户端 3 条路由 + 管理端 4 条路由）

用户端与管理端是**两个独立 HTML 入口**，各自一套导航。产物按入口切分：`index.html` 引
`main-*.js`，`admin.html` 引 `admin-*.js`，运维健康 / 评测看板 / 待审批队列的代码只进管理端包
（用户端首包 `grep "api/v1/admin|运维健康|评测看板|待审批"` 0 命中）。
**准确说法是「应用代码按入口切分」，不是「两个包毫无共享」**：Element Plus / Vue / vue-router / Pinia
被 Rollup 合成一个 vendor chunk（`_plugin-vue_export-helper-*.js`，约 1.0 MB），两个入口都引它——
同一份框架代码没必要打两遍，代价是只开用户端的人也会下载管理端用到的那部分组件。

用户端 http://127.0.0.1:5173 — 按下面顺序走一遍就是一次完整审查：

| 路由 | 页面 | 做什么 |
| --- | --- | --- |
| `/` | 合同列表 | 上传 `deploy/demo/demo-contract.docx`（没有就 `make demo-contract` 生成） |
| `/contracts/:id` | 工作台 | 三栏：左原文 / 中风险发现 / 右运行态；点「全量审查」 |
| `/reviews/:id` | 结果页 | 只读落地页，可分享；`waiting_approval` 时只显示「等待法务审批中」+ 已等待时长 |

审批页不在这里——拍板的是审核者，不是发起审查的提交者，它在管理端（见下）。
用户端**没有任何审批入口**：没有按钮、没有跳管理端的链接，连「隐藏的入口」都不留。
旧地址 `/reviews/:id/approve` 会落到 404 页（`NotFoundView`）并写明审批已迁到管理端——
静默回合同列表会被读成「跳错页了」，地址失效必须停住说清楚。

管理端 http://127.0.0.1:5173/admin（生产 `/admin`，产物 `admin.html`），**第一次打开要先登录**：

管理端用 **hash 路由**（`/admin#/health`）：它是独立 HTML 入口，hash 后面怎么变服务端都只看到 `/admin`，
不用为每条子路由配 nginx 回退；前进/后退、刷新、复制地址都正常。

| 路由 | 屏 | 做什么 |
| --- | --- | --- |
| `#/health` | 运维健康 | Java + Python 双栈状态，10 秒轮询（打开 `/admin` 默认落这里） |
| `#/eval` | 评测看板 | token / 成本 / 校验通过率 / 增量收益（老链接 `/eval`、`/admin#eval` 自动迁移） |
| `#/approvals` | 待审批队列 | 审核者的待办：合同名 / 高风险条款数 / 等待时长 / 进入审批；超过 2 小时标黄，30 秒自动刷新 |
| `#/approvals/:id` | 审批详情 | 375px 优先，逐条 通过/驳回/改措辞；审批人 = 当前登录账号，底栏有「返回待审批队列」 |

队列数据来自 `GET /api/v1/admin/approvals?status=WAITING_APPROVAL&page=0&size=20`，
挂在 `/api/v1/admin/**` 下，因此和健康页共用同一套 BASIC 鉴权。没有新建队列表：
待审是 `review.status` 的一个取值，单独存表就会与事实源漂移。`waiting_since` 取 `interrupt`
事件的时间戳——按 `created_at` 算会把运行耗时也算成审核者的等待。

账号只有两个，都写在 `java-service/src/main/resources/application.yml` 的 `clauseguard.security.users`：

| 账号 | 角色 | 能干什么 |
| --- | --- | --- |
| `admin` | ADMIN | 进得了四个屏；口令就是该文件里 `admin` 那一行的 `password`（演示口令，随仓库提交） |
| `user` | USER | 口令对但不是 ADMIN：停在登录卡并显示「该账号没有管理员权限（需要 ADMIN 角色）」——用它验证 403 不会被当成「密码错」 |

**换口令的方式**（上线前必须换，演示口令不能带出本机）：改那两行的 `password` 后重启——启动时
会把明文 BCrypt 编码后放进内存，内存里不留明文，配置文件本身仍是明文（正因为如此，生产不该继续
用这份内存用户源，应换成外部用户源 + 密钥管理，本阶段没做）。凭证以 HTTP BASIC 存在
**当前标签页的 sessionStorage** 里，关标签页即失效；任何 admin 请求返回 401 都会清掉凭证退回登录卡。

分开的理由：这两屏暴露的是内部拓扑（谁探谁、依赖是否连通）与成本结构，不是给签约方看的。
权限由后端把关——`/api/v1/admin/**` 需要 `ADMIN` 角色（401/403 都是 JSON）。**前端登录卡不是安全边界**：
知道接口的人绕开页面直接调，一样会被后端拒。

演示合同第五条（违约金不设上限）会同时触发两个专家，因此审批页会显示
**1 条条款 / 2 个风险点**——审批按条款拍板，不是按风险点。

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
| `web/` | Vue3 + Vite + TS + Pinia + vue-router + Element Plus；两个入口（用户端 3 路由 / 管理端 4 条 hash 路由）、三栏工作台、375px 审批页（只连 Java） |
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
4. **同一合同同版本的第二笔审查会串状态**：`thread_id` 取 `contract_id:version`，两笔审查
   共享同一个 LangGraph checkpoint，第二笔的 `findings` 会累加并出现一批 `unverified`
   （实测 23 → 46）。单笔隔离跑是干净的。属 Stage 9 设计遗留，修法见
   [docs/stage12-acceptance.md](docs/stage12-acceptance.md) 5.1。
5. **管理端已鉴权，用户端仍未鉴权**：`/api/v1/admin/**`（含待审批队列）需要 `ADMIN` 角色
   （HTTP BASIC，账号在 `application.yml` 的 `clauseguard.security.users`，启动时 BCrypt
   编码后进内存，**明文只用于本机演示，生产必须换**）。`/api/v1/**` 其余接口仍是 `permitAll`。
   **审批写入端点也在其中**：`POST /api/v1/reviews/{id}/approve` 没锁，绕过管理端页面直接
   POST 照样能批（`X-Operator` 现在是登录账号，比手填强，但仍是「客户端自称」，
   不是服务端认定）。要真锁，得先给该路径加上 ADMIN 前缀规则。
   对外上线前必须先接用户认证，否则审计留痕形同虚设。**注意评测接口也是其中之一**：
   管理端登录卡把两屏都挡在后面，但 `/api/v1/eval/summary` 在网关侧仍放行，
   绕过页面直接请求照样拿得到成本结构——前端登录只解决「谁在用界面」，不解决「谁能调接口」。
6. **压测跑在 mock 后端**：数值反映编排层（下载/切分/6 专家 fan-out/critic/回写），
   不含真实供应商的模型时延与限流。
7. **Python 侧 resume 是同一条 trace 的第二段根 span**（不是同一个 span 的延续），
   Langfuse 里会显示为两段，用 `clauseguard.resumed` 区分。
8. **待审批队列不区分「派给谁」**：所有 `AWAITING_APPROVAL` 的审查对每个管理员都一样可见，
   没有指派人、没有认领、没有并发保护（两人同时打开同一笔，后提交的会拿到 409
   `CONTRACT_CHANGED`，因为状态已不是待审）。队列只解决「待办在哪儿」，不解决「谁来干」。
9. **「预计 2 小时」是口径不是测量值**：用户端详情页写的排期与队列标黄阈值用的是同一个常量
   （`APPROVAL_SLA_HOURS = 2` / `OVERDUE_MS = 2h`），没有按历史审批时长统计。改成真实
   percentile 需要一张审批耗时的事实表，本阶段不做。
10. **状态先改、事件后写**：`handleAgentEvent` 先更新 `review.status` 再落 `review_event`，
    两步不在同一个事务里。Stage 5 实测撞到过——`run.completed` 的 payload 超过 TEXT 上限，
    事件插入失败而状态已是 `COMPLETED`，用户端重放看不到终态、永远停在「等待审批」。
    已用 `V4__widen_event_payload.sql` 把列放宽到 MEDIUMTEXT 消除触发条件，
    但「写事件失败会导致状态与事件不一致」这个结构性问题仍在。
11. **批注稿链路的源对象键没接通（默认配置下没有「下载批注稿」）**：Java 上传把源 docx
    落在 `uploads/{id}-v{n}.docx` 并把 `source.object_key` 透传给 Python；Python 的 render
    只按 `contracts/{id}/v{n}/source.docx` 去对象存储取源。两边键约定没对上，render 走
    `PLACEHOLDER_RENDER: reason=source-unavailable`，结果页就只剩一个空产物。本机验收是把
    源 docx 按约定键摆进 `local_dir` 才跑出批注稿的——修法是让 Java 按同一套约定键写入对象
    存储（Python 侧不动），或者让 render 改用透传的 `source.download_url`。
12. **后端不可用时前端不承认**：列表页把后端/代理给的裸 `HTTP 500` 直接显示出来，并落到
    「还没有合同」空态（像是「你还没上传」而不是「服务挂了」）；结果页连不上时只显示
    「重连中（第 N 次）」，没有任何失败说明。不是白屏，但会误导。另外工作台加载时请求
    `/api/v1/contracts/{id}/diff?target=<当前版本>`，新版本尚未审查时必然 404（前端已
    try/catch，浏览器仍记一条资源级 error，属既有噪音）。
13. **用户端接口仍未鉴权，只有 `/api/v1/admin/**` 受保护**：`SecurityConfig` 里除管理端路径外
    一律 `permitAll`，所以上传、发起审查、读结果、审批写入、以及 `/api/v1/eval/summary` 都不需要
    任何凭证——用 `curl` 不带 `-u` 直接打就是 200。管理端那套 BASIC 只是把「运维/成本/审批」这一块
    关起来，**不是**给整个 API 上的门。任何「用户端看着没有入口」的结论都不等于「接口调不到」，
    细节与后果见第 5 条。

## 测试与门禁

```bash
make test-py     # pytest（293 passed）
make test-java   # mvn test（85 tests，需 JDK ≥ 21）
cd python-agent && uv run ruff check app tests && uv run mypy app
make test-web    # node --test（31 passed，需 Node ≥ 22.18：跑 .ts 靠内置类型剥离）
cd web && npm run build   # vue-tsc -b + vite build，类型错误在这一步拦下
```

CI 分两个独立 workflow，按改动路径触发，互不阻塞：

| workflow | 触发 | 跑什么 |
| --- | --- | --- |
| `.github/workflows/web.yml` | `web/**` 变动 | `npm ci` → `npm run test:unit` → `npm run build`（Node 22） |
| `.github/workflows/eval.yml` | `eval/**`、`python-agent/app/**` 变动 | 冒烟/全量评测（确定性，不连真模型） |

前端 job 只跑纯函数单测与构建，**不跑需要真实模型与真实服务的用例**：那些在这里既慢又不稳，
属于手工验收 + 后续 `web/e2e` 的范围。

跑 Java 测试前先 `java -version` 确认是 21：若报
`class file version 65.0 ... only recognizes up to 61.0`，是 `JAVA_HOME` 还指着 JDK 17，
不是代码问题（`export JAVA_HOME=<jdk-21>` 后重跑即可）。

## 许可

内部项目，未授权勿外传。
