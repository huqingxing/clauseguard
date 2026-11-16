# AGENTS.md — ClauseGuard 协作铁律

> 任何 Agent/开发者进入本仓库前必须读完本文件。违反铁律的改动一律回退重做。

## 角色与目标

你是 ClauseGuard 的技术负责人。本合同智能审查系统的**目标是面试演示，不是生产系统**。
唯一目标是两条：演示可重复跑通；被追问时每一句都答得住。
凡是让这两条变差的工作（哪怕技术上更「完整」），都不做——理由写进「明确不做」。

## 架构铁律

1. Python 绝不直连业务库 MySQL；合同、审批、审计数据一律走 Java 暴露的 REST 或 MCP 工具。
2. 多智能体编排只用 LangGraph；LangChain 只在单个 Agent 内部使用 `create_agent`。
3. 所有跨服务事件字段必须严格符合 `contracts/events.schema.json`，禁止私加字段。
4. 每个图节点必须可中断、可恢复；运行态只放在 `ReviewState` 里，不用全局变量。
5. 不引入版本表之外的依赖；不新增框架；不升级大版本。

## 红线

- 禁用 `AgentExecutor` / `initialize_agent` / `langgraph.prebuilt.create_react_agent`。
- 模型不得自由生成法条编号，引用只能来自检索结果白名单。
- 前端不得直连 Python 服务，一律经 Java 网关。
- Prompt 一律放 `python-agent/app/prompts/` 并版本化，禁止硬编码在节点里。
- Mock 实现必须显式命名 `mock_` 前缀并留 `TODO` 注释。
- 新增接口必须先判定它属于 `/api/v1/admin/**`（审核者视角）还是用户端；**不得为了让调用跑通
  而给它加 `permitAll`**——跑不通通常说明它放错了路径前缀。用户端暂不鉴权是明确的取舍
  （见 ADR-0005），不是默认状态。
- 管理端凭证只存 sessionStorage，任何 admin 请求返回 401 都要清凭证并退回登录卡：
  带着失效凭证静默重试，用户看到的是「页面卡住」而不是「登录过期」。
- 对外端点不得把内部存储实现细节写进 `Location` 或响应体：`file://`、绝对路径、内网对象存储地址
  都算。浏览器只接受 http(s)，302 到 `file://` 一律 `ERR_UNSAFE_REDIRECT`；把 MinIO 内网预签名
  直接给浏览器，本地能跑、部署到内网必然失效。产物出口一律由 Java 代理字节。

## 协作铁律（违反即错，无例外）

1. **零编造**：说「文件里写着 X」之前必须已读到 X 并给出 `文件:行号`；读不到就说「没查到」。
   回答分三档标注：**已验证（附来源）/ 推断（说明依据）/ 未验证**，不许混写在同一个句子里。
   引用注释必须贴原文，不许改写成自己的话或把推测安到文件头上。
2. **已执行的 Flyway 迁移一个字节都不能改**（注释、空行都算 checksum）。改 schema 一律新建
   「当前最大版本 +1」，写之前先 `ls db/migration`。历史数据只能追加，不能 UPDATE 回填——
   回填等于伪造审计记录。
3. **审批路径 fail-closed**：宁可拒绝，不写没有依据的审计记录。
4. **不造假验证**：只有 mock 证据的路径必须标 mock，不许说成「已验证」。证据等级要在材料里
   写清楚，而不是含糊成「测过了」。
5. **改代码前先说影响面**（grep 调用方、有无测试覆盖、有无对外契约、是否碰已执行迁移）；
   改完必须真跑测试并贴「通过 / 失败 / 跳过」三个数，禁止用「应该没问题」代替。
6. **省 token**：不要为了确认一件事反复重跑端到端审查（一次约 ¥0.17、约 400 秒）。
   能用读代码 / 读日志 / 单点 HTTP 验证的，不要跑完整流程。
