"""结果缓存与基线快照存储（Stage 11）。

缓存键 = sha256(clause.hash + prompt_version + model + expert)：
- clause.hash 保证「同一份条款」才复用（改一个字即 miss）；
- prompt_version 保证「改了提示词必然 miss」（验收硬要求）——prompt 一改，
  旧结论的判定口径就变了，复用等于把过期结论当新结论交付；
- model 保证换模型后不串用（不同模型的结论分布不同）；
- expert 是必须项而非可选项：prompt_version 是「每个专家各自的版本号」，六个专家
  当前全是 v1 且共用同一模型，缺了 expert 则所有专家的键完全撞车——会把主体资格
  专家的结论当成商业条款专家的结论复用（缓存正确性的硬伤，不是命中率的小数点问题）。

value = 该（专家 × 条款）产出的 findings 列表。缓存粒度取「专家×条款」而非「条款」：
一个条款会被多个专家审查，若整条款共用一个键，任一专家的 prompt 升级都会让整条失效，
命中率与成本都被拖低；分专家存则只让真正变了的那位重审。

存储后端只依赖标准库（内存 / 本地 JSON 目录），不引入 Redis/新依赖（铁律 5）。
生产多副本部署时把 IncrementalStore 换成 Postgres 实现即可，接口不变。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import tempfile
from functools import lru_cache
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.graph.state import Clause, Finding

logger = logging.getLogger("app.incremental.cache")

STORE_CONFIG_KEY = "incremental_store"
_UNSAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


class CacheSettings(BaseSettings):
    """缓存后端配置；目录为空即用内存（单进程/测试语义）。"""

    model_config = SettingsConfigDict(env_prefix="clauseguard_cache_")

    dir: str = Field(default="", description="缓存根目录；空 = 内存实现（进程内有效）。")


def cache_key(clause_hash: str, prompt_version: int, model: str, expert: str) -> str:
    """缓存键：sha256(clause.hash + prompt_version + model + expert)。"""
    return hashlib.sha256(
        f"{clause_hash}|{prompt_version}|{model}|{expert}".encode()
    ).hexdigest()


class CacheStats(BaseModel):
    """命中率指标；命中/未命中按「专家×条款」的查询次数计。"""

    hits: int = Field(default=0, description="命中次数。")
    misses: int = Field(default=0, description="未命中次数。")

    @property
    def total(self) -> int:
        return self.hits + self.misses

    @property
    def hit_rate(self) -> float:
        """命中率；无查询时定义为 0（不制造 100% 的假象）。"""
        return self.hits / self.total if self.total else 0.0


@runtime_checkable
class IncrementalStore(Protocol):
    """增量重审所需的两个命名空间：findings 缓存 + 版本基线快照。

    同步/异步：一律 async——生产后端（Postgres/MinIO）是 IO，接口先按异步定，
    后续换实现不必改动调用方。
    """

    async def get_findings(self, key: str) -> list[Finding] | None:
        """取缓存的 findings；None = 未命中。"""
        ...

    async def put_findings(self, key: str, findings: list[Finding]) -> None:
        """写入 findings 缓存（同键覆盖）。"""
        ...

    async def get_baseline(self, contract_id: str, version: int) -> list[Clause] | None:
        """取某合同某版本的条款快照；None = 无基线。"""
        ...

    async def put_baseline(
        self, contract_id: str, version: int, clauses: list[Clause]
    ) -> None:
        """写入条款快照，供下一版本 diff 使用。"""
        ...


class InMemoryIncrementalStore:
    """进程内实现：测试与单进程场景；进程重启即空（显式语义，不是假持久化）。"""

    def __init__(self) -> None:
        self._findings: dict[str, list[Finding]] = {}
        self._baseline: dict[tuple[str, int], list[Clause]] = {}

    async def get_findings(self, key: str) -> list[Finding] | None:
        cached = self._findings.get(key)
        return None if cached is None else list(cached)

    async def put_findings(self, key: str, findings: list[Finding]) -> None:
        self._findings[key] = list(findings)

    async def get_baseline(self, contract_id: str, version: int) -> list[Clause] | None:
        snapshot = self._baseline.get((contract_id, version))
        return None if snapshot is None else list(snapshot)

    async def put_baseline(
        self, contract_id: str, version: int, clauses: list[Clause]
    ) -> None:
        self._baseline[(contract_id, version)] = list(clauses)


class JsonFileIncrementalStore:
    """本地 JSON 目录实现：跨进程/重启仍有效，用于单机部署与验收复现。

    为什么不用业务库：铁律 1 禁止 Python 直连业务 MySQL；缓存属于编排层的私有派生数据，
    放在自己的命名空间里，删掉最多是「缓存全 miss」，不影响任何业务数据。
    """

    def __init__(self, root: Path) -> None:
        self._root = root
        self._findings_dir = root / "findings"
        self._baseline_dir = root / "baseline"

    async def get_findings(self, key: str) -> list[Finding] | None:
        path = self._findings_path(key)
        payload = _read_json(path)
        if payload is None:
            return None
        try:
            return [Finding.model_validate(item) for item in payload]
        except Exception:
            # 缓存文件损坏/版本不兼容：按未命中处理（缓存是加速手段，不是事实源）。
            logger.warning("缓存文件无法解析，按未命中处理: %s", path)
            return None

    async def put_findings(self, key: str, findings: list[Finding]) -> None:
        path = self._findings_path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = [finding.model_dump(mode="json") for finding in findings]
        _write_json(path, payload)

    async def get_baseline(self, contract_id: str, version: int) -> list[Clause] | None:
        payload = _read_json(self._baseline_path(contract_id, version))
        if payload is None:
            return None
        try:
            return [Clause.model_validate(item) for item in payload]
        except Exception:
            logger.warning("基线快照无法解析，按无基线处理: %s/%s", contract_id, version)
            return None

    async def put_baseline(
        self, contract_id: str, version: int, clauses: list[Clause]
    ) -> None:
        path = self._baseline_path(contract_id, version)
        path.parent.mkdir(parents=True, exist_ok=True)
        _write_json(path, [clause.model_dump(mode="json") for clause in clauses])

    def _findings_path(self, key: str) -> Path:
        # 两级目录避免单目录文件过多（键是 64 位十六进制）。
        return self._findings_dir / key[:2] / f"{key}.json"

    def _baseline_path(self, contract_id: str, version: int) -> Path:
        return self._baseline_dir / _safe_name(contract_id) / f"{version}.json"


def _safe_name(value: str) -> str:
    """把外部标识收敛为安全文件名，杜绝路径穿越。"""
    return _UNSAFE_NAME.sub("_", value).strip("_") or "default"


def _read_json(path: Path) -> list[dict[str, Any]] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        logger.warning("缓存文件读取失败，按未命中处理: %s", path)
        return None
    return payload if isinstance(payload, list) else None


def _write_json(path: Path, payload: list[dict[str, Any]]) -> None:
    # 先写临时文件再原子替换：进程被中断时不会留下半截 JSON（半截文件会被当成 miss，
    # 但至少不会让下一次读取抛异常）。
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, prefix=".tmp-", delete=False
    )
    try:
        with handle:
            json.dump(payload, handle, ensure_ascii=False)
        os.replace(handle.name, path)
    finally:
        if os.path.exists(handle.name):
            os.unlink(handle.name)


def build_incremental_store(settings: CacheSettings | None = None) -> IncrementalStore:
    """按配置构造缓存后端；目录为空即内存（部署不配置也能跑，只是不跨进程复用）。"""
    resolved = settings or CacheSettings()
    if not resolved.dir:
        return InMemoryIncrementalStore()
    return JsonFileIncrementalStore(Path(resolved.dir))


@lru_cache(maxsize=1)
def default_incremental_store() -> IncrementalStore:
    """进程级默认后端（基础设施，非运行态）：未显式注入时使用。

    为什么允许一个进程级单例：它是无状态缓存的连接句柄，等价于连接池，
    不是图的运行态（运行态仍在 ReviewState）。显式注入优先，测试可完全替换。
    """
    return build_incremental_store()


def get_store(config: RunnableConfig) -> IncrementalStore:
    """从 config 取本 run 的缓存后端；未注入则用进程默认（铁律 4：优先走注入）。"""
    configurable = (config or {}).get("configurable") or {}
    store = configurable.get(STORE_CONFIG_KEY)
    if isinstance(store, IncrementalStore):
        return store
    return default_incremental_store()
