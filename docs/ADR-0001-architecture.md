# ADR-0001: 双栈架构与通信契约

- 状态：已接受
- 日期：2026-10-22

## 背景

合同审查系统需要两件事合在一起：① 与企业级业务强相关的能力（合同存储、审批流、审计、文件对象存储、权限）；② LLM 推理编排（多专家 Agent、检索增强、可控生成）。这两类能力在生态上天然分属 Java 与 Python，强行单栈会让其中一方非常别扭。

## 决策与取舍理由

### 为什么 Java 管业务、Python 管编排

- **Java 管业务**：合同、审批、审计是系统的「事实来源」，需要强 schema、事务、审计可追溯、稳定的对内对外 REST。Spring Boot 3.5 + MyBatis-Plus 在这一层成熟且可维护；同时 Java 是唯一允许碰 MySQL 的一方，数据主权集中，权限与审计边界清晰。
- **Python 管编排**：多智能体审查本质是「LLM + 图编排 + 工具调用」，LangGraph/LangChain 只在 Python 生态成熟。把推理编排放 Python，可独立迭代 prompt、模型、图结构，而不动业务库。

### 铁律 1（Python 绝不直连业务库 MySQL）的取舍

- **收益**：业务数据读写集中在 Java，天然形成审计与权限边界；Python 侧即使被攻破也无法直接拖库；换模型/换编排不影响数据层。
- **代价**：Python 多一跳网络（经 Java REST/MCP），需要为合同/审批/审计数据定义清晰的 Java 接口与对象存储边界；但这部分成本在 `contracts/openapi.yaml` 里前置固化，长期可控。

### 铁律 2（编排只用 LangGraph；LangChain 仅限单 Agent 内部 create_agent）的取舍

- **收益**：图（节点、中断/恢复、checkpoint）由 LangGraph 统一托管，运行态只放 `ReviewState`，可中断可恢复；LangChain 只作为「单个专家 Agent 内部」的工具/链设施，避免把编排逻辑散落到裸 LangChain 调用里导致不可观测。
- **代价**：团队需区分「编排」与「Agent 内部实现」两层，不能在节点里随手 new 一个 AgentExecutor；通过 `app/graph` 与 `app/agents` 目录边界 + 红线审查来兜底。

### 其他铁律

- 跨服务事件以 SSE 传递，信封结构锁定于 `contracts/events.schema.json`（`additionalProperties=false`），改字段必须先改 schema 再同步两端。
- 前端只连 Java，经 nginx 代理；SSE 路径关闭 proxy buffering，避免事件攒批。

## 后果

- 契约先行：改事件字段必须先改 schema，再同步两端实现。
- 换来的是：数据主权清晰（审批/审计可审计）、Python 侧无业务库泄漏风险、图可中断可恢复、前端无法越权直连推理服务。