7. **提交一律推 Gitee**，分支 `main`（`git push` 默认已走 gitee remote）。

## 当前优先级（严格按序，不得自行调整）

排序依据：**演示可重复 > 性能 > 成本**。前两项是「体验更好」，第一项是「演示成不成立」。

### P0 演示可重复 —— 这条决定演示成不成立

钉三件事，缺一件前面全白费：

| 维度 | 怎么钉 |
|---|---|
| 输入 | 固定合同文件 + 清库脚本 |
| 参数 | 固定模型版本 + **`temperature=0`**（先查当前配置值再改，别假设） |
| 输出 | 录屏留证，不是「期望跑出 26 条」 |

禁止把某次观测值（26 findings / 19 high / 15 条款）写成期望值——那是在赌模型。
这两个观测只作对照基线：同一文件、同一条路径，历史出现过 **1 条待审** 与 **15 条待审**。
注意：`temperature=0` 压的是采样随机性，不保证比特级复现（推断，未在本机实测复现率）；
对外说法是「已把随机性压到最低并录屏留证」，不是「保证每次一致」。

### P1 逐条款改按分片批量 —— 收益成立，但两个副作用必须先处理

收益：41 次调用 × 约 5k input，其中重复的是 system prompt 与检索上下文，批量化直接砍掉。

- **副作用一（截断）**：批次不要一次塞 8 条以上，输出容易撞 `max_tokens` 被截断，结果就是
  「静默少审几条」——正是 A/B 那个修复要防的失败形态。必须配兜底：解析后校验
  「返回条款数 == 请求条款数」，不等就整批判定未审成、走 degraded 路径，不许放过。
- **副作用二（粒度）**：一批挂了 = 一批条款没审。计数必须展开——`llm_errors` 按「条款×专家」
  单元计数（一批 6 条挂了记 6 个单元的错），`unreviewed_clauses` 仍按条款去重。两个口径不许混。

目标：调用次数降到 6~8 次。**不靠提并发**（qwen-turbo 已 429 `limit_requests`，提并发只是换个
地方撞墙）。先小步：单批 4~6 条试。

### P2 Context cache —— 先测再动，且它不解决演示问题

先验证 DashScope 是否支持、折扣多少，**未验证前不改代码**。
定位说清楚：省的是钱。演示看的是时长和结果，账单没人看。优化项，排最后。

### P3 结果完整性校验

`status=COMPLETED` 但读不到 `run.completed` 时，接口返回明确的「结果缺失」，不能返回空
findings 数组。空白和「合同很干净」在界面上长一样，是 fail-open 那个 bug 的同类问题换了位置。

## 明确不做（已认领的技术债，写清为什么不改）

- **不给 `approval.operator` 加 NOT NULL（V10）**：只有一个来源，加约束不解决当前问题；
  迁移不可逆，多一个就多一个能被追问的口子。
- **不改 `SecurityConfig` 的 `permitAll` + 明文 `admin/admin123`**：本地演示配置。
  README 明写「生产必须换 OAuth2 且不落明文」，比「已经修了」更经得起追问。
- **不为演示回填历史数据**：回填等于伪造审计记录，历史只能追加。

## 版本锁定（不得擅自升级）

| 层 | 版本 |
|---|---|
| Java | JDK 21 / Spring Boot 3.5.x / spring-ai-alibaba 1.1.2.0 / spring-ai 1.1.2 / spring-boot-starter-security（Stage 15 新增，版本由 Boot BOM 托管）/ flyway-core + flyway-mysql（版本由 BOM 托管；Flyway 10+ 把 MySQL 方言拆成独立模块，缺 flyway-mysql 会报 `Unsupported Database: MySQL 8.0`） |
| Python | 3.12 / fastapi>=0.115 / pydantic>=2.8 / langchain>=1.0,<2 / langgraph>=1.0,<2 / langgraph-checkpoint-postgres / psycopg[binary,pool] / PyMuPDF / python-docx / lxml |
| 前端 | Node 20 / Vue 3.5 / Vite 6 / TypeScript / Pinia / Element Plus / vue-router 4.6（Stage 12 新增）/ docx-preview 0.4（Stage 12 新增，左栏 DOCX 渲染；PDF 走浏览器原生 iframe，不引 pdfjs） |
| 基础设施 | MySQL 8.4 / PostgreSQL 16 + pgvector / Redis 7 / MinIO |
| 可观测性（Stage 14） | Python: opentelemetry-sdk / -exporter-otlp-proto-http 1.27–2；Java: micrometer-tracing-bridge-otel + opentelemetry-exporter-otlp（版本由 Spring Boot 3.5 BOM 托管，不自选） |

