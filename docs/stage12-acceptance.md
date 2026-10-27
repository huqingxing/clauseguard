# Stage 12 验收报告｜前端工作台

> 目标：把已有的后端能力变成**能点、能演示**的界面——6 条路由、三栏工作台、
> SSE 断线重连、375px 移动端审批页。后端接口在此之前已就绪并有测试，
> 本阶段不动契约、不动状态字段，只补前端与两处被前端暴露出来的缺陷。

- 代码版本：Stage 12 提交（`git log` 中「Stage 12 前端工作台」）
- 环境：Windows 11 / Node 20 / Chrome（headless）/ JDK 21 / Python 3.13
- 运行形态：web 5173（vite dev）· java 8080（`--clauseguard.event-store=in-memory`）·
  python 8000（`CLAUSEGUARD_AGENT_MODEL_BACKEND=mock`）· MySQL 8（本机 3306）

## 1. 交付内容

### 1.1 两个入口：用户端 3 条路由 + 管理端 4 条路由

> 本节已按阶段 3–7 的实际形态更新。原 Stage 12 的形态是「用户端 4 路由（含 `/reviews/:id/approve`）
> + 管理端 2 屏手切 hash」，下面括号里保留当时的差异，便于对照。

用户端 `index.html`（`/`）：

| 路由 | 页面 | 职责 |
| --- | --- | --- |
| `/` | 合同列表 | 上传、搜索、看最近审查状态、进入工作台/查看结果 |
| `/contracts/:id` | 审查工作台 | 三栏：原文 / 风险发现·条款变更 / 运行态；发起全量或增量审查 |
| `/reviews/:id` | 审查结果 | 只读落地页，可分享给同事；批注稿下载；等待审批时只显示「等待法务审批中」 |

用户端不再有审批路由：`/reviews/:id/approve` 已迁到管理端 `#/approvals/:id`（阶段 5），
旧链接由 catch-all 落到 **404 页**（阶段 9 改的：原先静默回 `/`，会被读成「跳错页了」，
现在 404 页直接写明审批已迁到管理端）。用户端没有任何指向 `/admin`、`/eval` 的链接，也没有「隐藏入口」。

管理端 `admin.html`（`/admin`，**hash 路由**，阶段 4 起是真的 router）：

| 路由 | 屏 | 职责 |
| --- | --- | --- |
| `#/health` | 运维健康 | Java + Python 双栈状态，10 秒轮询（`/admin` 默认落这里） |
| `#/eval` | 评测看板 | token / 成本 / 质量 / 增量收益 |
| `#/approvals` | 待审批队列 | 合同名 / 高风险条款数 / 等待时长 / 进入审批；>2 小时标黄，30 秒轮询 |
| `#/approvals/:id` | 审批详情 | 375px 优先，逐条 通过/驳回/改措辞；审批人 = 当前登录账号 |

（原 Stage 12 是 `#admin` / `#eval` 两屏，靠 `watch + hashchange` 手切；带 `:id` 的审批页进来之后
手切撑不住前进/后退与可分享链接，改用 `createWebHashHistory`。选 hash 而不是 history 是因为
独立 HTML 入口下 history 要给每条子路由配 nginx 回退，hash 模式服务端永远只看到 `/admin`。）

两处拆分各有理由：

- **工作台 vs 详情**：工作台能发起运行（写），详情只读。把「分享一条结果链接」和「发起一次新运行」放同一页面会误操作。
- **用户端 vs 管理端**：运维与评测暴露的是内部拓扑（谁探谁、依赖通不通）与成本结构，不是给签约方看的。
  拆成两个 HTML 入口后，用户端首包里不再有任何指向 `/admin`、`/eval` 的引用（阶段 6 实测 `grep` 0 命中）。
  **准确表述是「应用代码按入口切分」**：`index.html` 引 `main-*.js`、`admin.html` 引 `admin-*.js`，
  但 Element Plus / Vue / vue-router / Pinia 合成一个共享 vendor chunk（约 1.0 MB），两个入口都引它——
  「两个包毫无共享」不成立。老链接 `/admin`、`/eval` 由 `main.ts` 在挂载前 `replace` 到新入口，
  且 `/admin` 只认精确匹配，`/administrator` 这类同前缀路径不再被误伤（阶段 6）。

