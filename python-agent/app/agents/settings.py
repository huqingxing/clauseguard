"""专家 Agent 层的模型路由与运行预算配置。

为什么不并进 app/config.py：config.py 承载服务级配置（端口、存储连接），本类只承载 LLM
路由与编排参数，边界清晰；后续若要统一收纳，只需改 spec.default_chat_model() 一处。
默认走 OpenAI 兼容接口（DashScope compatible-mode / vLLM / One-API 均可对接），
通过环境变量覆盖，禁止在代码里写死模型或密钥。
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

ExecutionMode = Literal["parallel", "serial"]


class AgentSettings(BaseSettings):
    """LLM 路由与编排配置；环境变量前缀 CLAUSEGUARD_AGENT_（如 CLAUSEGUARD_AGENT_LLM_MODEL）。"""

    model_config = SettingsConfigDict(env_prefix="clauseguard_agent_", extra="ignore")

    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: str = ""
    llm_model: str = "gpt-4o-mini"
    # 审查场景要求确定性输出，温度默认 0。
    llm_temperature: float = 0.0
    llm_max_tokens: int | None = 2048
    llm_timeout_s: float = 60.0
    # 模型供给来源：live=真实 OpenAI 兼容模型；mock=确定性替身（压测/无密钥演示）。
    # 为什么做成显式开关而非自动降级：缺密钥时静默走替身会让人误以为「模型跑通了」。
    model_backend: Literal["live", "mock"] = "live"
    # 替身每次调用的固定延迟（秒），代表模型思考时间；压测时延由它主导。
    mock_latency_s: float = 0.12

    # 成本估算单价（元 / 1K tokens），仅用于预算控制与 run.completed 的 cost_cny；
    # 默认值对应 DashScope qwen 档位，接其他供应商时按实际价格覆盖。
    llm_input_price_cny_per_1k: float = 0.0008
    llm_output_price_cny_per_1k: float = 0.002

    # 编排参数：parallel=Send 并行拓扑，serial=单节点串行（验收对照与降级兜底）。
    execution_mode: ExecutionMode = "parallel"
    # 单个 run 内并发专家分片/模型调用的上限（asyncio.Semaphore，按 run 隔离）。
    concurrency_limit: int = 5
    # 单 run 预算：token 上限、成本上限（元）、墙钟时限（秒，<=0 不设限）。
    max_run_tokens: int = 2_000_000
    max_run_cost_cny: float = 50.0
    run_deadline_s: float = 900.0

    # Postgres checkpointer 连接串（psycopg 异步池）；为空则回退 MemorySaver（本地/测试）。
    # 库名 clauseguard_agent（基础设施版本表锁定 PostgreSQL 16）。
    pg_dsn: str = ""

    def cost_cny(self, input_tokens: int, output_tokens: int) -> float:
        """按单价估算成本（元）；纯算术，供各节点记账复用。"""
        return (
            input_tokens * self.llm_input_price_cny_per_1k
            + output_tokens * self.llm_output_price_cny_per_1k
        ) / 1000


@lru_cache
def get_agent_settings() -> AgentSettings:
    """进程内单例；测试可通过 monkeypatch 环境变量后调用 cache_clear 重建。"""
    return AgentSettings()
