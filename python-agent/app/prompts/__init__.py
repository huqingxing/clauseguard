"""Prompt 唯一存放处：按 `专家名/vN.md` 组织，随代码版本化。

为什么用文件而不是代码常量：红线要求 prompt 不得硬编码在节点里；md 文件可 diff、可评审、
可按版本回滚。加载器只做「读文件 + 缓存」，不做任何字符串拼接——可变内容（条款、元信息）
一律由调用方通过消息体传入，保持 prompt 文件静态可复现。
"""

from __future__ import annotations

import functools
from pathlib import Path

_PROMPT_ROOT = Path(__file__).resolve().parent


@functools.lru_cache(maxsize=16)
def load_prompt(expert: str, version: int, *, suffix: str | None = None) -> str:
    """读取 `prompts/{expert}/v{version}[.{suffix}].md` 原文。

    suffix 用于同一版本的附属指令（如 retry 纠正指令），与主 prompt 同目录同版本号管理。

    文件缺失或为空即抛错，不静默降级——prompt 丢失意味着审查行为失控，必须显式失败。
    """
    name = f"v{version}.{suffix}.md" if suffix else f"v{version}.md"
    path = _PROMPT_ROOT / expert / name
    if not path.is_file():
        raise FileNotFoundError(
            f"prompt 文件不存在: {path}（expert={expert!r}, version={version}）"
        )
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        raise ValueError(f"prompt 文件为空: {path}")
    return text
