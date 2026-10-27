# AGENTS.md — ClauseGuard 协作铁律

> 任何 Agent/开发者进入本仓库前必须读完本文件。违反铁律的改动一律回退重做。

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

## 版本锁定（不得擅自升级）

| 层 | 版本 |
|---|---|
| Java | JDK 21 / Spring Boot 3.5.x / spring-ai-alibaba 1.1.2.0 / spring-ai 1.1.2 / flyway-core + flyway-mysql（版本由 BOM 托管；Flyway 10+ 把 MySQL 方言拆成独立模块，缺 flyway-mysql 会报 `Unsupported Database: MySQL 8.0`） |
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

## 端口与库名

java 8080 | python 8000 | web 5173（用户端 `/`，管理端 `/admin`）| mysql 3306(db=clauseguard) | postgres 5432(db=clauseguard_agent) | redis 6379 | minio 9000/9001

## 编码规范

- Python：类型注解全覆盖、pydantic v2、async 优先、ruff + mypy 通过、禁止裸 print。
- Java：构造器注入、DTO 与实体分离、统一异常处理、禁止在 Controller 写业务。
- 命名：Python snake_case，Java camelCase，事件类型点分小写（如 `run.started`）。
- 注释只写「为什么」，不写「是什么」。
