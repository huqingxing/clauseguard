# Stage 15｜把模型配置切到 live，并修掉「.env 半失效」

## 根因（已核对代码，属实）
1. `app/agents/settings.py` 的 `AgentSettings` 是 `SettingsConfigDict(env_prefix="clauseguard_agent_", extra="ignore")` —— **没有 `env_file`**，所以 `CLAUSEGUARD_AGENT_*` 只读进程环境变量，`.env` 写了也没用。
2. `app/config.py` 的 `Settings` 有 `env_file=".env"` —— 路径相对**启动时的 CWD**。从 `python-agent/` 启动时它找的是 `python-agent/.env`（不存在），根目录的 `clauseguard/.env` 实际没被读。
3. 结论：两组配置读取规则不一致，且 `.env` 来源由 CWD 决定 → 半失效。

---

## 1. 诊断证据（修复前，两种 CWD 对比）
最小诊断进程打印 `Settings().java_base_url` / `AgentSettings().llm_model` / `.llm_base_url` 以及各自解析出的 `env_file` 绝对路径。

**从仓库根 `clauseguard/` 启动：**
```
CWD: ...\clauseguard
--- Settings (app/config.py) ---
  env_file(raw): .env
  env_file(resolved): ...\clauseguard\.env
  java_base_url: http://localhost:8080
--- AgentSettings (app/agents/settings.py) ---
  env_file: None
  llm_model: gpt-4o-mini
  llm_base_url: https://api.openai.com/v1
```

**从 `python-agent/` 启动：**
```
CWD: ...\clauseguard\python-agent
--- Settings (app/config.py) ---
  env_file(raw): .env
  env_file(resolved): ...\clauseguard\python-agent\.env   <-- 不存在的路径
  java_base_url: http://localhost:8080
--- AgentSettings (app/agents/settings.py) ---
  env_file: None                                        <-- 完全不读 .env
  llm_model: gpt-4o-mini
  llm_base_url: https://api.openai.com/v1
```
**事实结论**：`Settings` 读哪份 `.env` 由 CWD 决定（根目录 vs 不存在的 `python-agent/.env`）；`AgentSettings` 根本不读 `.env`。值相同只是因为根 `.env` 没有覆盖这些字段，路径解析差异才是铁证。

---

## 2. 变更摘要（文件 + 一句话）
| 文件 | 变更 |
| --- | --- |
| `python-agent/app/config.py` | 新增 `ENV_FILE = Path(__file__).resolve().parents[2] / ".env"`，`env_file` 由 `".env"` 改为 `ENV_FILE`（按文件位置定位根 `.env`，不再随 CWD 漂移）。 |
| `python-agent/app/agents/settings.py` | 新增 `ENV_FILE = Path(__file__).resolve().parents[3] / ".env"`，`model_config` 增加 `env_file=ENV_FILE`，前缀 `clauseguard_agent_` 保持不变。 |
| `python-agent/tests/test_config_loading.py` | 新增 5 个测试：两 Settings 共享同一根 `.env`、进程环境变量 > `.env` 的优先级、AgentSettings 读带前缀键、`.env` 只匹配前缀键。 |
| `deploy/dev/start-agent-live.ps1` | 新增：live 模式启动脚本（仅非密钥变量，密钥走系统 `CLAUSEGUARD_AGENT_LLM_API_KEY`）。 |
| `deploy/dev/start-agent-mock.ps1` | 新增：mock 模式启动脚本（仅把 `MODEL_BACKEND` 设为 mock）。 |
| `README.md` | 新增「python-agent 启动方式（PyCharm / 脚本）」段：PyCharm 运行配置等价写法 + 配置加载优先级说明。 |

**优先级（已写入代码注释与 README）**：进程环境变量 > `.env` 文件 > 代码默认值。

---