**入口分开不是权限隔离，但后端这次真的上锁了。** 阶段 3 给 `/api/v1/admin/**` 加了
`SecurityFilterChain`（HTTP BASIC、ADMIN 角色、401/403 都是 JSON、无会话），绕开页面直接 `curl`
一样被拒；用户端接口仍是 `permitAll`（README 已知限制第 11 条）。**前端登录卡不是安全边界**。

### 1.2 新增/改动文件

```
web/index.html                    用户端入口
web/admin.html                    管理端入口（独立产物）
web/src/admin/main.ts             管理端挂载（阶段 4 起挂 hash router；老 #eval 先迁成 #/eval 再挂载）
web/src/admin/router.ts           † 管理端 4 条 hash 路由 + afterEach 写 document.title
web/src/admin/legacy-hash.ts      † 老 hash 写法（#eval）→ 新写法（#/eval）的纯函数
web/src/admin/AdminApp.vue        管理端外壳：登录卡 / 深色侧栏 / router-view
web/src/router/index.ts           用户端 3 条路由 + 标题（管理端与审批路由已移出）
web/src/api/auth.ts               † 管理端凭证：内存 + sessionStorage，Basic 头编码
web/src/api/http.ts               统一 fetch（错误体解析、401 抛 AuthError、409 语义交给上层）
web/src/api/contracts.ts          上传/列表/详情/diff/原文地址
web/src/api/reviews.ts            创建/结果/分页 findings/审批/产物（REST 部分）
web/src/api/stream.ts             SSE 状态机：退避序列 + seq 去重 + ?since= 断点（可单测）
web/src/api/eval.ts               评测汇总（仅管理端引用）
web/src/api/admin.ts              健康聚合 + 待审批队列（仅管理端引用）
web/src/types/api.ts              REST 契约镜像（snake_case，可选字段标 ?）
web/src/types/events.ts           事件契约镜像 + 类型守卫（isRunCompleted 等）
web/src/stores/run.ts             一次运行的实时状态（跨页面存活）
web/src/utils/wait.ts             † 等待时长格式化 / >2h 判定（队列与详情页共用）
web/src/components/AppShell.vue   用户端外壳（导航只剩「合同」；移动端底部栏仅在能返回时出现）
web/src/components/DocViewer.vue  PDF iframe / DOCX docx-preview + 证据定位
web/src/components/FindingsPanel.vue  风险筛选、引用展开、定位
web/src/components/RunPanel.vue   状态/节点进度/摘要/事件流（无审批入口）
web/src/components/EventFeed.vue  事件流（贴底自动跟随）
web/src/components/UploadCard.vue 上传（新合同 / 新版本共用）
web/src/views/*.vue               七个页面（用户端 3：列表/工作台/结果；管理端 4：健康/评测/队列/审批）
web/tests/*.test.ts               4 个文件 31 条纯逻辑单测（Node 内置 runner）
```

删除：`web/src/components/ReviewConsole.vue`（Stage 11 的单页控制台，被工作台取代；
其中的条款变更列表抽成 `ClauseDiffList` 继续复用，能力无损失）。
删除（阶段 4/5）：`web/src/stores/session.ts`——审批人改取登录账号，不再让人手填署名。
标记 † 的文件是阶段 3–7 新增，原 Stage 12 不存在。

### 1.3 新增依赖（已按「版本表变更规则」登记）

| 依赖 | 版本 | 为什么必须新增 |
| --- | --- | --- |
| `vue-router` | 4.6.4 | 4 条路由 + 可分享的 `/reviews/:id` 链接需要真实 URL；管理端只有两屏，故**没有**给它挂 router |
| `docx-preview` | 0.4.1 | 浏览器不能原生渲染 DOCX；PDF 走 `<iframe>`，因此**没有**引 pdfjs |

## 2. 关键设计说明（面试可讲的三点）

### 2.1 SSE 自己重连 + seq 去重

浏览器 `EventSource` 会自动重连，但它只在**同一个实例**上带 `Last-Event-ID`；
本前端为了自己控制退避，断线后是手动 `new EventSource`，新实例不继承这个头。因此：

- 客户端维护 `lastSeq`，重连时把它放进 `?since=`，让 Java 只补缺失那一段
  （服务端把 `since` 与 `Last-Event-ID` 折成同一个 `afterSeq`，不维护两套「看到哪了」）；
- `seq <= lastSeq` 仍然丢弃 —— 重放边界上多发的一条不该让节点计数翻倍；
- 断线不交给 EventSource：下游熔断时 `/stream` 返回 503，浏览器默认静默重试，
  界面上看不出任何异常。这里按 1s→30s 指数退避，并把 `连接中/重连中(第 N 次)/已结束`
  透出到运行面板；
