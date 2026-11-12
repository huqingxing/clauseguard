# ADR-0006：LangGraph thread 作用域从「合同版本」改为「单次审查」

- 状态：**已实施并验收**（2026-11-12）
- 取代：ADR-0004 中「`thread_id = contract_id:version`」的会话粒度决策（其余断点/恢复机制不变）
- 关联：README「已知限制」原第 4 条已删除；`docs/stage12-acceptance.md` 5.1 记录的是修复前现象，保留作为历史证据

## 1. 背景

Stage 9 为支持人工审批断点，把运行态交给 Postgres checkpointer 持久化，并按
`thread_id = f"{contract_id}:{version}"` 划分命名空间（见 ADR-0004）。这把「合同版本」
误当成了「会话」：**同一合同同一版本的两笔独立审查共用一份 checkpoint**。

LangGraph 在同一 thread 上再次 `ainvoke` 时会带上一次终态，`merge_findings` 按
`(clause_id, risk_type)` 合并，于是第二笔的 findings 变成「旧 + 新」：

- Stage 12 实测：`23 → 46`，其中旧的 23 条因条款已不存在被 Critic 降级为 `unverified`；
- 本次修复前用确定性 mock 复现：`9 → 18`，其中 `9` 条 `unverified`，且 checkpointer 里
  只有 `c-dup:1` 一个 thread（见 `tests/thread_scope/baseline_pre_fix.json` 采集口径）。

该缺陷会让「结果看起来错了」，且随审查次数累积，不属于可容忍的展示偏差。

### 约束

1. 审批续跑、断线重连、增量重审三项能力**不得退化**；
2. 事件契约（`contracts/events.schema.json`，`additionalProperties: false`）不得改；
3. 不清理历史 checkpoint；不用「run 前清空 findings」绕过；不改 findings 合并逻辑。

## 2. 决策

### 2.1 一次审查 = 一个 thread，由 run_id 唯一派生

新增唯一构造入口 `python-agent/app/graph/threads.py`：

```python
def review_thread_id(run_id: str) -> str:
    return f"run:{run_id}"
```

选 `run_id` 而非 Java 的 `reviewId` 作为审查标识，理由：

- `run_id` 是事件契约里既有的「审查运行 ID，跨服务追踪主键」（`RunStarted.run_id`），
  Java 已从 `run.started` 捕获并落 `review.run_id`，审批时用它定位 resume 端点；
- Java 侧 `reviewId` 虽是 `UUID.randomUUID()`，但 `PythonAgentClient.streamRun` 的
  `reviewId` 参数**没有进入 `/v1/runs` 请求体**（body 只有 `contract_id/version/mode/
  base_version/source`），编排层拿不到它。透传需要改 Java + 改内部请求体，且事件契约无
  `review_id` 字段，改动面更大；
- 一次 `POST /v1/runs` 生成一个新 `run_id`，与「一笔审查」一一对应，语义已足够。

### 2.2 构造点收敛

| 位置 | 改动 |
|---|---|
| `app/db.py` | 删除 `thread_id_for(contract_id, version)`；模块 docstring 改为「只落库/读出，不构造」 |
| `app/main.py` `start_run` | `thread_id = review_thread_id(run_id)` |
| `app/main.py` `resume_run` | **由 `run_id` 直接派生** `review_thread_id(run_id)`，不再读注册表里的 `thread_id` |
| `eval/runner.py` | 评测旁路改用 `review_thread_id(f"eval-{contract_id}")` |
| `app/graph/build.py`、locust 注释 | 更新旧约定描述 |

`run_registry` 表结构与字段不变：首跑仍写入 `thread_id`（恒等映射，便于审计），
但 resume **不依赖该落盘值**，因此也完全不依赖 `contract_id/version`。

### 2.3 断点恢复为何不退化

resume 端点的 path 参数是 `run_id`，首跑与 resume 是同一笔业务操作 → 同一个 `run_id`
→ `review_thread_id` 算出同一个 thread → checkpointer 命中同一份断点。
`run_registry` 仍负责跨进程保留 `event_seq`/`trace_id`/`session`，seq 续接机制不变。
断线重连走 Java 侧事件回放（`replaySince(reviewId, afterSeq)`），根本不经过 Python
thread_id；增量重审的 diff/缓存键是 `(contract_id, version)` 与条款指纹，与 thread 正交。
三者均无需改动，且已由回归测试覆盖。

### 2.4 观测维度不跟着改

`langfuse.session.id` 仍按 `f"{contract_id}:v{version}"` 聚合：session 是**观测聚合维度**
（便于横向对比同合同多笔审查），thread 是**状态隔离维度**，两者不该绑定。

为保证「单个审查仍可过滤」，根 span 增加（并同步打一条 INFO 日志）：

```
clauseguard.review.id  = run-a1c922f0          # 一次审查 = 一次 run
clauseguard.run.id     = run-a1c922f0          # 既有字段，保留
clauseguard.thread.id  = run:run-a1c922f0      # 新增，指向实际 checkpoint 命名空间
langfuse.session.id    = c-tag:v1              # 保持合同版本聚合
```

Langfuse / OTLP 后端按单审查过滤示例：

```
traces where metadata."clauseguard.review.id" = "run-a1c922f0"
```

日志等价过滤（无需 Langfuse）：

```
app.main | grep "run tags:" | grep "review.id=run-a1c922f0"
```

实测输出见 `tests/thread_scope/` 验收脚本；`review.id` 与 `thread.id` 一一对应，
但前者是业务 ID、后者是存储命名空间，排查 checkpointer 问题时用后者。

## 3. 代价

