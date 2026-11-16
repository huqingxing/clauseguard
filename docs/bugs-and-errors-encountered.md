# clauseguard 构建 / 实现过程中遇到的 Bug 与报错汇总

> 用途：复盘、面试讲解「改哪里 / 为什么 / 怎么修」。
> 范围：阶段 13（last_event_at）、阶段 14（看门狗回收）、阶段 15（.env 半失效）、本地跑通时的运维坑，以及 2026-11-15 的四项加固（索引/N+1、DSN fail-fast、异常兜底、跨层调用）。
> 结论先行：**绝大多数 bug 出现在「真实环境 vs mock 单测」的落差处**——单测覆盖不到 JPA/Hibernate 绑定、事务边界、进程环境变量、前端代理、系统代理等问题。

---

## 一、总览表

| # | 阶段 | 现象 / 报错 | 根因 | 修复 |
|---|---|---|---|---|
| 1 | 13 | `last_event_at` 比 `MAX(review_event.ts)` 少 1 秒 | `TIMESTAMP(fsp=0)` 四舍五入，代码 `truncatedTo(SECONDS)` 截断 | round-half-up（+500ms 截断） |
| 2 | 14 | 看门狗静默不回收卡死 RUNNING（候选集恒空） | JPQL `Instant` 绑 `DATETIME` 偏 8h | 原生 SQL + UTC 字符串参数 |
| 3 | 14 | 看门狗仍不回收，日志只记 WARN | `@Modifying` 在 `scan()` 内无事务，`TransactionRequiredException` 被 catch 吞 | 调度入口方法加 `@Transactional` |
| 4 | 运维 | 管理端登录报「后端出错了（HTTP 500）」 | Java 跑在 8099，前端 `/api` 代理写死 8080 → 连不上 | Java 改回 8080 |
| 5 | 运维 | `mvn spring-boot:run` 启动失败：`webServerStartStop` | 不带 `-Dspring-boot.run.arguments` → 解析成随机端口 49277 撞监听 | 显式传 `--server.port=8080` |
| 6 | 15 | `CLAUSEGUARD_AGENT_*` 配置了却"不生效" | `AgentSettings` 无 `env_file`；`Settings.env_file` 相对 CWD | 按文件位置定位根 `.env` |
| 7 | 15 | 端到端 live/mock 实跑无法在本机沙箱完成 | `docker compose up -d` 拉镜像返回 HTML 错误页 | 交用户本机执行（非代码 bug） |
| 8 | 运维 | 本机 curl 返 502（假死相） | 走系统代理连本地服务 | `--noproxy '*'` / `NO_PROXY` |
| 9 | 运维 | 连真实 LLM 报 401 | 默认 `base_url=openai.com`、缺 `base_url/model` 覆盖 | 显式设 DashScope/DeepSeek 端点 |
| 10 | 加固 | 合同列表 20 条 = 20 次审查查询；按 contract_id 全表扫 | `review` 只有主键，无 `(contract_id, created_at)`；`toSummary` 逐行查 | `V6` 补两索引 + 最近审查收进 `ReviewService` 批量取 |
| 11 | 加固 | 检索静默 0 命中，链路照常跑完 | `pg_dsn` 默认串指向 `5432/clauseguard_agent`（另一个库），配置丢失时连空库不报错 | 删默认值，`ChunkStore` 构造期 fail-fast |
| 12 | 加固 | 未预期异常的 500 可能带堆栈、表名、绝对路径 | 只映射了 6 个业务异常，其余走 Spring 默认处理 | 兜底 handler：`ErrorResponse` 原样放行，其余固定文案 |
| 13 | 加固 | `ContractController` 直接注入 `ReviewRepository` | 跨模块越过 Service 层直接读别人的仓储 | 下沉为 `ReviewService.latestReview/latestReviews` |
| 14 | 加固 | 同一 review 的同一 clause 出现两条审批记录 | `approval` 只有 `(review_id)` 普通索引；状态 CAS 只管 review 状态，管不到请求体里的重复 clause | `V7` 加 `uk_approval_review_clause` + 写前判重 + 事务外捕获冲突当幂等 |
| 15 | 运维 | `flyway_schema_history` 停在 v5，V6 的索引在库里根本不存在 | 测试要么不连库、要么显式关 Flyway，迁移从未被真实执行过 | 用 flyway-maven-plugin 对本地库实跑 `migrate`，v5 → v7 |
| 16 | 设计 | 审批提交 21 条，只落 17 条 | 上游 `build_interrupt_payload` 按 finding 生成 items，一个 clause 被两个风险维度命中就出两条 | Java 侧去重保住约束（待定：Python 侧聚合） |