- **退避只在收到第一条有效事件后清零**：HTTP 200 只说明连上了，不代表收到东西。
  服务端「先 200 再断」时 `onopen` 每次都成功，在 `onopen` 里清零会让退避退化成恒定 1s。

**为什么终态要主动关闭**：服务端在 `run.completed` 后会 `complete()`，不主动关会立刻
触发一次重连，白跑一遍回放。

### 2.2 审批页可以「冷启动」

审批请求体的 `contract_version` / `content_hash` 是乐观锁，只能来自中断时的快照。
这些字段不在 REST 结果里，但 `interrupt` 事件本身带着它们，而 `/stream` 支持从 seq 0
回放——所以审批页**不需要先打开工作台**：直接打开链接（手机扫码/IM 转发）就能重建待办。
实测见 3.3：Chrome 冷启动直开 `/reviews/:id/approve` 拿到完整卡片。

这比「新增一个 pending 接口」省了一次契约变更，也避免了两份快照口径。

### 2.3 一条条款可能被多个专家同时报

演示合同第五条同时命中 `payment_risk`（商务）与 `breach_risk`（法务），
`interrupt.payload.items` 因此出现**两条相同 `clause_id`**。审批的主键是条款而不是风险点
（`ApproveRequest.clause_id`），所以前端按 `clause_id` 归并成一张卡片、展示「2 个风险点」，
提交时一条条款只发一次决定。不归并会出现「同一卡片渲染两次、草稿却只有一份」的错位。

## 3. 验收（真实输出）

### 3.1 门禁

```
$ cd python-agent && pytest -q
293 passed in 64.77s

$ ruff check app tests
All checks passed!

$ mypy app
Success: no issues found in 54 source files

$ cd java-service && mvn test        # JAVA_HOME=JDK 21
[INFO] Tests run: 63, Failures: 0, Errors: 0, Skipped: 0      # 阶段 7 时为 85
[INFO] BUILD SUCCESS

$ cd web && npm run build            # 含 vue-tsc 类型检查
✓ built in 10.55s

$ cd web && npm run test:unit        # 提交前自查补入（Node 内置 runner，零新增依赖）
# tests 7   # pass 7   # fail 0                                # 阶段 7 时为 31
```

> 上面是 Stage 12 当次的原始输出；当前门禁数字（Java 85 / web 31）与 CI 配置见 3.5。

Java 从 61 增至 63：新增 `approveDecodesPercentEncodedOperator` 与
`decodeOperatorKeepsAsciiAndMalformedInput`（见 4.1）。

前端单测覆盖的是重连退避与 seq 去重两处纯逻辑（`web/src/api/stream.ts`）：退避写错会在熔断期间
持续打满网关且界面看不出来，seq 比较方向写错会让重连后计数翻倍或把新事件全丢掉——这两种故障
在手工验收里都只表现为「数字不对」，很难复现。用 Node 内置 test runner 而非引入测试框架，
是为了不新增版本表之外的依赖（铁律 5）。

### 3.2 两个入口可达（真实 Chrome 渲染，不是只看 200）

用户端（阶段 6 实测，3 条路由）：

```
/                                            渲染出 "ClauseGuard / 合同风险审查 / 上传合同"
/contracts/{id}                              三栏渲染，DOCX 原文可见（正文含「技术开发合作协议」）
/reviews/{id}                                结果页可渲染；awaiting_approval 时显示「等待法务审批中」
/reviews/{id}/approve                        已删除 → 404 页（用户端不再有审批路由；阶段 9 起不再静默回 /）
```

管理端（`/admin`、`/admin/`、`/admin.html` 三种写法都落到同一入口，且**先过登录卡**）：

```
/admin                                       登录卡（.gate-card），无侧栏；<title> 已是「评测看板 · ClauseGuard 管理端」
admin + 正确口令                              → 保留 hash，进 #/approvals，标题「待审批 · ClauseGuard 管理端」
#/health / #/eval / #/approvals / #/approvals/:id   4 条 hash 路由都渲染，标题随路由变
拼错 hash（#/nope）                          收敛到 #/health，不留白屏
/eval（老链接）、/admin#eval                  迁到 /admin.html#/eval
```

分离度实测（阶段 8 复核）：