### 3.1 正在跑的旧审查无法 resume（选择「部署时排空」）

升级后，旧审查的断点仍挂在 `contract_id:version` 命名的 thread 上，而 resume 会用
`run:{run_id}` 找 checkpoint，两者对不上。**演示项目选择不自动迁移**：

- 旧 thread 数据**保留只读**，不清理、不改名、不回写；新键 `run:*` 与旧键天然不冲突，
  新代码不会读取旧 checkpoint（回归用例 `test_t4_legacy_thread_data_is_untouched_and_not_read`
  先造一份真实旧格式 checkpoint，再断言它仍在、且新审查 findings 干净无 `unverified`）；
- 若 `run_registry` 里的记录仍是旧格式 thread，`resume_run` 记 WARN 并返回
  `409 LEGACY_THREAD_SCOPE`（「请重新发起审查」），**不读旧 checkpoint、不跑空图、不报 500**；
- **上线动作**：切换前等待所有在跑审查到达终态（或明确告知用户重新发起），再发布新版本。
  生产若不能停机排空，需要额外做一层「按旧 thread 键只读回放」的迁移，本 ADR 不含该设计。

### 3.2 其它

- `run_id` 变成 thread 派生的关键约定，不能为空：`review_thread_id("")` 显式 `ValueError`
  fail-fast，否则所有审查会重新串到 `run:` 上。
- checkpoint 数量随审查笔数线性增长（原先按合同版本收敛）。演示规模可忽略；
  生产需要为 checkpointer 表配 TTL/归档策略，本轮未做。
- README「已知限制」删去该条后，后续条目编号整体前移一位（原 5→4 … 原 13→12），
  README 内部交叉引用已同步修正；`docs/stage12-acceptance.md`、`docs/stage14-acceptance.md`
  等**历史验收文档中的编号引用按当时版本理解**，为保留历史证据不回改。

## 4. 影响面

| 层 | 是否改动 | 说明 |
|---|---|---|
| Python 编排构造点 | ✅ | `app/graph/threads.py` 新增；`main.py` 首跑/resume；`db.py` 删旧构造函数 |
| Python 评测旁路 | ✅ | `eval/runner.py` 统一入口 |
| Java 业务服务 | ❌ | 无改动；仍是唯一文件写入方，仍按 `run_id` 转发审批 |
| 事件契约 | ❌ | 无 `review_id` 字段，`additionalProperties:false` 不变 |
| `run_registry` 表 / DDL | ❌ | 字段不变，thread_id 落库值改为 `run:*` |
| 断线重连（Java 事件回放） | ❌ | 不经过 Python thread_id |
| 增量重审（diff / 缓存键） | ❌ | 键是 `(contract_id, version)` + 条款指纹 |
| findings 合并逻辑 | ❌ | `merge_findings` 一行未动 |
| 历史 checkpoint | ❌ | 不清理、不迁移，保留只读 |
| 观测 | ✅（仅增） | session 不变，新增 review/run/thread 三个 tag |

## 5. 验收（真实输出，`MODEL_BACKEND=mock`）

回归测试：`python-agent/tests/thread_scope/`（`pytest tests/thread_scope -v` → **9 passed**）。
所有用例走真实 `/v1/runs` 与 `/v1/runs/{run_id}/resume` 端点，共享注入的 checkpointer，
只替换 ingest 的外部 HTTP 下载，不注入 config、不清 findings。

```
T1 first=9 unverified=0 | second=9 unverified=0
   threads=['run:run-c099785c', 'run:run-f40acdaf']
T2 v1=9 v2=8 overlap=0 | threads=['run:run-579f6390', 'run:run-e0e0ab62']
T3 now=9 pre_fix=9 content_equal=True   # 与 a182d29 采集的基线逐字段一致
```

- **T1** 同 contract + 同 version 连跑两次：第二次 findings 数 == 第一次，无 `unverified`
  堆积，且落了两个不同的 `run:*` thread（修复前是 `9 → 18 / 9 unverified`）。
- **T2** v1/v2 各审一次：findings 条款集合交集为 0。
- **T3** 与修复前同输入对比：数量与内容逐字段一致（基线快照
  `baseline_pre_fix.json`，归一化解析层随机临时文件名前缀后对比）。
- **兼容**：旧格式 checkpoint 存在时，新代码不读取、不报错、不清理；旧格式
  `run_registry` 记录 resume 返回明确 `409 LEGACY_THREAD_SCOPE`（不是 500、不跑空图、
  不写任何新 checkpoint），由
  `test_legacy_registry_record_is_rejected_with_409_not_500` 锁定。

阶段 4 续跑端到端（`python -m tests.thread_scope.acceptance_resume`）：

```
[1] 创建       : POST /v1/runs -> run_id=run-04374472 thread=run:run-04374472
                事件=['run.started', 'node.started', 'node.finished']... seq=1..23
[2] interrupt  : type=interrupt | completed=False | threads=['run:run-04374472']
[3] 审批 resume: POST /v1/runs/run-04374472/resume -> run.completed | findings=5 unverified=0
                seq=24..27 (pause_max=23, 续接=True)
[4] 重启后 resume: 新 app 实例 | threads=['run:run-04374472']
                -> run.completed=True findings=5
                 seq=28..29 | 仍定位同一 thread=run:run-04374472
四步全部通过：审批续跑、seq 续接、重启后 thread 定位均不退化。
```

另有一条更硬的固化用例 `test_restart_between_interrupt_and_resume_still_completes`：
**重启发生在 interrupt 与 resume 之间**（模拟审批隔夜/跨发布），新 app 实例仍定位同一
thread 并跑到 `completed`，seq 续接、findings 保留。
