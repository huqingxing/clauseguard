"""集中配置。运行态配置禁止散落在节点或工具里，统一从这里读取。"""

from functools import lru_cache

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """环境变量驱动配置；均可在 deploy/docker-compose 与 .env 中覆盖。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        # 允许按字段名构造（测试注入 exporter/端点的场景），别名只用于读环境变量。
        populate_by_name=True,
    )

    service_name: str = "python-agent"

    # Java 业务服务地址：合同/审批/审计数据唯一入口（架构铁律 1）
    java_base_url: str = "http://localhost:8080"

    # Postgres 仅存 LangGraph checkpoint 运行态，不是业务库
    postgres_dsn: str = "postgresql://clauseguard:clauseguard@localhost:5432/clauseguard_agent"

    redis_url: str = "redis://localhost:6379/0"
    minio_endpoint: str = "localhost:9000"
    minio_bucket: str = "contracts"
    minio_access_key: str = "clauseguard"
    minio_secret_key: str = "clauseguard123"

    # LLM 供给（密钥只从 .env 读，绝不下库）
    llm_provider: str = "dashscope"
    llm_api_key: str = ""

    # 运行约束：并发上限、token 预算、花费熔断（人民币元）
    concurrency_limit: int = 4
    max_tokens: int = 8192
    max_cost_cny: float = 5.0

    # ---------------- 可观测性（Stage 14）----------------
    # 为什么沿用 OTEL_* 标准变量名：Langfuse/Collector 接入文档全是这套名字，
    # 换后端（Langfuse Cloud → 自建 Collector）只改环境变量，代码不动。
    otel_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices("OTEL_ENABLED", "CLAUSEGUARD_OTEL_ENABLED"),
        description="是否启用追踪；默认关闭，保证无 collector 的测试/离线场景零副作用。",
    )
    otel_endpoint: str = Field(
        default="",
        validation_alias=AliasChoices(
            "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "CLAUSEGUARD_OTEL_ENDPOINT"
        ),
        description="OTLP/HTTP traces 端点；Langfuse 为 <host>/api/public/otel/v1/traces。",
    )
    otel_headers: str = Field(
        default="",
        validation_alias=AliasChoices("OTEL_EXPORTER_OTLP_HEADERS", "CLAUSEGUARD_OTEL_HEADERS"),
        description="OTLP 请求头，`k=v,k2=v2` 形式；Langfuse 用 Authorization=Basic <b64>。",
    )
    otel_service_name: str = Field(
        default="clauseguard-python",
        validation_alias=AliasChoices("OTEL_SERVICE_NAME", "CLAUSEGUARD_OTEL_SERVICE_NAME"),
    )
    otel_environment: str = Field(
        default="local",
        validation_alias=AliasChoices(
            "OTEL_DEPLOYMENT_ENVIRONMENT", "CLAUSEGUARD_OTEL_ENVIRONMENT"
        ),
    )
    otel_sample_ratio: float = 1.0
    otel_export_timeout_s: float = 10.0


@lru_cache
def get_settings() -> Settings:
    return Settings()
