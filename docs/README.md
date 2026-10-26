# docs/ 索引

## ADR（架构决策记录）

按「一决策一文件」维护：背景 → 决策 → 备选与否决理由 → 后果。**改架构先改 ADR**。

| 编号 | 主题 | 一句话结论 |
| --- | --- | --- |
| [0001](ADR-0001-architecture.md) | 双栈分工 | Java 管业务与数据主权，Python 管编排与推理，靠 SSE 事件 + REST/MCP 通信 |
| [0002](ADR-0002-parallel-topology.md) | 并行拓扑 | 专家×分片用 LangGraph `Send` fan-out，纯函数 reducer 收敛，P95 −70.2% |
| [0003](ADR-0003-citation-whitelist.md) | 引用白名单 | 三道闸强制引用来自检索结果；丢空则降级 `unverified` 而非删除结论 |
| [0004](ADR-0004-interrupt-resume.md) | 中断恢复 | 审批断点在图内 + Postgres checkpoint + 独立 resume 端点，seq 不写图状态 |

## 阶段验收报告

| 阶段 | 主题 | 报告 |
| --- | --- | --- |
| 9 | 中断恢复与事件续接 | [stage9-acceptance.md](stage9-acceptance.md) |
| 10 | 带批注 Word 回写 | [stage10-acceptance.md](stage10-acceptance.md) |
| 11 | 增量重审与缓存（token −95%） | [stage11-acceptance.md](stage11-acceptance.md) |
| 13 | 评测与回归（120 份 / 466 标注） | [stage13-acceptance.md](stage13-acceptance.md) |
| 14 | 可观测性与交付物 | [stage14-acceptance.md](stage14-acceptance.md) |

## 性能与演示材料

| 文件 | 用途 |
| --- | --- |
| [benchmark.md](benchmark.md) | 20 并发 5 分钟压测：P50/P95/P99、错误率、平均成本 |
| [demo-script.md](demo-script.md) | 90 秒演示分镜 + 旁白（逐秒可照读） |
| [talk-outline.md](talk-outline.md) | 3 分钟技术讲解提纲：架构 / 难点 / 数据 |

## 评测

`eval/` 下的方法说明见 [eval/README.md](../eval/README.md)，产出报告见 `eval/out/report.md`。