---

## 二、详细记录

### 🐞 1. 阶段 13 — `last_event_at` 1 秒偏差
- **报错/现象**：`8977558a` 实机验收时，某审查的 `last_event_at = 04:33:22`，但 `MAX(review_event.ts) = 04:33:23`（interrupt 事件 ts 落在 `22.935995`，被 TIMESTAMP 进位）。
- **根因**：`review_event.ts` 是 `TIMESTAMP(fsp=0)`，MySQL 按**四舍五入**到秒；原代码用 `truncatedTo(ChronoUnit.SECONDS)` 是**截断**，落在秒后半段的事件就差 1 秒。
- **修复**（`ReviewService.java`）：改为 round-half-up，加 500ms 再截断：
  ```java
  entity.getTs().plusMillis(500).truncatedTo(ChronoUnit.SECONDS)
  ```
  同时用 **UTC 字符串字面**传参，绕开 Hibernate `Instant→DATETIME` 的绑定偏移。
- **验证**：全表 51 条 `last_event_at == MAX(ts)`，0 NULL / 0 偏差（3 条旧截断行幂等 UPDATE 校正）。

### 🐞 2. 阶段 14 — JPQL `Instant` 绑 `DATETIME` 偏 8h（候选集恒空）
- **报错/现象**：看门狗 5 分钟没触发，卡了几小时的 `14a1b626` 纹丝不动；单测却绿的。
- **根因**（单测 mock 没覆盖）：`findStaleRunningIds` / `reapStuckRunning` 原用 JPQL（`@Query` 实体名 + `Instant` 参数）。Hibernate 把 `Instant` 绑 `DATETIME(3)` 列会**系统性偏 8 小时**，导致 `COALESCE(last_event_at, created_at) < :cutoff` 恒为 FALSE。
- **修复**（`ReviewRepository.java`）：两个方法改为**原生 SQL + UTC 字符串参数**（与阶段 13 已验证的 `updateLastEventAt` 同一套路）。
- **验证**：直接 SQL 跑出候选 `14a1b626`、`wd-test-running-0001`；临时 `interval=10s` 加速，两条均回收为 FAILED。

### 🐞 3. 阶段 14 — `@Modifying` 缺事务，`TransactionRequiredException` 被吞
- **报错/现象**：修了 bug2 后仍不回收，日志只有 WARN。
- **根因**：`reapStuckRunning`（`@Modifying`）在 `scan()` 内调用，而 `scan()` 没有事务上下文，真实运行抛 `jakarta.transaction.TransactionRequiredException`，被 `@Scheduled` 入口的 try/catch 记 WARN 后丢弃。
- **修复**（`ReviewWatchdog.java`）：在带 `@Scheduled` 的入口方法加 `@Transactional`（org.springframework.transaction.annotation.Transactional），事务覆盖整个 `scan()` + reap。
- **验证**：实机回收成功，补 `run.failed(REVIEW_TIMEOUT, retryable=true)`；AWAITING_APPROVAL 反例零误伤；条件更新 `affected=1` 保证并发幂等。

### 🐞 4. 运维 — 管理端登录「HTTP 500」
- **报错/现象**：前端登录管理端，错误提示「后端出错了（HTTP 500）」。
- **根因**：密码错是 401，500 说明请求没到认证环节。真实原因：阶段 14 验收时 Java 用了 `--server.port=8099`，而 `web/vite.config.ts` 的 `/api` 代理 **target 写死 `http://localhost:8080`** → 连不上 → 前端 `describeApiError` 把无结构体错误降级成「HTTP 500」。
- **修复**：Java 改回默认 8080 重启（`health` 200）；管理端登录恢复。
- **教训**：前端代理写死 8080，Java 必须起在 8080。

### 🐞 5. 运维 — `mvn spring-boot:run` 随机端口
- **报错/现象**：启动报 `webServerStartStop` 启动失败。
- **根因**：不带 `-Dspring-boot.run.arguments` 时，参数被解析成随机高位端口（如 49277），撞上 WorkBuddy 自身监听。
- **修复**：显式传 `--server.port=8080`。

