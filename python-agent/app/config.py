"""集中配置。运行态配置禁止散落在节点或工具里，统一从这里读取。"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """环境变量驱动配置；均可在 deploy/docker-compose 与 .env 中覆盖。"""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

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


@lru_cache
def get_settings() -> Settings:
    return Settings()
