# Stage 12 验收报告｜前端工作台

> 目标：把已有的后端能力变成**能点、能演示**的界面——6 条路由、三栏工作台、
> SSE 断线重连、375px 移动端审批页。后端接口在此之前已就绪并有测试，
> 本阶段不动契约、不动状态字段，只补前端与两处被前端暴露出来的缺陷。

- 代码版本：Stage 12 提交（`git log` 中「Stage 12 前端工作台」）
- 环境：Windows 11 / Node 20 / Chrome（headless）/ JDK 21 / Python 3.13
- 运行形态：web 5173（vite dev）· java 8080（`--clauseguard.event-store=in-memory`）·
  python 8000（`CLAUSEGUARD_AGENT_MODEL_BACKEND=mock`）· MySQL 8（本机 3306）

## 1. 交付内容

### 1.1 两个入口：用户端 4 条路由 + 管理端 2 屏

用户端 `index.html`（`/`）：

| 路由 | 页面 | 职责 |
| --- | --- | --- |
| `/` | 合同列表 | 上传、搜索、看最近审查状态、进入工作台/查看结果 |
| `/contracts/:id` | 审查工作台 | 三栏：原文 / 风险发现·条款变更 / 运行态；发起全量或增量审查 |
| `/reviews/:id` | 审查结果 | 只读落地页，可分享给同事；批注稿下载 |
| `/reviews/:id/approve` | 人工审批 | 375px 优先，逐条 通过/驳回/改措辞 |

管理端 `admin.html`（`/admin`，视图用 hash 切换，不挂 router）：

| 屏 | 职责 |
| --- | --- |
| `#admin` 运维健康 | Java + Python 双栈状态，10 秒轮询 |
| `#eval` 评测看板 | token / 成本 / 质量 / 增量收益 |

两处拆分各有理由：

- **工作台 vs 详情**：工作台能发起运行（写），详情只读。把「分享一条结果链接」和「发起一次新运行」放同一页面会误操作。
- **用户端 vs 管理端**：运维与评测暴露的是内部拓扑（谁探谁、依赖通不通）与成本结构，不是给签约方看的。
  拆成两个 HTML 入口后，用户端产物里不再有任何指向 `/admin`、`/eval` 的引用（实测 `grep` 0 命中），
  管理端代码也不进用户首包。老链接 `/admin`、`/eval` 由 `main.ts` 在挂载前 `replace` 到新入口。

**但要说清楚：这只分开入口，不构成权限隔离。** `/api/v1/admin/**` 仍未鉴权（README 已知限制第 5 条），
绕开页面直接调接口一样能拿到数据；真正的门是后端的 `SecurityFilterChain`，属下一阶段。

### 1.2 新增/改动文件

```
web/index.html                    用户端入口
web/admin.html                    管理端入口（独立产物）
web/src/admin/main.ts             管理端挂载（无 router，省掉回退配置）
web/src/admin/AdminApp.vue        管理端外壳：深色侧栏 + 两屏切换 + 返回用户端
web/src/router/index.ts           用户端 4 条路由 + 标题（管理端两条已移出）
web/src/api/http.ts               统一 fetch（错误体解析、409 语义交给上层）
web/src/api/contracts.ts          上传/列表/详情/diff/原文地址
web/src/api/reviews.ts            创建/结果/分页 findings/审批/产物 + SSE 订阅器
web/src/api/stream.ts             SSE 纯逻辑：退避序列 + seq 去重（可单测）
web/src/api/eval.ts               评测汇总（仅管理端引用）
web/src/api/admin.ts              健康聚合（仅管理端引用）
web/src/types/api.ts              REST 契约镜像（snake_case，可选字段标 ?）
web/src/types/events.ts           事件契约镜像 + 类型守卫（isRunCompleted 等）
web/src/stores/run.ts             一次运行的实时状态（跨页面存活）
web/src/stores/session.ts         审批人署名（localStorage）
web/src/components/AppShell.vue   用户端外壳（导航只剩「合同」；移动端底部栏给「返回列表」）
web/src/components/DocViewer.vue  PDF iframe / DOCX docx-preview + 证据定位
web/src/components/FindingsPanel.vue  风险筛选、引用展开、定位
web/src/components/RunPanel.vue   状态/节点进度/摘要/事件流/审批入口
web/src/components/EventFeed.vue  事件流（贴底自动跟随）
web/src/components/UploadCard.vue 上传（新合同 / 新版本共用）
web/src/views/*.vue               六个页面（4 个属用户端，2 个归管理端复用）
web/tests/stream.test.ts          SSE 纯逻辑单测（Node 内置 runner）
```

