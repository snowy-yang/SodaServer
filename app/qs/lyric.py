"""汽水音乐歌词（逐字时间轴 → LRC/YRC）。

来源: seo_track 的 lyric.content，行格式 `[起(ms),长(ms)]<行内偏移(ms),长(ms),0>字...`
与网易云 yrc 同族（wy 的 parseTools 处理 (a,b,c) 括号变体，这里是尖括号）
"""

from __future__ import annotations

import re

from app.qs.music_info import music_info

_LINE_RE = re.compile(r"^\[(\d+),\d+\]")
_WORD_RE = re.compile(r"<(\d+),(\d+),\d+>([^<]*)")


def ms_format(time_ms: int) -> str:
    total_seconds = time_ms // 1000
    minutes = total_seconds // 60
    seconds = total_seconds % 60
    return f"[{minutes:02d}:{seconds:02d}.{time_ms % 1000}]"


def parse_timed_lyric(content: str | None) -> dict[str, str]:
    lrc_lines: list[str] = []
    yrc_lines: list[str] = []
    for raw_line in str(content or "").split("\n"):
        line = raw_line.strip()
        line_match = _LINE_RE.match(line)
        if not line_match:
            continue
        time_tag = ms_format(int(line_match.group(1)))
        words = list(_WORD_RE.finditer(line[line_match.end() :]))
        if not words:
            continue
        text = "".join(word.group(3) for word in words)
        if not text.strip():
            continue  # 间奏空行
        lrc_lines.append(f"{time_tag}{text}")
        yrc_lines.append(f"{time_tag}{''.join(f'<{w.group(1)},{w.group(2)}>{w.group(3)}' for w in words)}")
    return {"lyric": "\n".join(lrc_lines), "yrc": "\n".join(yrc_lines)}


async def get_lyric(track_id: str) -> dict[str, str]:
    body = await music_info(track_id)
    lyric_content = (body.get("lyric") or {}).get("content")
    info = parse_timed_lyric(lyric_content)
    if not info["lyric"]:
        raise RuntimeError("获取歌词失败")
    return {"lyric": info["lyric"], "tlyric": "", "yrc": info["yrc"]}