```
用户端首包 main-*.js 中 grep "api/v1/admin|api/v1/eval|运维健康|评测看板|待审批"  -> 0 命中
用户端侧栏 .nav-item                                                        -> ["合同"]
管理端侧栏 .side-item                                                       -> ["运维健康 双栈探活", "评测看板 用量与质量", "待审批 审核者待办"]
管理端侧栏「当前账号/退出」                                                  -> "当前账号：admin"
375px 用户端首页 .tabbar                                                    -> 不存在，padding-bottom=0px
375px 工作台底栏                                                            -> ["‹ 返回列表"]，scrollW=375/375
两端控制台 / pageerror                                                      -> 0
```

工作台实测（1440×900，真实布局数值）：

```
grid-template-columns: 419.6px 384.7px 314.7px
docx 渲染页数: 1   原文首句: "技术开发合作协议 甲方：云栖科技有限公司 乙方：蓝湾数据服务有限公司"
风险发现 23 条     条款变更 0 条（本轮 full，无 diff 可比）
DOCX 横向溢出: 0px（覆盖了 docx-preview 默认的 A4 固定宽度，见 4.3）
```

### 3.3 端到端（无头 Chrome 真点击，375px）

一笔全新合同，从「冷启动打开审批页」到「审批后续跑完成」：

```
[step] open approval page (cold start, 375px)
[step] cards=1  submit="提交（1/1）"  scroll=375/375     ← 无横向滚动
[step] confirm: 1 条高风险条款：通过 1 条、驳回 0 条。提交后立即续跑，不能撤回。
[step] -> POST /api/v1/reviews/2e84a889.../approve
[step] POST /approve -> 200 {"status":"accepted","decided_at":"2026-10-27T02:56:40.02Z"}
[step] toast=["审批已记录，正在续跑"]
[step] url=/reviews/2e84a889-ff87-4369-835d-203dcd7d1ec8
[step] detail: {"status":"已完成","findingsLine":"共 23 条，当前显示 23 条"}
[step] workbench: {"docxSections":1,"findingsTab":"23","columns":"419.6px 384.7px 314.7px"}
[step] list 1440: 7 行
```

审批卡片内容（证明归并与快照都正确）：

```json
{
  "clauseId": "tmpjz1kzdx8#0005",
  "clause": "第五条　违约责任 任何一方违约的，应向对方支付合同总价百分之二十的违约金；违约金总额不设上限，已支付款项不予退还。",
  "risks": ["payment_risk/high", "breach_risk/high"]
}
```

### 3.4 审批结果落库可查

```
$ curl -s .../api/v1/reviews/{id} | jq '.status'
"completed"
```

`X-Operator` 用中文名「验收员-e2e」提交，落库后为原样中文（见 4.1 的编码约定）。

### 3.5 阶段 3–8 补充验收：401/403 · SSE 时序 · 375px · 队列 · CI

上面 3.1–3.4 是 Stage 12 当次的记录。以下四项是阶段 3–8 新增能力各自的验收，
全部在**真实 Chrome + 真实 8080 鉴权 + 真实 MySQL 数据**上跑（阶段 8 复核时重测）。

**① 401 / 403（真鉴权，不是前端藏起来）**

```
$ curl -i http://127.0.0.1:8080/api/v1/admin/health            # 不带凭证
HTTP/1.1 401
WWW-Authenticate: Basic realm="clauseguard"
{"code":"UNAUTHORIZED","message":"需要管理员凭证"}

$ curl -i -u admin:<正确口令> .../api/v1/admin/health           # 200
$ curl -i -u user:<正确口令>  .../api/v1/admin/health           # 403 {"code":"FORBIDDEN","message":"当前账号没有管理员权限"}

$ curl -s .../api/v1/contracts                                  # 用户端接口：200，不需要凭证（已知限制 11）
```

浏览器侧（`web/src/api/http.ts` 把 401 抛成 `AuthError`，登录卡只显示一行原因）：

```
PASS 401 提示账号或密码错误 — {"alert":"账号或密码错误","stillGate":true}
PASS 403 提示需要 ADMIN 角色（不得当成密码错） — {"alert":"该账号没有管理员权限（需要 ADMIN 角色）","shell":false}
PASS 未登录只渲染登录卡（无侧栏） — {"hasGate":true,"hasShell":false,"sideVisible":false}
```

单测层同样钉住这两条（`AdminControllerTest`）：`healthRejectsAnonymousWith401Json`、
`wrongPasswordReturnsTheSame401JsonAsAnonymous`、`healthRejectsNonAdminWith403Json`。
「密码错」和「没带凭证」必须返回**同一个** JSON 体——`BasicAuthenticationFilter` 自带 entry point，
不把它一起替换掉，同一个接口就会有两种 401 响应体。