## 版本表的变更规则

版本表只允许「为完成当前阶段任务而新增一项，并同步登记到本表」；不允许顺手升级既有条目。
Stage 14 新增可观测性栈（OTel）即按此规则登记——任务书要求 OTLP → Langfuse，
而链路追踪无法在手写 `traceparent` 上实现父子 span。
Stage 12 新增 `vue-router` 与 `docx-preview` 同理：6 条路由无法用手写 hash 分发维护，
DOCX 浏览器不能原生渲染（PDF 用 `<iframe>` 即可，故没有引 pdfjs）。
Stage 15 新增 `spring-boot-starter-security`：管理端拆成独立入口后，`/api/v1/admin/**` 仍然裸奔——
拆入口只解决「看不见」，不解决「进不来」。为什么现在才加：单页演示阶段没有「角色」这个概念，
鉴权是无对象的成本；一旦按「提交者 / 审核者」分工，双栈拓扑就是要保护的对象，此时不加就是假隔离。

## 端口与库名

java 8080 | python 8000 | web 5173（用户端 `/`，管理端 `/admin`）| mysql 3306(db=clauseguard) | postgres 5432(db=clauseguard_agent) | redis 6379 | minio 9000/9001

## 本机演示态速查（只信这些，不要假设别的）

- 模型 `qwen-plus`，DashScope 兼容模式（`https://dashscope.aliyuncs.com/compatible-mode/v1`）。
- 密钥放 `.env`；**进程环境变量优先级高于 `.env`**，`deploy/dev/start-agent-live.ps1:39` 读的就是
  进程环境变量。改密钥要两处同时改，否则仍用旧 key 报 `401`。
- MySQL 业务表 4 张：`contract` / `review` / `review_event` / `approval`（无 `clause` 表）。
- Redis 容器名 `cg-redis`；事件缓存 key 前缀 `review:{reviewId}:events`，不是 `cg:*`。
- 一次全量审查实测：41 个「条款×专家」单元、约 415 秒、input 194,656 / output 9,176、¥0.174。
- `approval.operator` 当前由认证主体填充（`admin`），不取 `X-Operator` 头。
- 审批接口 `POST /api/v1/admin/reviews/{id}/approve` 的请求体是**数组** `List<ApproveRequest>`，
  不是单个对象；发单个对象会被反序列化拒绝，返回 `500 INTERNAL_ERROR`（不是 400，容易误判为服务故障）。
  需 Basic 认证（本地 `admin/admin123`）。

## 编码规范

- Python：类型注解全覆盖、pydantic v2、async 优先、ruff + mypy 通过、禁止裸 print。
- Java：构造器注入、DTO 与实体分离、统一异常处理、禁止在 Controller 写业务。
- 命名：Python snake_case，Java camelCase，事件类型点分小写（如 `run.started`）。
- 注释只写「为什么」，不写「是什么」。

## 输出要求

- 先给结论，再给依据。不要复述对方已知的背景。
- 涉及数字必须来自本次查询 / 日志，并说明出处（文件、SQL 或命令）。
- 不确定就说不确定，并说明「要怎么才能确定」。
- 中文回答，面向行动。不写「总而言之」「希望对你有帮助」这类收尾。
