"""检索实验台配置：模型名、维度、融合权重一律从配置读，代码里不写死常量。

为什么单独一套 Settings 而不复用 app.config.Settings：检索库是知识库（Postgres），
与 LangGraph checkpoint 的 pg_dsn 是两条独立连接，且本模块要能在不启动整套
Agent 应用的前提下被评测脚本/索引脚本单独 import。env_file 按文件位置定位仓库根
.env（不依赖 CWD），与阶段 15 修好的配置来源口径保持一致。

阶段 6 新增 YAML 配置源：config/retrieval.yaml 是「实验台结论」的落盘形态，字段用
业务名（strategy/embedding/top_k/...），通过 validation_alias 映射到 Settings 字段。
为什么仍走 pydantic-settings 而不是自己 open().read()：环境变量 > .env > YAML >
代码默认值这四层优先级必须由同一套 source 链管理，手搓解析迟早会在某一层出现
「改了不生效」的静默覆盖。
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)

# retrieval/config.py -> python-agent -> clauseguard（仓库根）
ENV_FILE = Path(__file__).resolve().parents[2] / ".env"
# retrieval/config.py 的父目录是 python-agent/；配置文件跟着包走，不跟着 CWD 走。
RETRIEVAL_YAML = Path(__file__).resolve().parents[1] / "config" / "retrieval.yaml"

EmbeddingBackend = Literal["E1", "E2", "E3"]
FusionMethod = Literal["rrf", "weighted"]
RetrievalMode = Literal["mock", "live"]

# 三种后端的默认档位。为什么用「OpenAI 兼容 /v1/embeddings」统一三种：
# bge-m3 / bge-small-zh 是本地模型，引入 sentence-transformers 属于框架级新依赖
# （铁律禁止）；改由本地推理服务（Xinference/TEI/Infinity 等）暴露同一协议端点，
# 代码零分支，只需换 base_url + model + dim。E1 则直接指向百炼兼容端点。
EMBEDDING_PROFILES: dict[str, dict[str, str | int]] = {
    "E1": {
        "model": "text-embedding-v3",
        "dim": 1024,
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    },
    "E2": {
        "model": "bge-m3",
        "dim": 1024,
        "base_url": "http://127.0.0.1:9997/v1",
    },
    "E3": {
        "model": "bge-small-zh",
        "dim": 512,
        "base_url": "http://127.0.0.1:9997/v1",
    },
}


class RetrievalSettings(BaseSettings):
    """环境变量 > .env 文件 > config/retrieval.yaml > 代码默认值。"""

    model_config = SettingsConfigDict(
        env_prefix="CLAUSEGUARD_RETRIEVAL_",
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # YAML 排在 env/.env 之后（低优先级）：文件是实验台结论的默认值，运维用
        # 环境变量临时覆盖时必须立即生效，否则回滚要先改文件再重启，故障窗口变长。
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            YamlConfigSettingsSource(
                settings_cls, yaml_file=RETRIEVAL_YAML, yaml_file_encoding="utf-8"
            ),
            file_secret_settings,
        )

    # 兼容开关。mock 只影响「审查工具取候选」与 Java MCP 的注册选择，检索端点本身
    # 永远是真实能力；离线演示时不该连库却也不该改变 /v1/retrieval/search 的语义。
    retrieval_mode: RetrievalMode = Field(
        default="live",
        validation_alias=AliasChoices("CLAUSEGUARD_RETRIEVAL_MODE", "mode"),
    )

    # 知识库连接串；允许 Python 直连 Postgres，但绝不指向业务库 MySQL。
    # 默认串带 connect_timeout：库不可达时审查链路要能快速降级（ADR-0003），
    # 而不是被 libpq 的默认 20~30s 连接超时拖住整轮 run。
    pg_dsn: str = (
        "postgresql://clauseguard:clauseguard@localhost:5432/clauseguard_agent?connect_timeout=2"
    )
    schema_dim: int = 1024

    embedding_backend: EmbeddingBackend = Field(
        default="E1",
        validation_alias=AliasChoices("CLAUSEGUARD_RETRIEVAL_EMBEDDING_BACKEND", "embedding"),
    )
    # 留空表示用 EMBEDDING_PROFILES[backend] 的默认值；显式给出时覆盖，便于实验台
    # 换模型而不改代码。维度必须与该模型真实输出一致，store 建表时会校验。
    embedding_model: str = ""
    embedding_dim: int = 0
    embedding_base_url: str = ""
    embedding_api_key: str = Field(
        default="",
        validation_alias=AliasChoices(
            "CLAUSEGUARD_RETRIEVAL_EMBEDDING_API_KEY",
            "CLAUSEGUARD_AGENT_LLM_API_KEY",
            "LLM_API_KEY",
        ),
    )
    embedding_batch_size: int = 32
    embedding_max_retries: int = 2
    # 服务端硬上限：百炼兼容端点实测 batch>10 返回 400「batch size is invalid」。
    # 配置值 32 是「一次组织多少条」，实际请求按此上限切成子批，二者不冲突。
    provider_batch_limit: int = 10
    embedding_timeout_s: float = 30.0

    fusion_method: FusionMethod = Field(
        default="rrf",
        validation_alias=AliasChoices("CLAUSEGUARD_RETRIEVAL_FUSION_METHOD", "fusion"),
    )
    rrf_k: int = Field(
        default=60,
        validation_alias=AliasChoices("CLAUSEGUARD_RETRIEVAL_RRF_K", "fusion_rrf_k"),
    )
    dense_weight: float = Field(
        default=0.6,
        validation_alias=AliasChoices("CLAUSEGUARD_RETRIEVAL_DENSE_WEIGHT", "fusion_dense_weight"),
    )
    sparse_weight: float = Field(
        default=0.4,
        validation_alias=AliasChoices(
            "CLAUSEGUARD_RETRIEVAL_SPARSE_WEIGHT", "fusion_sparse_weight"
        ),
    )
    candidate_k: int = 50
    default_k: int = Field(
        default=5,
        validation_alias=AliasChoices("CLAUSEGUARD_RETRIEVAL_DEFAULT_K", "top_k"),
    )

    index_strategy: str = Field(
        default="S1_whole",
        validation_alias=AliasChoices("CLAUSEGUARD_RETRIEVAL_INDEX_STRATEGY", "strategy"),
    )
    use_bm25_default: bool = Field(
        default=True,
        validation_alias=AliasChoices("CLAUSEGUARD_RETRIEVAL_USE_BM25", "use_bm25"),
    )
    use_rerank_default: bool = Field(
        default=False,
        validation_alias=AliasChoices("CLAUSEGUARD_RETRIEVAL_USE_RERANK", "use_rerank"),
    )
    bm25_enabled: bool = True
    stopwords_file: str = ""

    def resolved_embedding(self) -> tuple[str, int, str]:
        """返回 (model, dim, base_url)；显式配置优先，否则用后端档位默认值。"""
        profile = EMBEDDING_PROFILES[self.embedding_backend]
        model = self.embedding_model or str(profile["model"])
        dim = self.embedding_dim or int(profile["dim"])
        base_url = self.embedding_base_url or str(profile["base_url"])
        return model, dim, base_url.rstrip("/")


@lru_cache
def get_retrieval_settings() -> RetrievalSettings:
    return RetrievalSettings()
