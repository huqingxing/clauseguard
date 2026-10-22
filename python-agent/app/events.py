"""跨服务 SSE 事件模型桥接层。

为什么存在：events.schema.json 是事件结构的唯一事实源，models.py 由
datamodel-code-generator 从 schema 生成（见 contracts/generate.sh）。本模块只做
重新导出与少量常量枚举，不定义任何字段——改契约必须先改 schema 再重新生成，
杜绝手写 DTO 漂移。运行前必须先运行 `make gen`（或 contracts/generate.sh）。
"""

from enum import StrEnum

from app.contracts.models import (  # noqa: F401  生成模型，单一事实源的镜像
    Interrupt,
    MessageDelta,
    NodeFinished,
    NodeStarted,
    RunCompleted,
    RunFailed,
    RunStarted,
    ToolCall,
    ToolResult,
)


class EventType(StrEnum):
    """点分小写事件类型；扩充必须先登记 contracts/events.schema.json 的 $defs。"""

    RUN_STARTED = "run.started"
    NODE_STARTED = "node.started"
    NODE_FINISHED = "node.finished"
    MESSAGE_DELTA = "message.delta"
    TOOL_CALL = "tool.call"
    TOOL_RESULT = "tool.result"
    INTERRUPT = "interrupt"
    RUN_COMPLETED = "run.completed"
    RUN_FAILED = "run.failed"


class Producer(StrEnum):
    JAVA_SERVICE = "java-service"
    PYTHON_AGENT = "python-agent"
