"""中文条号数字转换（千以内）。

抽成公共模块而不是各写一份：语料完整性校验（条号 1..N 连续）与 S5 前缀
（「第585条」）都要用，重复实现会让两处对「零/〇/两」的处理悄悄漂移。
"""

from __future__ import annotations

_DIGITS = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
           "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_UNITS = {"十": 10, "百": 100, "千": 1000}


def cn_number(text: str) -> int:
    """把「一千二百六十」转成 1260；仅支持本法条语料出现的千以内数值。"""
    section = 0
    current = 0
    for char in text:
        if char in _DIGITS:
            current = _DIGITS[char]
        elif char in _UNITS:
            section += (current or 1) * _UNITS[char]
            current = 0
    return section + current