> 口径说明：第一段（无凭证 401）是阶段 8 重测的原样输出；后两行的口令打码，403 的响应体取自
> `SecurityConfig` 的 `accessDeniedHandler`（`{"code":"FORBIDDEN","message":"当前账号没有管理员权限"}`），
> 行为由 `healthRejectsNonAdminWith403Json` 与浏览器用例共同保证（登录卡显示那句中文）。

**② SSE 重连时序（拦截 `/stream` 返回 200 后立刻 EOF，复现「先 200 再断」）**

```
PASS 无事件时连了 4 次 — requests=4
PASS 间隔 1s → 2s → 4s（不是恒定 1s） — gaps=1010,2006,4005ms
PASS 没收到事件时重连不带 since — /api/v1/reviews/{id}/stream
PASS 收到事件后重新从 1s 起：1s → 2s → 4s — gaps=1016,2006,4006ms
PASS 重连 URL 带 ?since=2 — /api/v1/reviews/{id}/stream?since=2
PASS 后续重连仍带同一个断点
```

后端侧（真实数据，210+ 事件）：`?since=5` 与 `Last-Event-ID: 5` 回放结果一致（从 `id:6` 起），
两者同时给时 `since` 优先。退避清零的时机是「收到第一条通过去重的事件」，不是 `onopen`——
`onopen` 只证明 HTTP 200，不证明收到了东西。

**③ 375px（真实设备宽度，375×780）**

```
PASS 375 首页无底栏、无留白 — {"tabbar":false,"pad":"0px","scrollW":375}
PASS 375 工作台底栏只有「‹ 返回列表」 — {"tabs":["‹ 返回列表"],"scrollW":375}
PASS 375 审批详情可用 — 1 条高风险条款 / 2 个风险点 / 「提交（1/1）」/ scrollW=375
```

首页不渲染 `.tabbar` 是整条 `v-if`（不是 `visibility:hidden`），所以底部不留白条。

**④ 待审批队列（`#/approvals`，数据来自真实 review 表）**

```
PASS 待审批队列有真实数据行 — {"count":8,"overdue":6,
     "first":"demo-contract.docx 5e586f34 1 条 5 小时 26 分 超时 进入审批"}
PASS 375 队列可用且表头收起 — {"rows":8,"scrollW":375,"headRowVisible":false}
PASS 管理端 #/approvals/:id 渲染（props 传 id）
PASS 后退回到上一屏且不白屏 — {"hash":"#/health","title":"运维健康 · ClauseGuard 管理端"}
```

`waiting_since` 取 `interrupt` 事件时间戳（不是 `created_at`），所以「5 小时 26 分」是审核者的
等待时间；超过 2 小时整行标黄（8 行里 6 行超时，与数据一致）。

**⑤ CI（阶段 8）**

`.github/workflows/web.yml`：`paths: web/**` 触发的独立 job，`setup-node@v4`（Node 22）→
`npm ci` → `npm run test:unit` → `npm run build`。**不跑需要真实模型/真实服务的用例**——
那些留给手工验收与后续 `web/e2e`。

本地等价执行（复制一份**不含 `node_modules`/`dist`** 的工程目录，模拟新机器从零装依赖）：

```
$ node -v            v22.22.2
$ npm ci             added 89 packages in 46s
$ npm run test:unit  # tests 31   # pass 31   # fail 0
$ npm run build      ✓ built in 8.00s
```

（本机直接 `rm -rf node_modules` 会被执行环境的批量删除保护拦下（>50 个文件需确认），
所以用「复制到干净目录再 `npm ci`」达成同一目的——它比原地删更接近 CI 的实际起点：
CI 的 `actions/checkout` 本来就只给源码。）

`web/package.json` 加了 `engines.node >= 22.18`，写进 `package-lock.json` 的根节点；
低于这个版本 `node --test` 不再默认剥离 `.ts` 类型，会在第一行报语法错——与其让人猜，不如让
`npm install` 直接给出警告。

### 3.6 阶段 9（16 项逐项验收）与阶段 10（10 项回归自查）

上面 ①–⑤ 是阶段 3–8 各自的验收。阶段 9 把它汇总成 16 项逐条判 PASS/FAIL/BLOCKED，
阶段 10 再按「有没有破坏原有能力」回归一遍。结论：**阶段 9 15 PASS / 1 FAIL，
阶段 10 8 PASS / 2 FAIL，无 BLOCKED**。两项 FAIL 都不是阶段 3–8 引入的回归，已登记进
README 已知限制。

