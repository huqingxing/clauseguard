"""model_gate 节点：把「一条都没审成」从静默降级改成显式失败（fail-closed）。

为什么需要这个节点：review_clause 的契约是「单条款失败不炸整轮 run」——这是对的，
19 条里错 1 条不该让整单失败。但同一条契约在「全部失败」时会产出一个看起来正常的
结论：findings=[]、degraded=false、run.completed。密钥欠费 / 配额用尽 / 模型 5xx /
网络全断都会走到这条路径，与「这份合同真的没问题」在事件流上完全不可区分——
用户看到的是「审查完成，0 个问题」。

判定条件只有一条：clauses_ok == 0 且 clauses_attempted > 0
（「一条都没审成」）。刻意不写成「错误数 > 0 就失败」：那是把局部失败升级成整单
失败，与上面的契约冲突，也会让 19 条里错 1 条的正常运行被判死。

错误码分三种，因为处理方式不同：
- MODEL_UNAVAILABLE（模型侧调用失败）→ 重试可能有用，retryable=true；
- MODEL_OUTPUT_INVALID（每次都拿到不可解析的输出）→ 重试大概率同样失败，
  但仍属「可安全重跑」，故同样 retryable=true；
- EXPERT_REVIEW_FAILED（分片装配/运行异常，既非模型失败也非解析失败）→ 同样
  retryable=true，靠 code 区分根因。
"""

from __future__ import annotations

import logging

from langchain_core.runnables import RunnableConfig

from app.events import EventType, get_emitter
from app.graph.state import ReviewState

logger = logging.getLogger("app.graph.model_gate")

NODE_NAME = "model_gate"

MODEL_UNAVAILABLE = "MODEL_UNAVAILABLE"
MODEL_OUTPUT_INVALID = "MODEL_OUTPUT_INVALID"
# 分片装配/运行异常：expert_review 的 except 分支刻意不记 llm/parse_errors（见其注释），
# 全灭且两个错误计数都是 0 时，根因既不是「模型失败」也不是「输出不可解析」。
EXPERT_REVIEW_FAILED = "EXPERT_REVIEW_FAILED"


class ModelUnavailableException(RuntimeError):
    """全部条款都没审成：整轮 run 必须判失败，不能下发「0 个发现」的完成事件。

    code 供编排层映射 run.failed.code（契约字段是 code，不是 reason），
    由它区分「模型调不通」与「模型输出全是垃圾」。
    """

    def __init__(self, message: str, code: str = MODEL_UNAVAILABLE) -> None:
        super().__init__(message)
        self.code = code


async def model_gate(state: ReviewState, config: RunnableConfig) -> dict[str, object]:
    """图节点：专家分片全部汇合后、critic 之前的一道门。

    位置的两条理由：
    1. 在 critic 之前——没审成就不该进质量校验（那会把「没数据」算成「全部通过」）；
    2. 在 cache_store / human_approval / render 之前——异常上抛即中止整图，
       不会留下「空结论写进缓存」「生成一份干净的批注稿」这类更贵的副作用。

    事件：进入时发 node.started，拦截时先发 node.failed 再抛——事件流里能直接看到
    「跑到 model_gate 就断了」，而不是只看到一条 run.failed 猜不出断在哪。
    """
    emitter = get_emitter(config)
    counters = state.get("review_counters")
    if counters is None or counters.clauses_attempted == 0:
        # 没有统计数据，或压根没有条款进入审查（ingest 失败另有 degraded 语义）：
        # 不声称「全灭」，把决定权留给既有路径。
        return {}
    if counters.clauses_ok > 0:
        return {}

    if counters.llm_errors > 0:
        code = MODEL_UNAVAILABLE
    elif counters.parse_errors > 0:
        code = MODEL_OUTPUT_INVALID
    else:
        # 既无模型失败也无解析失败却全灭：分片装配/运行异常（expert_review 的 except 分支
        # 不记 llm_errors，否则会把「装配错误」说成「模型不可用」）。
        code = EXPERT_REVIEW_FAILED
    message = (
        f"全部 {counters.clauses_attempted} 个审查单元均未产出可用结论"
        f"（模型调用失败 {counters.llm_errors}，输出不可解析 {counters.parse_errors}）："
        "整轮判失败，避免把「模型不可用」呈现成「合同无风险」"
    )
    await emitter.emit(EventType.NODE_STARTED, node=NODE_NAME, agent=NODE_NAME)
    await emitter.emit(
        EventType.NODE_FAILED,
        node=NODE_NAME,
        agent=NODE_NAME,
        code=code,
        message=message[:200],
    )
    logger.error("%s 拦截：%s", NODE_NAME, message)
    raise ModelUnavailableException(message, code=code)
