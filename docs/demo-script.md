# ClauseGuard 90 秒演示脚本

> 目标：90 秒内完整跑一遍「上传 → 六专家并行 → 高风险拦住 → 审批 → 批注 Word 下载」，
> 中途不退场、不解释架构。架构与指标留到 Q&A（见 [talk-outline.md](talk-outline.md)）。

## 演前 3 分钟检查（必做，别在台上试）

```bash
make demo-contract            # 生成演示合同（14 条，6 专家全覆盖 + 1 条高风险）
make up                       # 4 容器 healthy
make run-py MODEL_BACKEND=mock   # 终端 A（有密钥就去掉 MODEL_BACKEND）
cd java-service && mvn spring-boot:run   # 终端 B
curl -s localhost:8080/healthz localhost:8000/healthz   # 两个 ok
```

- 终端字号调到 **18pt 以上**，窗口只留 A/B 两个，关掉通知。
- 提前跑一遍（预热 JVM 与解析器），**保留那次产生的 reviewId 作为兜底**：
  万一现场网络抖动，直接打开已生成的 docx 与事件流录屏。
- 浏览器只留两个标签：`docs/benchmark.md`、`eval/out/report.md`。

## 分镜表（逐秒可照读）

| 时间 | 画面动作 | 旁白（照读） |
| --- | --- | --- |
| 0–8s | 终端 A，屏幕上只有 `demo-contract.docx` 文件名 | 「一份 14 条的技术开发合同，法务现在要逐条看。我让它 90 秒看完，并且告诉我哪一条不能签。」 |
| 8–20s | 终端 A 执行上传 + 建审查（两条 curl），高亮返回的 `reviewId` 与 `streamUrl` | 「上传走 Java：文件落在业务侧，Python 只拿一个下载地址——数据主权在 Java，推理在 Python。」 |
| 20–45s | 终端 A `curl -N <streamUrl>`，事件流滚动；手指点到 `review.commercial / subject / liability / ip / data / compliance` 六行 `node.started` | 「条款切完之后派给六个专家并行审：商务、主体、责任、知识产权、数据、合规。同一超步里六个 Agent 同时点亮，不是排队。」 |
| 45–55s | 事件流里 `message.delta` 连续出现风险摘要，停在 `findings_count` | 「每条结论都带引用，引用只能来自检索结果——模型编法条编号会被白名单拦掉，标成 unverified 等人复核，不会静默消失。」 |
| 55–68s | 出现 `interrupt`，指到 payload 里的 2 条 high | 「高风险条款在这里被拦住，图挂起、运行态落 Postgres。人可以三小时后才批，进程重启也不丢。」 |
| 68–78s | 终端 A 发 `approve` → `run.completed` → 打开 `download_url` 的 docx，滚到批注处 | 「批准后续跑，直接回写带批注的 Word——批注锚在原条款上，法务拿到的是能直接发给业务方的文件。」 |
| 78–86s | 切终端执行 `make trace-demo`，展示 trace 树（1 条 trace / 6 个 agent span / 带 tokens） | 「一条 trace 从 Java 贯到六个 Agent：哪个专家慢、花了多少 token，一屏能定位。」 |
| 86–90s | 切 `eval/out/report.md` 的指标表，收尾 | 「120 份合同、466 条标注：召回 100%，误报 39%，引用准确率 96%——这条基线是给人来打破的。」 |

## 现场兜底（按故障类型说，不要道歉超过 5 秒）

| 故障 | 立刻改说什么 |
| --- | --- |
| 模型超时 / 供应商抖动 | 「这里在等模型。正好说明为什么我把预算与降级做进图里——超预算的专家会被跳过并标 degraded，而不是整轮失败。」（等它自己降级完成） |
| 上传 500 | 「文件通道在 Java 侧，我直接看这条预热的 review。」→ 打开兜底 reviewId |
| 事件流不动 | 「SSE 是按 seq 严格递增的，我换成回放：`Last-Event-ID` 会把断线期间的事件补给你。」 |
| 完全不配合 | 切录屏（提前录 90 秒），旁白不变，最后一句：「所以我把演示也做成了可复现的脚本，`make trace-demo` 一条命令。」

## 90 秒内不要做的事

- 不讲 ADR、不讲 LangGraph 版本、不讲「为什么不用 CrewAI」——留给 Q&A。
- 不打开 IDE 或目录树。
- 不解释 mock：演示用的是确定性替身，被问到再说（见 talk-outline 的 Q&A 第 3 条）。
