# Stage 14 验收报告：可观测性与交付物

> 目标「能演示、能讲、别人能跑起来」。**验收结论：5 项全通过**，门禁全绿
> （pytest 293 / Java 61 / ruff / mypy 54 文件 / web build / trace-demo PASS / eval 复现一致）。
> 唯一一处需要新人注意的环境坑：`JAVA_HOME` 必须是 JDK 21（见 §6）。

- 日期：2026-10-26
- 代码版本：Stage 14 提交（`git log` 中「Stage 14 可观测性与交付物」；验收即在该版本上跑出）
- 关键产物：`docs/benchmark.md`（压测）、`README.md`（交付）、`docs/ADR-0002..0004`、
  `docs/demo-script.md`（90 秒分镜）、`docs/talk-outline.md`（3 分钟提纲）

## 1. 验收对照

| # | 任务书要求 | 证据 | 结论 |
| --- | --- | --- | --- |
| 1 | Java 生成 traceparent → HTTP 头 → Python 提取为父上下文 → 每节点建 span（含 agent 名、tokens）→ OTLP 上报 | §2 的真实 trace 树：1 个 trace_id、根 span 父 = Java span、6 个专家 span 各带 `gen_ai.agent.name` 与 tokens | ✅ |
| 2 | Locust 20 并发 5 分钟，输出 P50/P95/P99、错误率、平均成本 | §3：220 份 0 失败，主端点 P50 25s / P95 30s / P99 33s，单份 ¥0.013984；明细见 `docs/benchmark.md` | ✅ |
| 3 | README 补齐（架构图、3 条命令、目录、评测表、已知限制） | `README.md` 163 行，五节齐全 + Windows 无 make 的等价命令表 | ✅ |
| 4 | `docs/ADR` 0001–0004 | `docs/README.md` 索引 + 4 份 ADR | ✅ |
| 5 | 90 秒演示脚本 + 3 分钟技术讲解提纲 | `docs/demo-script.md`（逐秒分镜+旁白+4 类故障兜底）、`docs/talk-outline.md` | ✅ |

## 2. 一条 trace 串起 Java 与 6 个 agent（真实输出）

`make trace-demo`（`deploy/otel/verify_trace.py`，自带 OTLP 采集器，不依赖 Langfuse 账号）：

```
trace_id = 7a1f3c9d2b8e4f60a5c7d1e9b3f80246  (Java 生成的父上下文)
service  = clauseguard-python

└─ run.contract-review  [trace-demo:v1]  (首次运行)
   ├─ node.ingest
   ├─ node.diff
   ├─ node.dispatch
   ├─ node.review.commercial  tokens=1800/800
   ├─ node.review.subject  tokens=1800/800
   ├─ node.review.liability  tokens=720/320
   ├─ node.review.ip  tokens=1080/480
   ├─ node.review.data  tokens=1080/480
   ├─ node.review.compliance  tokens=1800/800
   ├─ node.critic
   ├─ node.cache_store
   └─ node.human_approval
└─ run.contract-review  [trace-demo:v1]  (审批后续跑)  tokens=8280/3680
   ├─ node.human_approval
   └─ node.render

根 span 2 段（首次运行 + 审批后续跑），专家 span 6 个：
['commercial_expert', 'compliance_expert', 'data_expert', 'ip_expert', 'liability_expert', 'subject_expert']
run.completed: findings=23 degraded=False usage={'input_tokens': 8280, 'output_tokens': 3680, 'cost_cny': 0.013984}
[trace] ✓ 一条 trace 串起 Java 父上下文与全部专家 span，OTLP 上报成功
```

脚本断言的正是「能被讲清楚」的三件事：**根 span 的 parent_span_id == Java 传下来的 span_id**、
**6 个专家 span 同 trace_id 且父亲是根 span**、**每个专家 span 带 tokens**。span 明细落在
`eval/out/trace-spans.json`。

三个实现要点（都踩过坑才这么写）：

1. **不伪造远端 span**：Python 用 `NonRecordingSpan(SpanContext(..., is_remote=True))` 引用 Java 的
   span 作父，而不是自己再建一个「假装是 Java」的 span；否则 Jaeger/Langfuse 里会出现两个同 id
   的「Java span」，一个永远没有结束时间。
