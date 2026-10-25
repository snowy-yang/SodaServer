"""纯工具函数（对应原 qs/lib/utils.ts）。"""

from __future__ import annotations

import math


def size_formate(size: float | int | None) -> str:
    """将字节数格式化为可读字符串。"""
    if not size:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB"]
    number = math.floor(math.log(size) / math.log(1024))
    return f"{size / math.pow(1024, number):.2f} {units[number]}"


def format_play_time(seconds: float | int | None) -> str:
    """秒数 → mm:ss；0 返回 --/--。"""
    try:
        value = float(seconds)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        value = 0
    if math.isnan(value) or value < 0:
        value = 0
    minutes = int(value // 60)
    secs = int(value % 60)
    if minutes == 0 and secs == 0:
        return "--/--"
    return f"{minutes:02d}:{secs:02d}"
