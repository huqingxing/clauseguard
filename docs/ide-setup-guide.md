# ClauseGuard 三 IDE 从零打开到跑起来 · 环境清单

> 适用对象：本项目（`clauseguard` monorepo）。所有命令均基于本机实测 + `README.md` / `Makefile` 的真实约定。
> 生成日期：2026-10-28。代码根目录：`C:\Users\27600\WorkBuddy\2026-10-22-21-46-35\clauseguard`

---

## 0. 本机环境实测结果（先看清缺什么）

| 项目 | 状态 | 路径 / 版本 | 备注 |
| --- | --- | --- | --- |
| IntelliJ IDEA | ❌ **未安装** | — | PATH 里的 bin 是失效残留，需先装 |
| PyCharm | ✅ 已装 | `D:\develop\pythno\PyCharm 2026.1.2` | 2026.x，足够新 |
| VS Code | ✅ 已装 | `D:\devdlop\Microsoft VS Code` v1.142.0 | 自带 `code` 命令 |
| JDK 25 | ✅ 已装 | `C:\Program Files\Java\jdk-25` | `java-service` 实际用 25（README 写的 21 已过时，以实测为准） |
| JDK 17 | ✅ 在 PATH | `C:\Program Files\Java\jdk-17` | 是默认 `java`，**IDE 里需手动切到 25** |
| Maven | ✅ 已装 | 3.9.4（`D:\develop\apache-maven-3.9.4`） | |
| Node | ✅ 已装 | v22.22.2 | `web` 要求 ≥ 22.18，满足 |
| Python | ✅ 已装 | 3.13.14 | |
| uv | ✅ 已装 | 0.12.5 | `python-agent` 包管理 |

> ⚠️ 结论：只有 **IDEA 需要新装**。PyCharm / VS Code 直接打开即可。运行时不缺，但 JDK 默认是 17，必须在 IDEA 里显式指定 25。

---

## 1. 模块 ↔ IDE 对照（别用错 IDE 开错目录）

| 子项目 | 目录 | 技术 | 用哪个 IDE 打开 | 识别文件 |
| --- | --- | --- | --- | --- |
| 业务网关 | `java-service` | Spring Boot 3.5 / Maven / JDK 25 | **IntelliJ IDEA** | `pom.xml` |
| 编排层 | `python-agent` | Python 3.13 / uv / LangGraph | **PyCharm** | `pyproject.toml` + `uv.lock` |
| 前端 | `web` | Vue3 / Vite / Node 22 | **VS Code** | `package.json` + `vite.config.ts` |

> 整个 `clauseguard` 根目录**不要**用一个 IDE 当单项目打开（语言会混）。三件套各开各的目录。

---

## 2. 一次性前置：基础设施（MySQL / Redis / PostgreSQL / MinIO）

项目跑起来需要四个中间件。两种起法，**二选一**：

### 方案 A：Docker Compose（README 推荐，最省心）
```bash
cd clauseguard
cp .env.example .env        # 首次必做
make up                    # 起 mysql / postgres / redis / minio
```
要求本机有 Docker Desktop 且已启动。

### 方案 B：用本机已装的服务（本机已在跑，可跳过多数）
- **MySQL 8**：本机 `localhost:3306`，库名 `clauseguard`，账号 `clauseguard/clauseguard`（见 `application.yml` 的 `datasource`）。Flyway 自动建表。
- **Redis**：本机 `localhost:6379`，**必须 ≥ 5.0**（事件重放依赖 Streams `XADD`/`XRANGE`）。先确认版本：
  ```bash
  redis-cli INFO server | grep redis_version
  ```
  ⚠️ 若本机还有老的 `D:\develop\redis` 3.x Windows 版抢占了 6379，会直接导致 SSE 端点 500。临时降级（仅排障，断线续传失效）：启动 Java 时加 `--clauseguard.event-store=in-memory`。