### 🐞 6. 阶段 15 — `.env` 半失效（配置的变量"不生效"）
- **报错/现象**：`CLAUSEGUARD_AGENT_*` 配了却不生效；从 `python-agent/` 启动读不到根 `.env`。
- **根因**（诊断证据）：
  - `AgentSettings`（`app/agents/settings.py`）**无 `env_file`** → `CLAUSEGUARD_AGENT_*` 只认进程环境变量；
  - `Settings`（`app/config.py`）`env_file=".env"` 相对 CWD → 从 `python-agent/` 启动读到不存在的 `python-agent/.env`，根 `.env` 实际没被读。
  - 两种 CWD 各跑一次，输出对比：修复前 `AgentSettings.env_file=None`、根 `.env` 仅在仓库根启动时被读。
- **修复**：两 Settings 类统一用 `Path(__file__).resolve().parents[N] / ".env"` 按文件位置定位仓库根 `clauseguard/.env`；`AgentSettings` 加 `env_file=ENV_FILE`，前缀不变。优先级：**进程环境变量 > .env > 代码默认**。
- **验证**：修复后两种 CWD 输出逐字一致；新增 `tests/test_config_loading.py`（5 测全绿，用 tmp `.env` 隔离，不读真实密钥）；`pytest` 全量退出码 0。

### 🐞 7. 阶段 15 — Docker 拉镜像失败（环境阻塞）
- **报错/现象**：`docker compose up -d` 失败：`failed to unpack image ... unexpected media type text/html ... not found`。
- **根因**：仓库/镜像源返回 HTML 错误页（网络/代理问题），非代码 bug。
- **影响**：MySQL/Postgres/Redis/MinIO 起不来，端到端 live/mock 实跑无法在沙箱完成。已把精确运行命令写进文档交用户本机执行。

### 🐞 8. 运维 — 本机 curl 返 502（假死相）
- **现象**：`curl localhost:8080/actuator/health` 返 502，看着像服务挂了。
- **根因**：走系统代理请求本地服务。
- **修复**：排查端口加 `--noproxy '*'`（curl）或 `NO_PROXY=localhost,127.0.0.1`（Python 连本地 Java）。

### 🐞 9. 运维 — 连真实 LLM 报 401
- **报错/现象**：设了 `CLAUSEGUARD_AGENT_LLM_API_KEY` 仍 401。
- **根因**：驱动模型调用的是 `AgentSettings`，默认 `llm_base_url=https://api.openai.com/v1`、`llm_model=gpt-4o-mini`；只设 key 不覆盖 base_url/model，请求打到 OpenAI 用别家 key 必 401。且 `CLAUSEGUARD_AGENT_LLM_PROVIDER` 变量**根本不存在**（曾误说）。
- **修复**：连 DashScope 设 `CLAUSEGUARD_AGENT_LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1` + `LLM_MODEL=qwen-plus`；连 DeepSeek 设 `base_url=https://api.deepseek.com/v1` + `model=deepseek-chat`。外网走代理、本地用 `NO_PROXY` 直连。

### 🐞 10. 加固 — 合同列表的 N+1 与缺失索引
- **现象**：`GET /api/v1/contracts?limit=20` 会发 20 条 `select ... where contract_id=? order by created_at desc limit 1`；`review` 表除主键外没有任何索引，按 `contract_id` 过滤就是全表扫。
- **根因**：阶段 12 加合同列表时给 `contract` 建了 `idx_contract_created`，但「取最近一次审查」落在 `review` 上，那条路没建索引；同时 `ContractController.toSummary` 是逐行调仓储，典型的 N+1。
- **修复**：
  - `V6__review_lookup_index.sql` 补 `review(contract_id, created_at)` 与 `review_event(review_id, ts)`（后者让阶段 13/14 的 `MAX(ts)` 不再回表）。只加索引、不改列；不写 `IF NOT EXISTS`（MySQL 8 不支持，而同一份迁移要在 H2 上跑）。
  - `ReviewService.latestReviews(ids)`：一条 `IN` 查全部，内存里 `putIfAbsent` 取每合同首条；`ContractService`/`ContractController` 不再各自查一次。