删除：`web/src/components/ReviewConsole.vue`（Stage 11 的单页控制台，被工作台取代；
其中的条款变更列表抽成 `ClauseDiffList` 继续复用，能力无损失）。

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
[INFO] Tests run: 63, Failures: 0, Errors: 0, Skipped: 0
[INFO] BUILD SUCCESS

$ cd web && npm run build            # 含 vue-tsc 类型检查
✓ built in 10.55s

$ cd web && npm run test:unit        # 提交前自查补入（Node 内置 runner，零新增依赖）
# tests 7   # pass 7   # fail 0
```

Java 从 61 增至 63：新增 `approveDecodesPercentEncodedOperator` 与
`decodeOperatorKeepsAsciiAndMalformedInput`（见 4.1）。

前端单测覆盖的是重连退避与 seq 去重两处纯逻辑（`web/src/api/stream.ts`）：退避写错会在熔断期间
持续打满网关且界面看不出来，seq 比较方向写错会让重连后计数翻倍或把新事件全丢掉——这两种故障
在手工验收里都只表现为「数字不对」，很难复现。用 Node 内置 test runner 而非引入测试框架，
是为了不新增版本表之外的依赖（铁律 5）。

### 3.2 两个入口可达（真实 Chrome 渲染，不是只看 200）

用户端：

```
/                                            200  渲染出 "ClauseGuard / 合同风险审查 / 上传合同 / 进入工作台"
/contracts/{id}                              200  三栏渲染，DOCX 原文可见
/reviews/{id}                                200  结果页可渲染
/reviews/{id}/approve                        200  审批页可渲染
```

管理端（`/admin`、`/admin/`、`/admin.html` 三种写法都落到同一入口）：

```
/admin                                       200  <title>ClauseGuard 管理端</title>，侧栏 "运维健康 / 评测看板"，h1=运维健康
点第二项                                      切到 #eval，h1=评测看板
/eval（老链接）                               302 到 /admin.html#eval，h1=评测看板
```

分离度实测：

```
用户端入口包 main-*.js 中 grep "api/v1/admin|api/v1/eval|运维健康|评测看板"  -> 0 命中
用户端侧栏 .nav-item                                                       -> ["合同"]
管理端侧栏 .side-item                                                      -> ["运维健康 双栈探活", "评测看板 用量与质量"]
管理端底部                                                                 -> "← 返回用户端"
375px 审批页底部栏                                                          -> ["‹ 返回列表", "合同"]，scrollW=375/375
两端控制台错误                                                              -> 0
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
2. **没有登录与权限**：`X-Operator` 是「署名」不是「认证」，任何人填名字都能审批。
   演示与内网试点可用，对外必须先接认证（否则审计留痕形同虚设）。
   **管理端虽已拆成独立入口，但那只是入口隔离**：`/api/v1/admin/**` 未鉴权，知道接口就能绕过页面拿数据；
   真正的门在后端 `SecurityFilterChain`，属下一阶段。
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
2. **接认证**：至少把 `X-Operator` 换成登录态里的 principal，审批才有审计价值。
3. **批量审批 + 审批历史**：`Approval` 表已落 operator/前后措辞，缺一个回看页面。
4. **Playwright 纳入 CI**：本阶段的端到端脚本是临时的（`/tmp` 下、不入库）；
   把 6 条路由 + 审批链路固化成仓库内 `web/e2e/` 才可持续。