**阶段 9 · 端到端（11/11，真实 Chrome + 真实 8080 鉴权）**

```
上传 → 工作台 → awaiting_approval → 管理端队列可见 → 审批详情 3 个选项
→ 提交受理 → completed → 发现 23 条（高危 2）→ 下载 annotated.docx 39855B
python-docx 重开：OPEN OK（30 段，word/comments.xml 含 23 组 commentRangeStart/Reference）
全程 console error 0、pageerror 0
```

SSE 首屏时序（新建一笔审查，取前 5 条事件）：相对毫秒 `[0.02, 0.08, 0.10, 0.12, 0.13]`，
相邻间隔 `[0.06, 0.02, 0.02, 0.01, 0.01]`，最大 0.06ms —— 远低于 200ms 的门槛，
证明事件是逐条到达而不是攒批下发。

产物隔离（按 `index.html` 可达 chunk 集合，不是只 grep 入口文件）：
用户端可达 11 个 chunk，关键字 `api/v1/admin|api/v1/eval|运维健康|评测看板|待审批` **0 命中**；
同一脚本跑 `admin.html` 可达集合命中 17 次（对照成立，说明 grep 有效）。

**阶段 9 · 唯一 FAIL**：`git diff --stat 011579f -- python-agent/ contracts/` 非空——
`python-agent/` 是空的（铁律守住），非空的是 `contracts/openapi.yaml +95`：阶段 5 的
`/api/v1/admin/approvals` 与阶段 7 的 `since`/`Last-Event-ID`。这两处是**新接口与新参数的
契约**，删掉等于让契约与实现不一致，因此判定为「不可回退的增量」而不是去改代码凑绿。

**阶段 10 · 回归 8 项 PASS 的关键数字**

```
主链路      6 个 review.* 节点各 1 次 node.started/finished，run.completed findings=23
断线续传    审查中途 reload → 继续收到终态；结果页 23 == API 23（没翻倍）；
            ?since=3 重连首条 seq=4，dup=[] holes=[]
断点续跑    53 条事件按 node 统计 started 次数全为 1；python 日志 resume 后无第二次 ingest/dispatch
增量重审    v2（改 1 条 + 新增 1 条）：changed=3 / unchanged=13 / reused=20 / cache_hit_rate=1.00；
            token 11960 → 2600（省 9360）；dispatch 分片 6 → 4
375px       列表 18 行可点、工作台三栏可渲染、结果页 23 张发现卡片 == API 23，scrollWidth 均 375
```

**阶段 10 · 两项 FAIL（都不是本次引入）**

- **后端挂掉时前端不承认**：真 kill 掉 java 后，列表页有 el-alert 但文案是裸 `HTTP 500`
  （vite 代理给的），并落到「还没有合同」空态；结果页停在「排队中 / 重连中（第 3 次）」，
  没有失败说明。两页都不是白屏，但会误导。
  `ContractListView.vue` 与 `stores/run.ts` 相对基线 **0 改动** → 既有行为。
- **console 有一条既有 404 噪音**：工作台加载时请求 `/api/v1/contracts/{id}/diff?target=<当前版本>`，
  新版本尚未审查时必然 404；前端 `loadDiff()` 已 try/catch（注释里写明 404 是正常情况），
  浏览器仍记一条资源级 error。warning 全程 0 条。

（这两项当时登记为「既有行为」，阶段 11 之后已修掉并复核，证据见 3.7。）

**跑这两轮时的环境说明（不是代码问题）**：本机没有 Docker daemon 与 MinIO，
对象存储用一个只实现 PUT/GET 的 S3 替身（127.0.0.1:9000）顶替；批注稿链路还撞到既有缺口——
Java 上传落 `uploads/{id}-v{n}.docx`，而 Python render 只认 `contracts/{id}/v{n}/source.docx`，
键约定没接通，默认配置下走 `PLACEHOLDER_RENDER: reason=source-unavailable`。
本机是把源 docx 按约定键摆进 `local_dir` 才跑出批注稿的（详见 README 已知限制 11）。

### 3.7 阶段 11 之后：把阶段 10 的两项 FAIL 修掉（含复核证据）

阶段 10 的两项 FAIL 都是既有行为，但都落在「用户能不能看懂出了什么事」上，值得修。
修完重跑同一组断言，结论 **PASS**；阶段 9 的项 1（契约增量不可回退）保持 FAIL 不动。