- **验证**：`mvn test` 132 通过（Flyway 在 H2 上执行过 V6）；响应字段与取值不变。

### 🐞 11. 加固 — 检索库 DSN 的错误默认值（静默 0 命中）
- **现象**：没有异常、没有告警，检索返回 0 命中，审查照常跑完，只是结论全错。
- **根因**：`retrieval/config.py` 的 `pg_dsn` 默认串是 `localhost:5432/clauseguard_agent`——那是 LangGraph checkpoint 库；真实检索库是 `55432/clauseguard_retrieval`。pydantic 的优先级是 环境变量 > `.env` > yaml > 代码默认，只要 yaml 缺失或换机器没配，就会静默连上另一个库。
- **修复**：默认值改空串，`ChunkStore.__init__` 在构造期抛 `ValueError` 并带配置示例——**宁可在启动时炸，也不要跑出一份看起来正常的错误报告**。
- **配套**：密码不进 `python-agent/config/retrieval.yaml`（该文件提交进仓库），改由 `.env` 的 `CLAUSEGUARD_RETRIEVAL_PG_DSN` 提供（`.env` 已 gitignore），README 快速开始与 `.env.example` 同步说明。
- **验证**：免 `PGPASSWORD` 连库 `law_chunk = 13191`；`ChunkStore('')` 直接抛错。

### 🐞 12. 加固 — 未映射异常把内部信息带进响应体
- **现象**：任何没被映射的异常（空指针、SQL 异常）走 Spring 默认处理，500 响应可能带出堆栈、表名、本机绝对路径。
- **根因**：`GlobalExceptionHandler` 只映射了 6 个业务异常，没有兜底。
- **修复**：加 `@ExceptionHandler(Exception.class)`，**先判断 `ex instanceof ErrorResponse` 并原样放行**（Spring 的 404/405/415 都是 ErrorResponse，状态码本身安全），其余记 `log.error` 带堆栈、对外只回固定文案「服务内部错误，请稍后重试」。
- **关键点**：不判 `ErrorResponse` 直接吞 `Exception`，未知路径会从 404 变成 500——「路径写错」被误报成「服务挂了」，比泄露堆栈更误导人。
- **验证**：临时 `@WebMvcTest` 三用例——未知路径 404、`POST` 打 `GET` 端点 405、抛 `IllegalStateException("/srv/secret")` → 500 + `INTERNAL_ERROR` 且响应体里查不到 `/srv/secret`；验完删除，全量 132 仍绿。

### 🐞 13. 加固 — Controller 跨层直连别人的 Repository
- **现象**：`ContractController` 注入 `ReviewRepository` 直接查审查（合同列表/详情各一处）。
- **根因**：合同模块需要「最近一次审查」，但 `ReviewService` 当时没有对应的只读方法，图省事就直接注了仓储；顺带把 N+1 也带进来了（见 10）。
- **修复**：`ReviewService` 新增 `record LatestReview(reviewId, status)` 与 `latestReview(contractId)` / `latestReviews(contractIds)` 两个只读方法；Controller 只依赖 `ContractService` + `ReviewService`，返回轻量视图而不是整个 `Review` 实体（实体含 `pending_payload` 这类大字段）。
- **验证**：`last_review` 字段名、状态小写取值、「无审查则不输出该字段」全部保持原样；132 测试全绿，`ContractControllerTest` 删掉已无引用的 `@MockBean ReviewRepository`。

### 🐞 14. 加固 — 同一 clause 被审批两次（CAS 之后仍有的窗口）
- **现象**：同一个 review 的同一个 clause 在 `approval` 里有两行。实测全表 23 行只对应 19 个唯一 `(review_id, clause_id)`，即 4 组重复。
- **根因**：`approval` 只有 `INDEX idx_approval_review (review_id)`，**没有任何唯一约束**。状态 CAS 挡的是「两个请求同时从 AWAITING_APPROVAL 抢同一份快照」，它看的是 review 状态、不看请求体——所以「一次请求里 approvals 数组带了两遍同一 clause」它拦不住；历史上「一条审批一个独立事务」时代留下的半成品它也拦不住。
- **修复**：三层，各管一段，顺序不能反：
  1. `V7` 加 `uk_approval_review_clause UNIQUE (review_id, clause_id)`——最后一道兜底；
  2. `ApprovalApplier` 写前用 `findByReviewId` 取已有 clause 集合，已在库里的**跳过不覆盖**（审批是一次性决定，以第一次为准），同一个集合顺带挡掉请求体内的重复；
  3. `ReviewService.approveReview` 捕获 `DataIntegrityViolationException` 后**回查库里是否真的落了这批 clause**：全部命中才当幂等返回 `accepted` 且**不再发 resume**（赢家已经在续跑，再发一次 Python 会收到两份审批结果）；一个都没落库说明是别的原因撞约束（字段超长之类），照实往上抛。
