"""汽水音乐搜索（App 搜索接口直连）。

接口: https://api.qishui.com/luna/search/track?<公共参数>&q=<关键词>&cursor=
官方 PC 通道: luna_pc 参数 + X-Helios/X-Medusa 签名（签名服务器或固定值，见 mssdk.py）。
              PC 签名不可用时搜索直接报错；被风控时上游返回 200 + 空 body（无 result_groups）。
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

from app.qs.lib.http import http_fetch
from app.qs.lib.utils import format_play_time, size_formate
from app.qs.mssdk import PC_UA, mssdk_available, pc_common_params, sign_qishui_request
from app.qs.music_info import cover_url

SEARCH_URL = "https://api.qishui.com/luna/search/track"
PC_PAGE_SIZE = 20


def _build_search_url(keyword: str, cursor: int) -> str:
    params = {
        **pc_common_params(),
        "q": keyword,
        "cursor": str(cursor),
        "search_id": "",
        "search_method": "input",
        "debug_params": "",
        "from_search_id": "",
        "search_scene": "normal",
        "scene_name": "search",
    }
    return f"{SEARCH_URL}?{urlencode(params)}"


def _get_singer(artists: Any) -> str:
    names: list[str] = []
    for singer in artists or []:
        if isinstance(singer, dict) and singer.get("name"):
            names.append(str(singer["name"]))
    return "、".join(names)


def _handle_result(raw_list: Any) -> list[dict[str, Any]]:
    if not raw_list:
        return []
    out: list[dict[str, Any]] = []
    for item in raw_list:
        track = ((item or {}).get("entity") or {}).get("track") or {}
        if not track.get("id"):
            continue
        # types 反映匿名实际可获取的资源：30 秒试听（preview.bit_rates 为试听码率）
        # track.bit_rates 是完整曲目码率元数据（66k~hi_res），但匿名无权取流，不对外声明
        preview_size = ((track.get("preview") or {}).get("bit_rates") or [None])[0]
        size = size_formate(preview_size.get("size")) if isinstance(preview_size, dict) else None
        types = [{"type": "128k", "size": size}]
        _types = {"128k": {"size": size}}
        album = track.get("album") or {}
        out.append(
            {
                "singer": _get_singer(track.get("artists")),
                "name": track.get("name"),
                "albumName": album.get("name"),
                "albumId": album.get("id"),
                "source": "qs",
                "interval": format_play_time((track.get("duration") or 0) / 1000),
                "songmid": str(track["id"]),
                "img": cover_url(album),
                "lrc": None,
                "types": types,
                "_types": _types,
                "typeUrl": {},
            }
        )
    return out


def _handle_search_body(raw_body: dict[str, Any], cursor: int, page: int) -> dict[str, Any]:
    groups = raw_body.get("result_groups") or []
    group = next((g for g in groups if isinstance(g, dict) and g.get("data")), {"data": [], "has_more": False})
    songs = _handle_result(group.get("data"))
    total = cursor + len(songs) + (1 if group.get("has_more") else 0)
    return {
        "list": songs,
        "allPage": page + 1 if group.get("has_more") else page,
        "limit": LIMIT,
        "total": total,
        "source": "qs",
    }


LIMIT = 30


async def search(keyword: str, page: int = 1) -> dict[str, Any]:
    if not mssdk_available():
        raise RuntimeError(
            "PC 签名不可用（16689 未探测到签名服务，可配置 QS_SIGN_SERVER 或 data/qs-sign.json，见 qs-signer 仓库）"
        )
    # PC 通道每页固定 20 条（上游默认、不接受 count），cursor 必须按 20 步进，否则翻页与 limit 错位重叠
    cursor = PC_PAGE_SIZE * (page - 1)
    url = _build_search_url(keyword, cursor)
    # 签名覆盖真实发出的头：先定头、再签、再发
    headers = {"User-Agent": PC_UA}
    signed = await sign_qishui_request(url, headers)
    if not signed:
        raise RuntimeError("官方签名失败")
    res = await http_fetch(url, method="get", headers={**headers, **signed}, timeout=15000)
    body = res.body if isinstance(res.body, dict) else {}
    # 被风控/无结果时上游不返回 result_groups（200 + {"extra":{"empty_search":1}}）
    if not body.get("result_groups"):
        raise RuntimeError("搜索无结果（疑似风控）")
    return _handle_search_body(body, cursor, page)
