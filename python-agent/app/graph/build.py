"""图拓扑：ingest -> diff -> dispatch -> (Send fan-out: expert_review ×N) -> critic ->
cache_store -> human_approval -> render -> END。

并行模式（默认，execution_mode=parallel）：dispatch 按专家范畴把条款切成
≤8 条的分片，经 LangGraph Send API 动态派发；同名词节点被多个分片并发调用，
各分片只读自己的 review_task 载荷；findings/budget/degraded 走自定义 reducer
收敛（纯函数，无 IO）。预算超限时 dispatch 直接不派发低优先级专家并置
degraded——降级在派发前决策，确定可复现。

Stage 8 起 critic 是专家汇合点：三类确定性校验（引用白名单/证据落地/完整性）
全部通过或经重审后仍超限→标记 unverified 放行，统一汇入 human_approval。
Stage 9 起 human_approval 是图内人工审批断点：存在 risk_level=high 的 finding 即
interrupt() 挂起（运行态由 Postgres checkpointer 持久化，可恢复），否则放行进入
render；render 本阶段仅占位，保证审批后图抵达 END 并下发 run.completed。

串行模式（execution_mode=serial）：ingest -> diff -> serial_review -> critic ->
cache_store -> human_approval -> render -> END，同一引擎顺序执行，用于并行/串行对照与降级兜底。

Stage 11 的两个新节点：
- diff（ingest 之后）：算版本 diff、命中缓存即复用、removed 条款历史 finding 标 stale，
  产出 review_targets 收窄后续 fan-out；全量模式下退化为「审全部」，不查缓存
  （全量是成本对比基线，若也复用缓存则 token 对比失去意义）。
- cache_store（critic 之后、human_approval 之前）：写回 findings 缓存与当前版本基线快照。
  位置的两条约束：在 critic 之后——只缓存已过确定性校验的结论；在 human_approval
  之前——审批是可能挂起数小时的图内断点，缓存必须在断点前落盘，否则审批期间的
  增量运行会全部 miss。

checkpointer 由 main.py 编译时注入（PostgresSaver，thread_id=contract_id:version）；
本模块只负责节点与边的装配，不持有任何连接资源。
"""

from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.agents.settings import AgentSettings, get_agent_settings
from app.graph.nodes.cache_store import cache_store
from app.graph.nodes.critic import critic
from app.graph.nodes.diff import diff_node
from app.graph.nodes.dispatch import dispatch
from app.graph.nodes.expert_review import expert_review
from app.graph.nodes.human_approval import human_approval
from app.graph.nodes.ingest import ingest
from app.graph.nodes.render import render
from app.graph.nodes.serial_review import serial_review
from app.graph.state import ReviewState


def build_graph(
    settings: AgentSettings | None = None, checkpointer: Any = None
) -> CompiledStateGraph[ReviewState, Any, ReviewState, ReviewState]:
    """按执行模式装配图；settings 可注入（测试对照并行/串行两种拓扑）。

    checkpointer 为可选项：生产注入 PostgresSaver，测试可注入 MemorySaver 验证
    中断/恢复语义而不依赖外部 Postgres。
    """
    resolved = settings if settings is not None else get_agent_settings()
    parallel = resolved.execution_mode != "serial"
    builder = StateGraph(ReviewState)
    builder.add_node("ingest", ingest)

    # critic 的打回目标随拓扑而变（Send 载荷 vs serial_review），用闭包把模式
    # 捕获进节点——运行态依赖走参数/注入，不用模块级全局变量（铁律 4）。
    async def critic_node(state: ReviewState, config: RunnableConfig) -> Any:
        return await critic(state, config, parallel=parallel)

    builder.add_node("critic", critic_node)
    builder.add_node("cache_store", cache_store)
    builder.add_node("human_approval", human_approval)
    builder.add_node("render", render)
    builder.add_node("diff", diff_node)
    if not parallel:
        builder.add_node("serial_review", serial_review)
        builder.add_edge(START, "ingest")
        builder.add_edge("ingest", "diff")
        builder.add_edge("diff", "serial_review")
        # 串行模式的打回也回到 serial_review（只重审指令条款），再汇入 critic。
        builder.add_edge("serial_review", "critic")
    else:
        builder.add_node("dispatch", dispatch)
        builder.add_node("expert_review", expert_review)
        builder.add_edge(START, "ingest")
        builder.add_edge("ingest", "diff")
        builder.add_edge("diff", "dispatch")
        # dispatch 的派发/收尾全部经 Command(goto=[Send...] | END) 动态路由，
        # 这里只声明 fan-out 目标的静态出边；分片执行完毕即汇入 critic。
        builder.add_edge("expert_review", "critic")
    # critic 的三条出路全部经 Command(goto=...) 动态路由，不建静态出边：
    #   - 通过/不可路由/轮数用尽/预算耗尽 -> goto="cache_store"；
    #   - 并行打回 -> goto=[Send("expert_review", 载荷)]；
    #   - 串行打回 -> goto="serial_review"（指令写 state["rework"]）。
    # 为什么不能同时建静态边 critic->cache_store：本版本 LangGraph 中静态边与
    # Command(goto) 会同时生效（最小复现验证），打回轮的 Send 与静态边目标交错执行，
    # 导致 human_approval 在 Send 任务中途触发 interrupt、resume 后两链路缠绕。
    # 下游静态边保留：cache_store / human_approval / render 只返回普通 dict，无动态路由。
    builder.add_edge("cache_store", "human_approval")
    builder.add_edge("human_approval", "render")
    builder.add_edge("render", END)
    return builder.compile(checkpointer=checkpointer)