- **关键点**：**不能在 `@Transactional` 方法内部 catch 约束冲突后继续往下走**。一旦 JDBC 抛出完整性违规，事务已被标记 rollback-only，方法正常返回后提交会抛 `UnexpectedRollbackException`——比原来的 500 更难查。所以捕获必须放在**事务边界之外**（`ApprovalApplier` 是独立 Bean，异常穿出代理时回滚已完成）。
- **脏数据处理**：迁移里**不写 DELETE**——本机有重复不代表别的环境也有，写死 DML 只会误删。正确顺序是先跑 `GROUP BY ... HAVING c > 1`，有结果先人工处置。本次 4 组重复的两行在 decision/operator/时间上完全一致（同一次演示的双写），按「保留 id 最大」删掉 4 行；删前整表 `mysqldump` 到 `.backup/`。
- **验证**：约束加上后插重复行直接 `ERROR 1062 ... for key 'approval.uk_approval_review_clause'`；单测新增 4 条（同请求重复 clause 只写一条 / 库内已存在则跳过不覆盖 / 撞约束且已落库→`accepted` 且不发 resume / 撞约束但库里没有→重抛），全量 137 通过。

### 🐞 15. 运维 — 迁移文件提交了，本地库却没跑过
- **现象**：`V6`（两个索引）早已提交，但 `flyway_schema_history` 只到 v5，库里查不到 `idx_review_contract_created`。也就是说「索引补上了」这件事在本地从未发生，只是代码仓库里有这个文件。
- **根因**：全量测试里的 `@SpringBootTest` 要么是 Web 切片（不加载数据源），要么仓储全是 `@Mock`；唯一真正连库的集成测试又显式 `spring.flyway.enabled=false`。于是「迁移能不能应用」从来没被任何一次测试验证过——**测试全绿 ≠ 迁移能跑**。
- **修复**：分两步。第一步把本地库补到 v7（flyway-maven-plugin 实跑 `migrate`，v5 → v7），逐个 `SHOW INDEX` 核对；第二步**让迁移进测试**——新增 `FlywayMigrationTest`：`flyway.validate()` 查 checksum、`info().pending()` 必须为空、`information_schema.STATISTICS` 断言 V1/V6/V7 的五个索引都在、再往 `approval` 插一对重复 `(review_id, clause_id)` 断言被约束拒绝（事务内插完回滚，不在审计表留痕）。
- **为什么不 clean + 从零重建**：库账号只有 `clauseguard.*` 权限，建不了独立 schema；在业务库上 clean 等于把开发数据全删。对「迁移没跑」这个失败模式，**校验 pending 比从头建库更直接**，且零破坏。
- **为什么命名 `*Test` 而不是 `*IT`**：surefire 默认只收 `*Test`，叫 IT 就得再引 failsafe，而「默认不跑」正是这类测试最怕的状态。
- **反证**：临时塞一个 `V99__probe.sql` 进去，`FlywayMigrationTest` 立刻红（`Migrations have failed validation`）；删掉恢复绿。**一个永远绿的测试等于没有测试。**
- **关键点**：迁移的正确性只能靠「真库跑一遍」证明。把 `migrate` 当成发布流程里的独立一步，不要假设「应用一启动就自动好了」——启动失败时 Flyway 往往不是第一个报错的组件，很容易被别的错误盖住。