2. **埋点集中在装配层**（`graph/build.py` 的 `_traced(name, fn, describe)` 包住 9 个节点）：
   节点函数本身不认识 OTel，测试里可以原样调用；专家名从 `review_task.expert` 取，与事件流同口径。
3. **`interrupt` 是控制流不是故障**：挂起时给 span 打 `Status(OK)` + `clauseguard.suspended=true`，
   否则链路图里每个审批断点都是红色错误，演示时先被问「你这怎么全是红的」。

## 3. 压测（20 并发 / 5 分钟）

| 端点 | 请求 | 失败 | P50 | P95 | P99 |
| --- | --- | --- | --- | --- | --- |
| `POST /v1/runs` | 220 | 0 | 25.0s | 30.0s | 33.0s |
| `POST /v1/runs/{id}/resume` | 220 | 0 | 0.27s | 4.3s | 8.3s |

- 旅程 220/240 完成、**错误率 0.00%**（差的 20 是到点关停瞬间在途，不是失败）
- 吞吐 **0.74 run/s ≈ 44 份/分钟**；单份成本 **¥0.013984**（46 次模型调用，8280 in / 3680 out）
- 单并发基线 P50 **3.49s** → 20 并发 25s：延迟线性涨、吞吐不涨，说明瓶颈是**单进程排队**而不是模型慢

完整解读（含 4/10/20 并发拐点、Little's law 复核、与 Stage 13 时延口径的区别）见
[benchmark.md](benchmark.md)。

> 补记一处方法论修正：第一版汇总用 Locust 的 Aggregated 行，P50 得出 16s——既不代表审查（25s）
> 也不代表恢复（0.27s）。报告改为**分端点统计**，并在 `test_stop` 里写清「混合 P 值是伪指标」。

## 4. 交付物清单

| 文件 | 行数 | 内容 |
| --- | --- | --- |
| `README.md` | 163 | 架构图 / 3 条命令快速开始 / 一键可验证三件事 / 目录 / 评测表 / 可观测性 / 6 条已知限制 |
| `docs/README.md` | 34 | ADR 索引 + 阶段验收索引 + 性能与演示材料索引 |
| `docs/ADR-0002-parallel-topology.md` | 50 | Send fan-out、纯函数 reducer、P95 3244→967ms（−70.2%）、三项代价 |
| `docs/ADR-0003-citation-whitelist.md` | 50 | 三道闸、丢空降级 `unverified`、能力边界（不保证语义贴切） |
| `docs/ADR-0004-interrupt-resume.md` | 58 | 图内 interrupt + PostgresSaver、`aupdate_state` 会清 pending interrupt 的坑 |
| `docs/benchmark.md` | 115 | 本阶段压测报告（口径/环境/分端点/拐点/局限） |
| `docs/demo-script.md` | 47 | 演前 3 分钟检查、0–90s 逐秒分镜与旁白、4 类现场故障兜底 |
| `docs/talk-outline.md` | 66 | 0:00–3:00 提纲（架构/三个真难点含「最初怎么想错」/数据口径）+ 5 条追问 |

## 5. 门禁（真实输出）

```
$ cd python-agent && pytest -q
293 passed in 66.21s

$ ruff check app tests
All checks passed!

$ mypy app
Success: no issues found in 54 source files

$ cd java-service && mvn test        # JAVA_HOME=JDK 21
[INFO] Tests run: 61, Failures: 0, Errors: 0, Skipped: 0
[INFO] BUILD SUCCESS

$ cd web && npm run build
✓ built in 13.69s

$ python-agent/.venv/Scripts/python.exe deploy/otel/verify_trace.py    # = make trace-demo
[trace] PASS；span 明细已写入 eval/out/trace-spans.json

$ PYTHONPATH=python-agent:. python-agent/.venv/Scripts/python.exe -m eval.run --label stage14
[eval] recall=1.0 fp_rate=0.3901 citation_acc=0.96 loc_acc=1.0 p95=4906ms
```

评测复现性（Stage 13 承诺的一条，本阶段再验一次）：质量/成本指标与 baseline **逐位一致**
（召回 100%、误报 39.01%、引用 96%、定位 100%、token 409500），P95 4906ms vs 4910ms（−0.08%，
墙钟噪声范围内）。

## 6. 环境坑（写进 README 的验收结论）

### 6.1 `mvn test` 报 class file version 65

第一次失败：

