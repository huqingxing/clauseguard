# clauseguard 构建 / 实现过程中遇到的 Bug 与报错汇总

> 用途：复盘、面试讲解「改哪里 / 为什么 / 怎么修」。
> 范围：阶段 13（last_event_at）、阶段 14（看门狗回收）、阶段 15（.env 半失效），以及本地跑通时的运维坑。
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

---

## 三、经验 / 面试可讲的要点
1. **mock 单测覆盖不到真实 JPA/Hibernate 绑定与事务边界**——关键路径必须跑真实 DB 实机验收（阶段 14 两个真 bug 都是这么揪出来的）。
2. **时区是隐形杀手**：`Instant`/`DATETIME` 绑定偏 8h、`TIMESTAMP` 四舍五入偏 1s——统一用 UTC 字符串字面传参最稳。
3. **`@Modifying` 必须有事务**：自调用也要在代理开启的事务内。
4. **配置来源要可预测**：别靠 CWD 解析 `.env`，用 `Path(__file__)` 定位根配置文件。
5. **前端代理 / 系统代理是本地联调的高频坑**：代理会把本地请求导到外网，表现成 502/500，排查时先排除代理。

## 四、提交记录（对应修复）
- `c15f729` 阶段 13（含看门狗 Repository 方法预置）
- `55b5c70` 阶段 14 看门狗初始
- `dc71a87` 看门狗实机修复（bug2 + bug3）
- 阶段 15 修复随后续提交（bug6 + 启动脚本 + 测试）