### 🐞 16. 设计 — 待审项里同一个 clause 出现两次（上一轮 4 组重复的真根因）
- **现象**：拿一份 AWAITING_APPROVAL 的 review 实跑审批，21 条待审项提交后只落了 **17** 条。
- **根因**：`human_approval.build_interrupt_payload` 按 **finding** 逐条生成 `items`，而一条条款可能被多个风险维度同时命中——实测 4 个 clause 各出现两次，且两次的 `risk_type` 与 `suggestion` 完全不同（`payment_risk` + `disclaimer_risk`、`delivery_risk` + `breach_risk`…）。所以「同一 clause 两条审批记录」不是用户连点两次，是**上游 payload 本来就有重复**。
- **影响**：Java 侧按 clause 去重保住了唯一约束，但代价是——如果用户对同组第二条改过措辞，那个编辑会被静默丢弃（保留第一条）。前端展示 21 条、实际生效 17 条，对不上。
- **待定方案**：① Python 侧在 `build_interrupt_payload` 里按 clause_id 聚合（risk 取最高、suggestion 合并），让「待审数量 = 落库数量」；② Java 侧把唯一键改成 `(review_id, clause_id, risk_type)` 保留 21 条——但这样同一条款可能拿到两个互相矛盾的决定，语义更乱。倾向 ①。


---

## 三、经验 / 面试可讲的要点
1. **mock 单测覆盖不到真实 JPA/Hibernate 绑定与事务边界**——关键路径必须跑真实 DB 实机验收（阶段 14 两个真 bug 都是这么揪出来的）。
2. **时区是隐形杀手**：`Instant`/`DATETIME` 绑定偏 8h、`TIMESTAMP` 四舍五入偏 1s——统一用 UTC 字符串字面传参最稳。
3. **`@Modifying` 必须有事务**：自调用也要在代理开启的事务内。
4. **配置来源要可预测**：别靠 CWD 解析 `.env`，用 `Path(__file__)` 定位根配置文件。
5. **前端代理 / 系统代理是本地联调的高频坑**：代理会把本地请求导到外网，表现成 502/500，排查时先排除代理。
6. **默认值是一种承诺**：给数据库连接串配一个"看起来能用"的默认值，等于承诺"配置丢了也能跑"。连错库不会报错，只会安静地返回 0 命中——这种 bug 比启动失败难查一个数量级，所以宁可让构造期直接抛错。
7. **全局兜底异常前先放行框架语义**：`ErrorResponse`（404/405/415）自带正确的状态码，一律按 500 兜底会把"路径写错"变成"服务挂了"。
8. **索引跟着查询形态建**：`(过滤列, 排序列)` 联合索引，把"按合同过滤 + 取最新一条"一次走完；判断有没有索引要看实际 SQL，不是看表有没有主键。
9. **唯一约束不能拿来当日常路径**：约束是兜底，不是入口。靠它挡重复点击，只会把"刷新一下就好"变成 500。先写前判重、再靠约束兜漏网的并发窗口，顺序反了体验就崩。
10. **事务内 catch 完整性违规等于没修**：连接被标记 rollback-only 后，方法正常返回也会在提交时炸成 `UnexpectedRollbackException`。要幂等化，捕获必须落在事务边界外。
11. **迁移文件存在 ≠ 数据库已迁移**：所有测试都不连真库时，"schema 有变更"只是一句仓库里的话。发布前必须独立跑一次 `migrate` 并核对实际索引。
12. **测试要先证明自己能红**：新加的校验测试，故意造一次违规看它是否报警；永远绿的测试只是安慰剂。
13. **去重会掩盖上游的重复**：下游按业务键去重（保住唯一约束）之后，多余数据的来源就不再有症状了。该顺手往上追一层——这次的「重复审批」根因根本不在审批接口，在生成待审项的地方。

## 四、提交记录（对应修复）
- `dd7e8f2` 阶段 13（含看门狗 Repository 方法预置）
- `dd7d905` 阶段 14 看门狗初始
- `9c5a15b` 看门狗实机修复（bug2 + bug3）
- 阶段 15 修复随后续提交（bug6 + 启动脚本 + 测试）
- `a306ed5` bug10 索引（V6）
- `86f6aad` bug10 + bug13 分层与批量取最近审查
- `21058a0` bug11 检索库 DSN fail-fast
- `3d54277` bug12 异常兜底
- `cbb8479` 配套文档：README 快速开始 + `.env.example` 说明 DSN 密码来源

> 哈希以 `git log` 为准：2026-11-15 首次推送 GitHub 前做过一次「统一提交人身份」的历史重写（只改 author/committer，文件内容零差异），此前文档里引用的旧哈希已一并更新。
