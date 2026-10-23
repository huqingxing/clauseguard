"""图拓扑：ingest -> echo -> END。

为什么不带 checkpointer：铁律 4 要求节点可中断/可恢复，但该能力（Postgres checkpointer）在
后续阶段接入；Stage 3 只验证事件流形状，compile 时不挂 checkpointer 即内存态运行。
"""

from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.graph.nodes.echo import echo
from app.graph.nodes.ingest import ingest
from app.graph.state import ReviewState


def build_graph() -> CompiledStateGraph[ReviewState, Any, ReviewState, ReviewState]:
    builder = StateGraph(ReviewState)
    builder.add_node("ingest", ingest)
    builder.add_node("echo", echo)
    builder.add_edge(START, "ingest")
    builder.add_edge("ingest", "echo")
    builder.add_edge("echo", END)
    return builder.compile()