**修一：失败提示说人话 + 连不上时要承认（`fix(web)`）**

- 新增 `web/src/utils/error.ts`：`describeApiError()` 把状态码翻译成人话。502/503/504 →
  「连不上后端服务，请确认 java-service 是否在 8080 运行」；5xx → 「后端出错了（HTTP 500）」；
  401/403/404 各自说清是谁的问题；**后端给了结构化 `{code,message}` 时原话优先**——
  我们编的文案永远不如后端那一句准（例：`CONTRACT_CHANGED` 会说清是哪个版本变了）。
  按形状（有没有 `status`/`code` 字段）判断而不是 `instanceof ApiError`，因为
  `api/http.ts` 的 import 链在 `node --test` 的原生 ESM 下解析不了，测试一 import 就崩。
- 列表页：失败时不再落到「还没有合同」空态（那句话会让人以为数据丢了、再去传一遍），
  空态文案改成「列表没能加载出来，按上面的提示处理后点『重试』」，并给了重试按钮。
- 结果页/工作台：`stores/run.ts` 新增 `offline`（重连 ≥3 次且一条事件都没收到 →
  `true`），`RunPanel` 据此显示「连不上服务…恢复后本页会自动接上，进度不会丢」。
  阈值取 3 是算过的：退避 1s→2s→4s，第 3 次失败时约 7 秒；取 1 会把「请求撞上重启」
  误报成故障，取 5 要等 31 秒，而用户通常在第 10 秒就自己刷新了。
- 覆盖到的调用点：合同列表、工作台（加载合同 + 发起审查）、原文渲染、管理端四屏
  （登录卡/概览/队列/评测）。

**修二：未审查版本的 diff 回空账本而不是 404（`fix(api)`）**

`GET /api/v1/contracts/{id}/diff?target=N` 在该版本还没跑过审查时抛 404。但「还没审过」
是**没有对比数据**，不是**资源不存在**：前端每次切到刚上传的新版本都会收获一条 404，
报表的人第一眼看到的是「接口坏了」。改法：`ReviewService.contractDiff` 在找不到审查时
返回空账本（`baseline_version=0`、三个列表为空）；`ContractController` 先调一次
`contractService.current(id)`，**404 只留给「合同 id 不存在」**。契约不用改——
`openapi.yaml` 里声明的 404 仍然可达。

**复核证据（真实命令 / 真实 Chrome）**

```
curl -i .../contracts/{id}/diff?target=4      → 200 {"baseline_version":0,"target_version":4,"changed":[],...}
curl -i .../contracts/00000000-...-ff/diff?target=2 → 404（合同不存在，404 仍然可达）
工作台（v4 未审查）  diff 请求确实发出 + 200；console error 0、warning 0、4xx/5xx 0

后端 kill 掉之后：
列表页   alert「后端出错了（HTTP 500），请稍后重试」+「重试」按钮；
         空态「列表没能加载出来…」不再冒充「还没有合同」；非白屏
结果页   11s 后文本含「连不上服务」，重连计数 4；非白屏；0 pageerror

管理端回归（两端都改了错误文案）   未登录只显示登录卡；错口令 401 显示「账号或密码错误」
                                   且仍停在登录卡；正确口令进管理端（侧栏 3 项），
                                   概览/评测/待审批三条路由均渲染；除故意触发的那条 401
                                   资源日志外 0 error

测试   mvn test 87（原 85，+2：空账本与 404 语义）
       node --test 36（原 31，+5：错误翻译五条断言）
       npm run build（vue-tsc + vite build）通过
```

## 4. 本阶段修掉的三个真实缺陷

### 4.1 中文审批人导致审批请求发不出去（前端 + Java，已在契约里补一句说明）

浏览器实测报错：

```
Failed to execute 'fetch' on 'Window': Failed to read the 'headers' property from
'RequestInit': String contains non ISO-8859-1 code point.
```

HTTP 头按 RFC 7230 只能承载 ISO-8859-1，**中文姓名会在 fetch 之前就被浏览器拒绝**，
请求根本没出网卡。任何用中文名审批的人都会卡在「点了提交没反应」。

修法：前端 `encodeURIComponent`，Java `ReviewController.decodeOperator` 解码后入库
（不含 `%` 的值原样返回，脚本与纯 ASCII 调用方不受影响）。`contracts/openapi.yaml`
的 `X-Operator` 描述补上了这条编码约定——这是传输层细节，字段形状未变。
审计记录里存的是「张三」而不是 `%E5%BC%A0%E4%B8%89`。