- **PostgreSQL**：仅 Python 编排的 checkpoint 用。本机没装就走 `CLAUSEGUARD_AGENT_PG_DSN=` 空值（mock 演示模式不强制），可先不装。
- **MinIO**：文档/附件存储，本机未配置时 `python-agent` 自动回退到本地 `LocalStorageClient`，可先不装。

---

## 3. IntelliJ IDEA（先安装，再打开）

### 3.1 安装
1. 去 https://www.jetbrains.com/idea/download 下载 **Community（免费）** 或 Ultimate。
2. Community 版不内置 Spring 框架提示，安装后在 `File → Settings → Plugins` 搜 **Spring Boot** / **Spring Assistant** 装上（不影响编译运行，只影响智能提示）。
3. 安装时勾选 “Add launchers to PATH”（可选）。

### 3.2 打开并配置
1. `File → Open` → 选 `clauseguard/java-service`（**不是** 根目录）。
2. 首次打开会提示 “Maven projects need to be imported” → 选 **Load Maven Project**（或右侧 Maven 面板点刷新）。
3. 配置 **Project SDK = JDK 25**：
   - `File → Project Structure → SDKs → + → Add JDK` → 指向 `C:\Program Files\Java\jdk-25`
   - `Project → SDK` 选刚加的 25；`Language level` 设为 25（或 21+）。
4. 配置 Maven：
   - `File → Settings → Build → Maven` → Maven home 指向 `D:\develop\apache-maven-3.9.4`（或用 Bundled）。
   - 设置里 `Maven → Runner → JRE` 也选 JDK 25。

### 3.3 运行
- 主类 `com.clauseguard.ClauseGuardApplication`。
- 直接在 `ClauseGuardApplication.java` 上点绿色箭头 `Run`，或建 Run Configuration：
  - `Run → Edit Configurations → + → Spring Boot`
  - Main class：`com.clauseguard.ClauseGuardApplication`
  - **VM options / Program arguments**：默认端口 8080 即可（前端代理写死 8080，不要改成 8099 否则前端 500）。如需显式：`--server.port=8080`
- 启动后看控制台 `Started ClauseGuardApplication`，或：
  ```bash
  curl --noproxy '*' localhost:8080/healthz          # {"status":"ok"}
  curl --noproxy '*' -u admin:admin123 localhost:8080/api/v1/admin/health   # 200
  ```
  > 管理端账号见 `java-service/src/main/resources/application.yml` 的 `clauseguard.security.users`：`admin / admin123`（ADMIN）、`user / user123`（USER，访问 admin 接口会 403）。

---

## 4. PyCharm（已装，直接打开）

### 4.1 打开并建解释器
1. `File → Open` → 选 `clauseguard/python-agent`。
2. 配 Python 解释器（用 uv 建虚拟环境）：
   - `File → Settings → Python Interpreter → Add → uv`（PyCharm 2026 原生支持 uv）。
   - 或终端：`cd python-agent && uv sync`（生成 `.venv`）。
3. 把解释器指向 `python-agent/.venv/Scripts/python.exe`。

### 4.2 运行配置（关键：环境变量 + 去代理）
`Run → Edit Configurations → + → Python`：
- Script path：`python-agent/app/main.py`（实际用 uvicorn 起，见下 Target）
- 推荐 Target 选 **Module name** = `uvicorn`，Parameters：`app.main:app --host 127.0.0.1 --port 8000`
- Working directory：`clauseguard/python-agent`
- **Environment variables**（必须）：
  ```
  CLAUSEGUARD_AGENT_MODEL_BACKEND=mock      # 无密钥演示；去掉即走真实模型
  CLAUSEGUARD_AGENT_PG_DSN=                 # 空 = 不强制 PG（mock 模式）
  JAVA_BASE_URL=http://localhost:8080       # 注意：主 Settings 无前缀，是 JAVA_BASE_URL 不是 CLAUSEGUARD_AGENT_JAVA_BASE_URL
  HTTP_PROXY=                               # 清空本机代理，否则连不上本地 Java（报 502）
  HTTPS_PROXY=                              # 同上
  ```
