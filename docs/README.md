> **简历素材**：主导 ClauseGuard 合同审查 RAG 改造与量化评估：搭向量+BM25+RRF 混合检索与 80 条标注集、15 组参数矩阵；用真模型在 30 份合同上做对照实验，证明真实检索提升引用可验证性，但暴露风险召回反降的架构缺陷。

# docs/ 索引

## ADR（架构决策记录）

按「一决策一文件」维护：背景 → 决策 → 备选与否决理由 → 后果。**改架构先改 ADR**。

| 编号 | 主题 | 一句话结论 |
| --- | --- | --- |
| [0001](ADR-0001-architecture.md) | 双栈分工 | Java 管业务与数据主权，Python 管编排与推理，靠 SSE 事件 + REST/MCP 通信 |
| [0002](ADR-0002-parallel-topology.md) | 并行拓扑 | 专家×分片用 LangGraph `Send` fan-out，纯函数 reducer 收敛，P95 −70.2% |
| [0003](ADR-0003-citation-whitelist.md) | 引用白名单 | 三道闸强制引用来自检索结果；丢空则降级 `unverified` 而非删除结论 |
| [0004](ADR-0004-interrupt-resume.md) | 中断恢复 | 审批断点在图内 + Postgres checkpoint + 独立 resume 端点，seq 不写图状态 |
| [0005](ADR-0005-role-split-and-admin-hash.md) | 按角色拆两端 | 用户端=提交者 / 管理端=审核者；hash 路由只为省服务端回退；用户端接口「暂不」鉴权是登记的取舍而非漏配 |
| [0006](ADR-0006-review-thread-scope.md) | 单次审查一个 thread | `thread_id = run:{run_id}` 唯一构造；session 仍按合同版本聚合；旧 checkpoint 只读不迁移 |
| [0007](ADR-0007-review-event-atomicity.md) | 状态与事件同事务 | 方案 A：一个 `@Transactional` 写状态+事件；终态优先补偿；看门狗补终态缺口并 WARN 计数 |
| [0008](ADR-0008-admin-protected-write-paths.md) | 审批/评测纳入 ADMIN | 审批写入与评测读迁到 `/api/v1/admin/` 下受 ADMIN 保护；操作人取认证主体而非 X-Operator 头 |

## 阶段验收报告

| 阶段 | 主题 | 报告 |
| --- | --- | --- |
| 7 | 检索实验台：把检索改进量化到业务指标 | [rag-lab.md](rag-lab.md) |
| 9 | 中断恢复与事件续接 | [stage9-acceptance.md](stage9-acceptance.md) |
| 10 | 带批注 Word 回写 | [stage10-acceptance.md](stage10-acceptance.md) |
| 11 | 增量重审与缓存（token −95%） | [stage11-acceptance.md](stage11-acceptance.md) |
| 12 | 前端工作台（6 路由 / 三栏 / 375px 审批页） | [stage12-acceptance.md](stage12-acceptance.md) |
| 13 | 评测与回归（120 份 / 466 标注） | [stage13-acceptance.md](stage13-acceptance.md) |
| 14 | 可观测性与交付物 | [stage14-acceptance.md](stage14-acceptance.md) |

## 性能与演示材料

| 文件 | 用途 |
| --- | --- |
| [benchmark.md](benchmark.md) | 20 并发 5 分钟压测：P50/P95/P99、错误率、平均成本 |
| [demo.md](demo.md) | 90 秒界面演示：上传 → 并行点亮 → 风险清单 → 切管理端审批 → 回用户端下载（含实测秒数与旁白） |
| [demo-script.md](demo-script.md) | 90 秒终端版演示分镜 + 旁白（逐秒可照读） |
| [talk-outline.md](talk-outline.md) | 3 分钟技术讲解提纲：架构 / 难点 / 数据 |

## 评测

`eval/` 下的方法说明见 [eval/README.md](../eval/README.md)，产出报告见 `eval/out/report.md`。
