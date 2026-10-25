"""审查运行状态与领域模型。

为什么用 TypedDict 承载运行态：ReviewState 是 LangGraph 的状态载体，所有节点只通过它读写；
图的可中断/可恢复依赖该状态可被 checkpoint 序列化（铁律 4），故既有字段名由契约锁定，禁止改名。
Clause/Finding/Citation/Budget/ReviewTask 是节点间传递的纯数据结构，用 pydantic v2 保证校验与
序列化。带 Annotated 的字段走自定义 reducer——并行分支并发写同一通道时由 reducer 收敛，
reducer 全部为纯函数（禁止 IO），这是并行拓扑下状态一致性的基础。
"""

import hashlib
import time
from typing import Annotated, Final, NotRequired, TypedDict

from pydantic import BaseModel, Field, model_validator

# Critic 打回重审的轮数上限，硬编码在状态模块（任务要求：次数上限硬编码在状态里）。
# 语义：同一轮 run 内 Critic 最多把未通过校验的 finding 交回专家重审 2 轮；
# 第 3 次校验仍失败即标记 unverified 放行——计数器单调递增，图必然收敛，不会死循环。
MAX_CRITIC_RETRIES: Final = 2


def _text_hash(text: str) -> str:
    """条款内容指纹：sha256 前 16 位（与解析层 clause_hash 同一口径）。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


class Citation(BaseModel):
    """检索白名单中的引用条目；引用只能来自这里，禁止模型自由生成法条编号（红线）。"""

    source_id: str = Field(..., description="白名单条目唯一 ID，可追溯来源。")
    title: str = Field(..., description="法规/条款标题。")
    excerpt: str = Field(..., description="命中摘录片段，用于前端展示与审计留痕。")
    url: str | None = Field(default=None, description="可选来源链接，便于人工跳转核对。")


class Clause(BaseModel):
    """合同经切分后的单个条款，是审查与审批回写的最小单位。

    Stage 11 增 no/hash：版本 diff 的定位键是「条款编号 + 内容哈希」——条款 ID 由切分
    序号生成（doc#0003），插入一条就会整体错位，只有编号才是跨版本的稳定身份；
    hash 用于判定编号相同时内容是否真的变了。
    """

    clause_id: str = Field(..., description="条款唯一 ID，审批回写 Word 批注时定位用。")
    index: int = Field(..., description="条款在合同中的顺序位，决定展示与批注顺序。")
    text: str = Field(..., description="条款原文。")
    risk_level: str = Field(default="unknown", description="low/medium/high/unknown，初切分时未知")
    no: str = Field(
        default="", description="条款编号原文（如 第三条 / 3.1），跨版本 diff 的稳定键。"
    )
    hash: str = Field(default="", description="text 的 sha256 前 16 位；留空时按 text 现算。")

    @model_validator(mode="before")
    @classmethod
    def _fill_hash(cls, data: object) -> object:
        """未显式给 hash 时按 text 现算，保证「同一份条款在任何入口都有同一指纹」。

        为什么放在 before 校验器：diff/缓存都要用 hash 做键，若允许空值则键会随入口漂移
        （解析层给前 16 位、测试直接构造则为空），命中率失去意义。
        """
        if isinstance(data, dict):
            text = data.get("text")
            if not data.get("hash") and isinstance(text, str):
                data = {**data, "hash": _text_hash(text)}
        return data


class Finding(BaseModel):
    """单个专家 Agent 对某条款的审查发现。"""

    clause_id: str = Field(..., description="关联条款 ID。")
    agent: str = Field(..., description="产出该发现的专家 Agent 名。")
    risk_type: str = Field(
        ..., min_length=1, description="风险类别标签，与 clause_id 共同构成跨专家去重键。"
    )
    risk_level: str = Field(
        ...,
        description=(
            "low/medium/high/unverified；unverified 为引用校验降级值，驱动人工审批阈值。"
        ),
    )
    summary: str = Field(..., description="发现摘要，面向业务人员的结论。")
    citations: list[Citation] = Field(
        default_factory=list, description="支撑结论的白名单引用，不可为空才能过关。"
    )
    suggestion: str = Field(default="", description="修改建议，回写 Word 批注用。")
    evidence: str = Field(default="", description="条款原文逐字片段，定位风险位置用。")
    citation_whitelist: tuple[str, ...] = Field(
        default=(),
        description=(
            "产出该发现时本次检索返回的 source_id 集合快照；Critic 据此复核引用"
            "（citation.source_id 必须存在于该次检索返回集合），是引擎白名单之外的第二道防线。"
        ),
    )
    stale: bool = Field(
        default=False,
        description=(
            "Stage 11：所属条款在新版本中已被删除（removed）。true 表示历史结论仅作追溯"
            "保留——不进报告（Word 批注/发现数/人工审批），但仍在状态里可查。"
        ),
    )


def active_findings(findings: list[Finding]) -> list[Finding]:
    """剔除 stale 后的「本版本有效」findings——报告/计数/审批口径的唯一入口。

    Stage 11：stale 的历史结论所属条款已被删除，既没有可挂的批注锚点也不该计入发现数，
    但必须留在 state 里（红线：不得静默丢弃）。「剔除」只发生在对外呈现层，状态仍完整。
    """
    return [finding for finding in findings if not finding.stale]


class Budget(BaseModel):
    """本次运行的成本与时限约束，超阈值触发熔断/降级。

    spent_* 由各节点增量写入、经 merge_budget 累加；max_*/deadline 为上限，全程不变。
    """

    max_tokens: int = Field(..., description="token 硬上限，超限即停。")
    max_cost_cny: float = Field(..., description="花费硬上限（人民币元），超限熔断。")
    deadline_ts: int = Field(..., description="截止时间戳（epoch 秒），超时降级；<=0 表示不设限。")
    spent_input_tokens: int = Field(default=0, description="已消耗输入 token（reducer 累加）。")
    spent_output_tokens: int = Field(default=0, description="已消耗输出 token（reducer 累加）。")
    spent_cost_cny: float = Field(default=0.0, description="已消耗估算成本（元，reducer 累加）。")

    @property
    def spent_tokens(self) -> int:
        """已消耗总 token；预算判定与 run.completed 用量都用这个口径。"""
        return self.spent_input_tokens + self.spent_output_tokens


def merge_budget(left: Budget, right: Budget) -> Budget:
    """LangGraph reducer：把各分支的消耗增量累加进预算。

    上限取较严者（同一 run 内各分支上限一致，取 max 只为并发下确定）；
    spent 求和——每个节点只写自己的增量，累加交给本函数，节点不读-改-写整个预算。
    """
    return Budget(
        max_tokens=max(left.max_tokens, right.max_tokens),
        max_cost_cny=max(left.max_cost_cny, right.max_cost_cny),
        deadline_ts=max(left.deadline_ts, right.deadline_ts),
        spent_input_tokens=left.spent_input_tokens + right.spent_input_tokens,
        spent_output_tokens=left.spent_output_tokens + right.spent_output_tokens,
        spent_cost_cny=left.spent_cost_cny + right.spent_cost_cny,
    )


def budget_exhausted(budget: Budget, now: float | None = None) -> bool:
    """预算是否已超限（token / 成本 / 截止时间任一触发）。"""
    if budget.spent_tokens >= budget.max_tokens:
        return True
    if budget.spent_cost_cny >= budget.max_cost_cny:
        return True
    if budget.deadline_ts > 0 and (now if now is not None else time.time()) >= budget.deadline_ts:
        return True
    return False


def budget_delta(input_tokens: int, output_tokens: int, cost_cny: float) -> Budget:
    """构造只携带消耗增量的 Budget，供各节点执行后经 merge_budget 累加。

    limits 传 0：merge_budget 对上限取 max，与 run 级上限合并后仍保留原值——
    节点只报「我花了多少」，不代言整个预算（并发下读-改-写整个预算会互相覆盖）。
    """
    return Budget(
        max_tokens=0,
        max_cost_cny=0.0,
        deadline_ts=0,
        spent_input_tokens=input_tokens,
        spent_output_tokens=output_tokens,
        spent_cost_cny=cost_cny,
    )


_RISK_RANK: dict[str, int] = {"unverified": 0, "low": 1, "medium": 2, "high": 3}


def _finding_key(finding: Finding) -> tuple[str, str]:
    return (finding.clause_id, finding.risk_type)


def _merge_two(left: Finding, right: Finding) -> Finding:
    """同 (clause_id, risk_type) 两条 finding 的收敛：risk_level 高者胜，同级 evidence 长者胜。

    仍同级则保留左者（先到者）——确定性优先，保证 reducer 幂等； citations 取并集去重，
    让胜出结论拥有全部检索依据。citation_whitelist 同样取并集：合并后的结论由多位专家的
    检索共同支撑，任一出处的引用都应通过 Critic 的「该次检索集合」复核。
    """
    left_rank = _RISK_RANK.get(left.risk_level, 0)
    right_rank = _RISK_RANK.get(right.risk_level, 0)
    if right_rank > left_rank:
        winner, loser = right, left
    elif right_rank < left_rank:
        winner, loser = left, right
    elif len(right.evidence) > len(left.evidence):
        winner, loser = right, left
    else:
        winner, loser = left, right
    merged: list[Citation] = list(winner.citations)
    seen = {citation.source_id for citation in merged}
    for citation in loser.citations:
        if citation.source_id not in seen:
            merged.append(citation)
            seen.add(citation.source_id)
    whitelist = tuple(sorted({*winner.citation_whitelist, *loser.citation_whitelist}))
    return winner.model_copy(update={"citations": merged, "citation_whitelist": whitelist})


class FindingTombstone(BaseModel):
    """findings 通道的删除标记：Critic 打回时按 (clause_id, risk_type) 移除旧版本。

    为什么需要墓碑而不是让重审结果直接合并：同键合并会做 citations 并集，未通过校验的
    旧引用（如伪造 id）会污染重审后的正确结论。先删后加才能让「交回重审的条款以全新
    finding 替换旧版本」的语义确定成立。墓碑只在单次 update 列表内存续，reducer 消费后
    不出现在终态（通道静止值始终是纯 Finding 列表）。
    """

    clause_id: str = Field(..., description="待删除 finding 的条款 ID。")
    risk_type: str = Field(..., description="待删除 finding 的风险类别（去重键另一半）。")

    @property
    def key(self) -> tuple[str, str]:
        return (self.clause_id, self.risk_type)


def merge_findings(
    left: list[Finding], right: list[Finding | FindingTombstone]
) -> list[Finding]:
    """LangGraph reducer：按 (clause_id, risk_type) 去重合并，必须幂等。

    并行拓扑下多个专家/分片会并发写 findings：同一条款同一风险只保留一条
    （risk_level 高者胜、同级 evidence 长者胜），citations 并集去重。
    FindingTombstone 按出现顺序删除对应键——Critic 的 update 列表里墓碑总在重写/重审
    结果之前（同批内顺序由单节点保证，跨节点不写墓碑），删除语义确定。
    纯函数、无 IO；重复/冲突/空输入/墓碑的语义由 tests/agents/test_merge_findings.py 锁定。
    """
    merged: dict[tuple[str, str], Finding] = {}
    for item in (*left, *right):
        if isinstance(item, FindingTombstone):
            merged.pop(item.key, None)
            continue
        key = _finding_key(item)
        existing = merged.get(key)
        merged[key] = item if existing is None else _merge_two(existing, item)
    return list(merged.values())


def merge_reviewed(left: list[str], right: list[str]) -> list[str]:
    """LangGraph reducer：已审 (专家, 条款) 对取并集（去重、保持首次出现顺序）。

    并行分支各自追加自己审过的对，取并集而非覆盖；顺序稳定保证写缓存的顺序可复现。
    """
    merged: dict[str, None] = {pair: None for pair in left}
    for pair in right:
        merged.setdefault(pair, None)
    return list(merged)


def merge_degraded(left: bool, right: bool) -> bool:
    """LangGraph reducer：任一分支置降级即降级（单调置位，不会互相覆盖）。"""
    return left or right


def merge_event_seq(left: int, right: int) -> int:
    """LangGraph reducer：事件序号取较大值（单调），保证断点恢复后续接而非重置。

    为什么用 max 而非 last-value：并行分支不写序号，序号只由编排层经
    graph.update_state 落盘（每次运行/恢复各写一次），取 max 避免并发写相互覆盖。
    """
    return max(left, right)


class ReworkInstruction(BaseModel):
    """Critic 打回指令：哪个专家的哪条条款、为什么没通过、要改什么。

    reasons 是确定性校验产出的失败原因（非模型自评）；期望修正点文案来自
    prompts/critic/v1.rework.md（版本化文件，红线：prompt 不硬编码在节点里），
    由 Critic 节点把条款 id 与 reasons 填入模板生成。
    """

    expert: str = Field(..., description="专家名（registry 键），决定打回哪个节点。")
    clause_id: str = Field(..., description="需重审的条款 ID。")
    agent: str = Field(..., description="专家 Agent 名（事件 agent 字段用）。")
    reasons: list[str] = Field(default_factory=list, description="未通过校验的确定性原因列表。")


class ReviewTask(BaseModel):
    """Send 载荷：单个专家的一个条款分片（每片 ≤ SHARD_SIZE 条）。

    走 review_task 单通道（LastValue）：同一超步内多个分片并发调用同一节点时，
    每次调用只读到自己的载荷——LangGraph 对每个 Send 任务隔离应用写入。
    注意本版本 LangGraph 的语义：Send 派发的节点只见载荷键、不见父状态，故
    dispatch 的载荷还须自带 contract_id/version/budget（节点执行所需通道）；
    载荷写入按任务隔离，不会经 reducer 并入终态（不会重复累加预算）。

    rework_hints：Critic 打回时按 clause_id 携带重审指令（Stage 8）。普通派发为空——
    该字段让 Send 隔离的节点也能拿到「为什么要重审」，无需回读父状态。
    """

    expert: str = Field(..., description="专家名（registry 键）。")
    priority: str = Field(..., description="high/low，决定预算超限时是否跳过。")
    shard_index: int = Field(..., description="该专家第几个分片（从 0 开始）。")
    shard_total: int = Field(..., description="该专家分片总数。")
    clauses: list[Clause] = Field(default_factory=list, description="本分片待审条款。")
    rework_hints: dict[str, str] = Field(
        default_factory=dict,
        description="clause_id -> Critic 重审指令；普通派发为空，打回轮由 Critic 填充。",
    )


FULL_MODE: Final = "full"
INCREMENTAL_MODE: Final = "incremental"


class IncrementalInfo(BaseModel):
    """本次运行的增量重审账本（Stage 11）。

    为什么单独成模型：报告必须写明「本次是 full 还是 incremental、复用了多少条」，
    这些数字由 diff 节点一次算定并随状态走，避免各节点各自统计口径漂移。
    全量重审（mode=full）刻意不查缓存——全量是「重算一遍」的语义基线，
    若也命中缓存，增量与全量的 token 对比就失去意义，验收口径会自相矛盾。
    """

    mode: str = Field(default=FULL_MODE, description="full=全量重审；incremental=仅重审变更。")
    baseline_version: int = Field(default=0, description="参与比对的基线版本号；0 表示无基线。")
    changed_count: int = Field(default=0, description="编号相同但内容变化的条款数。")
    added_count: int = Field(default=0, description="新增条款数。")
    removed_count: int = Field(default=0, description="被删除条款数。")
    unchanged_count: int = Field(default=0, description="内容与基线一致的条款数。")
    reused_findings: int = Field(default=0, description="命中缓存直接复用的 findings 条数。")
    stale_findings: int = Field(
        default=0, description="因条款删除而标记 stale 的历史 findings 条数。"
    )
    cache_hits: int = Field(default=0, description="缓存命中次数（按 专家×条款 计）。")
    cache_misses: int = Field(default=0, description="缓存未命中次数（按 专家×条款 计）。")
    cache_hit_rate: float = Field(
        default=0.0, description="命中率 = hits/(hits+misses)；无查询为 0。"
    )
    changed_clause_ids: list[str] = Field(default_factory=list, description="changed 条款 ID。")
    added_clause_ids: list[str] = Field(default_factory=list, description="added 条款 ID。")
    removed_clause_ids: list[str] = Field(default_factory=list, description="removed 条款 ID。")

    @property
    def is_incremental(self) -> bool:
        return self.mode == INCREMENTAL_MODE


class ReviewState(TypedDict):
    """LangGraph 运行态；字段名由契约锁定，禁止改名（保证 checkpoint 兼容）。

    Stage 11 新增的增量字段一律 NotRequired：既有运行/测试的初始状态不携带它们时，
    节点按「全量」语义处理（渐进式兼容，不改既有 checkpoint 形状）。
    """

    contract_id: str
    version: int
    clauses: list[Clause]
    findings: Annotated[list[Finding], merge_findings]
    budget: Annotated[Budget, merge_budget]
    critic_failures: int
    approved: bool
    degraded: Annotated[bool, merge_degraded]
    review_task: ReviewTask
    rework: list[ReworkInstruction]
    # 事件序号：编排层经 graph.update_state 落盘到 checkpoint，断点恢复时续接而非重置
    # （任务要求：重启进程后 resume，seq 继续递增不重置）。取 max 合并，避免并发覆盖。
    event_seq: Annotated[int, merge_event_seq]
    # 合同内容哈希：ingest 阶段算定，作为人工审批乐观锁基线（Java 校验防篡改）。
    content_hash: str
    # 回写渲染结果占位（Stage 9 仅占位；后续阶段接 python-docx 生成批注 Word）。
    render_result: str
    # ---- Stage 11 增量重审（全部可选：缺省即全量语义）----
    review_mode: NotRequired[str]
    base_version: NotRequired[int]
    baseline: NotRequired[list[Clause]]
    review_targets: NotRequired[dict[str, list[str]]]
    incremental: NotRequired[IncrementalInfo]
    # 本轮「实际审过」的 (专家, 条款) 对，形如 "expert|clause_id"。
    # 为什么需要它：缓存必须记下「这条没发现问题」这个结论——只缓存有 finding 的条款，
    # 正常条款每次增量都要重审，省钱幅度会被打掉一半以上。
    reviewed_pairs: NotRequired[Annotated[list[str], merge_reviewed]]