## 3. 验收表
| 项 | 命令 | 实际输出 | 结论 |
| --- | --- | --- | --- |
| 诊断（修复前两种 CWD） | 见 §1 | 路径解析不同 / AgentSettings 无 env_file | ✅ 复现半失效 |
| 修复后两种 CWD 一致（§4.1） | 见 §4.1 | 两份输出完全一致 | ✅ |
| 配置加载测试 | `pytest tests/test_config_loading.py` | 5 passed | ✅ |
| 不破坏既有测试 | `pytest tests/agents/test_commercial.py` | 10 passed（含 `llm_base_url==openai.com` 默认断言） | ✅ |
| 密钥卫生：`git grep sk-` | `git grep -in "sk-\|Bearer [A-Za-z0-9]"` | 仅命中 `risk-01..10`(ri**sk-**)、Bearer 注释、`sk-...`占位、`sk-test/sk-one/sk-two` 测试假值 | ✅ 无真实密钥 |
| 密钥卫生：精确长密钥 | `git grep -inE "sk-[A-Za-z0-9]{20,}\|Bearer [A-Za-z0-9]{20,}"` | 0 命中 | ✅ |
| 密钥卫生：`.env` 被忽略 | `git check-ignore .env` | `.env`（来自 `.gitignore:18`） | ✅ |
| 完整 pytest | `pytest -q`（python-agent 全量） | 退出码 0，100% 无 failure/error（safe-delete 仅为临时目录清理） | ✅ 全绿 |
| Java 单测 | `mvn test` | 本次未改 java-service，超出范围；且需 MySQL（当前 Docker 起不来） | ➖ 不适用 |

---

## 4. 修复后两种 CWD 完全一致（Task 4.1）
**从根启动：**
```
env_file(resolved): ...\clauseguard\.env
AgentSettings.env_file: ...\clauseguard\.env
java_base_url: http://localhost:8080
llm_model: gpt-4o-mini
llm_base_url: https://api.openai.com/v1
```
**从 `python-agent/` 启动：**
```
env_file(resolved): ...\clauseguard\.env
AgentSettings.env_file: ...\clauseguard\.env
java_base_url: http://localhost:8080
llm_model: gpt-4o-mini
llm_base_url: https://api.openai.com/v1
```
两份输出逐字一致，且 `AgentSettings.env_file` 不再是 `None` → 修复生效。

---

## 5. live / mock 实跑（Task 4.2 / 4.3）—— 受阻，需你本机执行
**本沙箱无法完成端到端实跑**：`docker compose up -d` 拉取镜像时镜像仓库返回 HTML 错误页
（`failed to unpack image ... unexpected media type text/html ... not found`），导致 MySQL / Postgres /
Redis / MinIO 全部起不来；而审查链路至少需要 Java + MySQL，故无法在此环境跑通真实审查。

**请你在本机（Docker 正常）按以下顺序验证**，即可拿到 spec 要求的真实 token / 具体 findings 证据：
```bash
# 1. 基础设施（mysql/postgres/redis/minio）
make up
# 2. 业务网关（默认 8080）
cd java-service && mvn spring-boot:run
# 3. 编排层 live（脚本已封装非密钥变量；密钥来自系统变量 CLAUSEGUARD_AGENT_LLM_API_KEY）
cd python-agent && pwsh deploy/dev/start-agent-live.ps1
# 4. 提交一份审查（前端 5173 上传 deploy/demo/demo-contract.docx，或用 API）
#    期望：Python 日志出现真实 token 数（非 mock 固定 360/160），findings.summary 为具体内容而非 "[mock] ..."
```
切回 mock：`./deploy/dev/start-agent-mock.ps1`，summary 应回到 `[mock]` 形态。

---

## 6. 密钥卫生（Task 4.4 原始命令结果）
```
$ git check-ignore .env
.env
$ git grep -inE "sk-[A-Za-z0-9]{20,}|Bearer [A-Za-z0-9]{20,}"
0 命中（无真实长度的密钥）
```
（宽松 `sk-` 命中的全是 `risk-01..10`、`Bearer` 注释、`sk-...` 文档占位、`sk-test/sk-one/sk-two`
测试假值，均非真实凭证；真实 `.env` 已被 `.gitignore:18` 忽略。）

---

## 7. 禁止项核对
- ✅ 未把任何密钥写入 `.env` / 脚本 / 文档 / 测试 / 示例（root `.env` 未改动，密钥仅在系统环境变量）。
- ✅ 未改 python-agent 编排/审查逻辑，只动配置加载与启动方式。
- ✅ 未引入 python-dotenv 等新依赖（沿用 pydantic-settings 自带 `.env` 支持）。
- ✅ 未做「读不到 key 就静默降级 mock」（项目原本禁止，未破坏）。
- ✅ 测试未读取开发者本机 `.env`（全部用 `tmp_path` + `_env_file` 隔离）。