### 4.2 同一条款多专家重复上报 → 审批卡片 key 冲突

见 2.3。按 `clause_id` 归并，一张卡展示多个风险点。

### 4.3 DOCX 左栏被裁掉左半页

docx-preview 默认按 A4（约 794px）排版，而左栏只有约 400px，页面向左溢出、
用户看到的是被切掉的半页。覆盖为跟随容器宽度（`width:100%` + 去纸张阴影）后
实测横向溢出 0px，首句完整可见。

另外顺手清掉两类噪音：`el-text type="placeholder"` 是非法枚举值（Element Plus 只在
console 告警、样式不生效），改用 `type="info"` + `.muted` 类；补了内联 SVG favicon，
消掉 `/favicon.ico` 的 404。

## 5. 遗留风险

1. **同一合同同版本的第二笔审查会串状态（后端，非本阶段引入）**：`thread_id` 取
   `contract_id:version`，因此第二笔审查与第一笔共享同一个 LangGraph checkpoint。实测
   连续两笔 full 审查后，第二笔的 `run.completed.findings` 变成 **46 条**（23 条旧前缀
   + 23 条新前缀），且旧前缀的 23 条因条款已不存在被降级为 `unverified`；单笔隔离跑
   则是干净的 23 条 / 单一前缀 / 无 unverified。界面如实展示了这个结果，没有隐藏。
   建议修法二选一：`thread_id` 带上 `run_id`（`run_registry` 已按 run 存 thread_id，
   resume 不受影响），或在开跑前清掉该 thread 的 checkpoint。**属 Stage 9 设计决策，
   本阶段未擅自变更。**
2. **管理端有鉴权，用户端没有**：阶段 3 给 `/api/v1/admin/**` 上了 `SecurityFilterChain`
   （HTTP BASIC / ADMIN 角色 / 401·403 都是 JSON / STATELESS），**前端登录卡只是体验，真正的门在后端**，
   `curl` 不带凭证会被 401 挡住。但 `/api/v1/**` 其余接口（上传、发起审查、读结果、审批写入、
   `/api/v1/eval/summary`）仍是 `permitAll`，不加凭证直接 200。`X-Operator` 已改为取登录账号，
   比手填强，但**审批写入端点 `POST /api/v1/reviews/{id}/approve` 没锁**——知道 URL 就能绕过管理端
   直接提交。要真锁，得把它挪到 `/api/v1/admin/**` 下或单独加规则。见 README 已知限制第 5、11 条。
3. **契约里可选字段仍为空**：`ContractSummary.last_review` 的 `mode` / `findings_count`
   在 Java 侧未填充（`Review` 实体没有 `mode` 列），列表页按 `—` 展示。补它需要在列表
   里逐行取事件（N+1），本阶段没做。
4. **批量审批没做**：目前逐条决定；`payload.items` 多时，缺一个「全部通过」快捷操作
   （故意的：高风险条款逐条确认才是设计意图，但演示时希望更快可以加）。
5. **批注稿下载依赖 MinIO**：本机未起 MinIO，`run.completed.artifact` 为空，结果页
   「下载批注稿」按钮不显示（这是正确行为，不是 bug）。
6. **PDF 无法文本定位**：PDF 用原生 iframe，没有文本层，`locate()` 返回 false 后提示
   用户用浏览器内查找。DOCX 可精确定位并高亮（实测首句命中）。

## 6. 下一阶段建议

1. **修 5.1 的 thread 复用问题**：这是目前唯一会让「结果看起来错了」的缺陷，
   且修法明确（`thread_id` 带 run_id）。
2. **用户端也要有身份**：管理端已上 BASIC（阶段 3），`X-Operator` 也已改成取登录账号（阶段 5），
   剩下的是给用户端接认证，并把审批写入端点挪进受保护的路径前缀。
3. **批量审批 + 审批历史**：`Approval` 表已落 operator/前后措辞，缺一个回看页面。
4. **E2E 纳入 CI**：阶段 8 已把 web 的单测与构建接进 `.github/workflows/web.yml`（纯函数层，
   不需要真实服务）；浏览器级脚本仍是临时的（`/tmp` 下、不入库）。把用户端 3 条 + 管理端 4 条路由、
   登录闸门、审批链路固化成仓库内 `web/e2e/` 并单独开一个 job 才可持续。
