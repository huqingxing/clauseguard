"""测试环境统一把检索切到 mock。

为什么必须在 conftest（最早被 import 的位置）设：既有 300+ 单测断言的是固定候选集
与白名单行为，它们不该依赖本机是否起了 pgvector 集群；而 `get_retrieval_settings`
是进程级 lru_cache，若在测试模块里才设环境变量就晚了。
生产默认值仍是 live（config/retrieval.yaml），验收脚本显式设 live 覆盖本行。
"""

from __future__ import annotations

import os

os.environ.setdefault("CLAUSEGUARD_RETRIEVAL_MODE", "mock")