```
[ERROR] com/clauseguard/admin/AdminControllerTest has been compiled by a more recent version
of the Java Runtime (class file version 65.0), this version of the Java Runtime only
recognizes class file versions up to 61.0
```

不是代码问题：本机 `JAVA_HOME` 指向 jdk-17，而 `target/` 里是 JDK 21 编出的 class（65 = 21）。
显式指定 JDK 21 后 61 个测试全绿。README 的「需要 JDK 21」由此从一句提示变成一条**必须核对**的
前置条件（`java -version` 看到 17 就先改 `JAVA_HOME`）。

### 6.2 本机没有 GNU make，「3 条命令」跑不起来

`make trace-demo` 直接报 `make: command not found`——**README 的快速开始对 Windows 用户是不成立的**，
而「新人 30 分钟能跑起来」恰恰是这一阶段的验收项。补法不是把命令改成脚本，而是在 README 里给出
`make → 等价命令` 对照表，并逐条在本机跑通（含 `run-py` / `trace-demo` / `eval` / `bench` / 两个 test）。
这条是自测 README 时抓到的，不写下来等于交付物有假。

## 7. 变更清单

新增（10 个代码文件 / 9 个文档）：

| 层 | 文件 |
| --- | --- |
| Python | `app/observability.py`（259）· `app/agents/mock_model.py`（189）· `tests/test_observability.py`（221） |
| Java | `observability/TraceContextSupport.java`（132）· `observability/TraceContextSupportTest.java`（109） |
| 部署 | `deploy/otel/capture_collector.py`（188）· `deploy/otel/verify_trace.py`（286）· `deploy/demo/make_demo_contract.py`（58）· `deploy/locust/locustfile.py`（343）· `deploy/locust/single_run.py`（133） |
| 文档 | `README.md`（重写）· `docs/README.md` · `docs/ADR-0002/0003/0004` · `docs/benchmark.md` · `docs/demo-script.md` · `docs/talk-outline.md` |

修改：`app/config.py`（OTel 配置项）· `app/main.py`（根 span/两段式 resume/`_execute_run`）·
`app/graph/build.py`（`_traced` 统一埋点）· `app/agents/settings.py`（`model_backend` 显式开关）·
`app/agents/spec.py` · `pyproject.toml`（OTel + locust）· `ReviewService.java`（`inSpan` + traceparent）·
`application.yml` · `pom.xml` · 两个 Java 测试 · `AGENTS.md`（版本表新增可观测性行 + 变更规则）· `Makefile`（4 个新目标）。

## 8. 遗留风险

1. **没有真跑过 Langfuse 云端**：链路用本地 OTLP 采集器验证（协议同一套），但 Langfuse 侧的
   dataset/session 视图、成本面板字段映射未经真实账号回归；首次接云端需核对
   `gen_ai.usage.input_tokens/output_tokens` 两字段是否被成本面板识别。
2. **压测只覆盖 mock 后端与单 worker**：真实模型的时延/限流/429、`--workers 4` 下的吞吐曲线都未测。
3. **`deploy/` 不在 lint 门禁内**（`ruff check app tests` 只覆盖应用代码）；deploy 脚本里有多处
   裸 `print`（CLI 输出，符合意图），但意味着这批脚本没有风格检查兜底。
4. **Stage 12 前端工作台仍未完成**（6 路由/三栏/SSE 断线重连/375px 审批页）——已如实写进
   README 已知限制第 4 条，没有藏。
5. **`docs/` 里 `eval/stage6/stage7` 的历史验收文档被本轮评测重跑刷新过时延数字**，已回退，
   保持「历史记录不因后续跑分漂移而改写」。

## 9. 下一阶段建议

按「缺口 → 收益」排序：

1. **补 Stage 12 前端工作台**（唯一的显式欠账，演示时最直观）：6 条路由、三栏布局、
   SSE `Last-Event-ID` 断线重连、375px 审批页。
2. **接真模型跑一次评测基线**：把 `MODEL_BACKEND=live` 的召回/误报/引用率作为第二条基线，
   让「39.01% 误报是替身固有成本」这句话有对照数字。
3. **Postgres checkpoint 下的压测**：当前 MemorySaver 偏乐观，生产形态会引入写盘开销。
4. **多 worker 吞吐曲线**：`--workers 4/8` 下重跑 `make bench`，验证「加 worker 才加吞吐」的判断。
