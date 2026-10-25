"""系统路由 —— 健康检查与平台能力清单。"""

from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Request

from app.http.envelope import ok
from app.qs.stream import SUPPORT_QUALITIES

router = APIRouter(tags=["系统"])

# 单平台服务端：能力位与 MusicServer 的平台清单同构（仅 qs 一项，其余能力不对外声明）
_CAPABILITIES = {
    "search": True,
    "url": True,
    "lyric": True,
    "pic": True,
    "boards": False,
    "hotSearch": False,
    "songList": False,
    "singer": False,
    "album": False,
    "albumSearch": False,
    "playlist": False,
    "playlistSearch": False,
    "playlistCategories": False,
    "parse": False,
    "userPlaylist": False,
}


@router.get(
    "/health",
    summary="健康检查",
    tags=["系统"],
)
async def health(request: Request) -> Any:
    started_at = getattr(request.app.state, "started_at", None) or time.time()
    return ok({"status": "ok", "uptime": round(time.time() - started_at)})


@router.get(
    "/sources",
    summary="获取支持的平台",
    description="单平台服务端，恒返回汽水一项；音质为官方直连接口的支持范围。",
    tags=["系统"],
)
async def sources() -> Any:
    return ok(
        {
            "platforms": [
                {
                    "id": "qs",
                    "name": "汽水音乐",
                    "qualities": SUPPORT_QUALITIES,
                    "capabilities": dict(_CAPABILITIES),
                }
            ]
        }
    )