> ⚠️ 本机若开着系统代理，Python 起服务后调 Java 会 502。最稳：在 PyCharm 运行配置里把 `HTTP_PROXY`/`HTTPS_PROXY` 设为空字符串。

### 4.3 验证
```bash
curl --noproxy '*' localhost:8000/healthz   # {"status":"ok","service":"python-agent"}
```

---

## 5. VS Code（已装，直接打开）

### 5.1 打开并装扩展
1. `File → Open Folder` → 选 `clauseguard/web`（前端只开这个）或整个 `clauseguard`（要看全栈）。
2. 扩展商店装：
   - **Vue - Official**（Volar，Vue3 官方）
   - **ESLint**
   - （可选）TypeScript Vue Plugin

### 5.2 安装依赖并运行
```bash
cd clauseguard/web
npm install
npm run dev          # Vite 起 5173
```
- 前端 `/api` 代理已写死 `vite.config.ts` → `http://localhost:8080`，所以 **Java 必须在 8080**。
- 验证：浏览器开 `http://127.0.0.1:5173`（用户端）、`http://127.0.0.1:5173/admin.html`（管理端，登录 `admin / admin123`）。

---

## 6. 端到端启动顺序（必须按这个来）

```
① 基础设施（MySQL/Redis/PG/MinIO）      ← make up 或本机已有
        ↓
② Java 业务网关 :8080        （IDEA 或 mvn spring-boot:run）
        ↓
③ Python 编排层 :8000        （PyCharm 或 make run-py MODEL_BACKEND=mock）
        ↓
④ 前端 :5173                 （VS Code → npm run dev）
```

等价命令行（不用 IDE 也能跑，已本机验证）：
```bash
# ② Java
cd java-service && mvn spring-boot:run                # 默认 8080

# ③ Python（去代理 + mock）
cd python-agent && CLAUSEGUARD_AGENT_MODEL_BACKEND=mock CLAUSEGUARD_AGENT_PG_DSN= \
  uv run uvicorn app.main:app --host 127.0.0.1 --port 8000

# ④ 前端
cd web && npm install && npm run dev
```

端到端自检（README 第 74 行起的那张表精简版）：
| 检查 | 命令 | 期望 |
| --- | --- | --- |
| Java | `curl --noproxy '*' localhost:8080/healthz` | `{"status":"ok"}` |
| Python | `curl --noproxy '*' localhost:8000/healthz` | `{"status":"ok","service":"python-agent"}` |
| 鉴权 | `curl --noproxy '*' -u admin:admin123 localhost:8080/api/v1/admin/health` | 200 |
| 前端 | 浏览器开 `http://127.0.0.1:5173` | 合同列表页 |
| 管理端 | 浏览器开 `http://127.0.0.1:5173/admin.html` | 登录卡（输入 admin/admin123） |

---

## 7. 常见坑速查

- **前端报 “后端出错了（HTTP 500）”**：不是密码错（错密码是 401）。99% 是 Java 没在 8080（之前验收用过 8099），Vite 代理连不上。把 Java 起回 8080 即可。
- **curl 端口返回 502**：本机系统代理拦截。加 `--noproxy '*'` 绕过。
- **Python 调 Java 报 502**：PyCharm / 终端里 `HTTP_PROXY`/`HTTPS_PROXY` 没清空。
- **Redis 版本过低 / SSE 500**：确认 `redis-cli INFO server` ≥ 5.0。
- **mvn 报 class file version 65.0**：JDK 用成了 17，切到 25。
- **管理端登不进**：账号 `admin / admin123`（不是 `admin123`）。`application.yml` 里是明文，生产必须换。
- **mvn spring-boot:run 不带端口参数偶尔起在随机高位端口**：显式加 `--server.port=8080`。
