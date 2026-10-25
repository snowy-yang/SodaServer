"""汽水音乐 URL 获取（直连）。

两条取流通道：
  匿名（未登录）: seo_track → url_player_info（VOD GetPlayInfo 免登录）→ 未加密试听直链（30s 132k）
  登录态:         官方 PC 签名 X-Helios/X-Medusa（见 mssdk.py）→ /luna/pc/track_v2
                  → 加密音频（cenc-aes-ctr）→ spade key 解密 → /stream/qs/:id 代理端点回明文流
                  （相对地址，路由层负责补全 origin）
  实测（2026-10-01）: track_v2 匿名=加密试听；完整曲目需登录 sessionid（汽水音乐 APP 扫码 /login/qs/qr）。
  登录请求失败自动降级匿名试听。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

from app.qs.audiodecrypt import decrypt_audio_buffer, resolve_audio_key
from app.qs.lib.http import http_fetch
from app.qs.mssdk import PC_UA, mssdk_available, pc_common_params, sign_qishui_request
from app.qs.music_info import music_info
from app.qs.passport import get_cookie, get_session_id

# 官方直连接口支持的音质：授权曲匿名只有 30 秒试听 128k（登录态取码率最高条目，324k/hi_res 视权益）；
# 用户上传曲匿名即可整曲（实测 2026-10-02：DJ 版整曲 204.84s / 三档 68k・132k・260k）
SUPPORT_QUALITIES = ["128k", "320k"]

TRACK_V2_URL = "https://api.qishui.com/luna/pc/track_v2"
STREAM_PREFIX = "/stream/qs/"
STREAM_CACHE_TTL = 10 * 60.0
STREAM_CACHE_MAX = 3


@dataclass
class StreamEntry:
    buffer: bytes
    content_type: str
    bitrate: int
    size: int
    resolved_at: float


# 已解密音频缓存（/url 解析一次，/stream 直接消费；LRU + TTL）
_stream_cache: OrderedDict[str, StreamEntry] = OrderedDict()
_inflight: dict[str, asyncio.Task[StreamEntry]] = {}


def _cache_put(track_id: str, entry: StreamEntry) -> None:
    _stream_cache.pop(track_id, None)  # 重新插入以刷新 LRU 顺序
    _stream_cache[track_id] = entry
    while len(_stream_cache) > STREAM_CACHE_MAX:
        _stream_cache.popitem(last=False)


def get_cached_stream(track_id: str) -> StreamEntry | None:
    """已缓存的明文音频（/stream/qs/:id 用；缓存未命中返回 None，由路由触发重解析）。"""
    entry = _stream_cache.get(track_id)
    if entry is None:
        return None
    import time

    if time.time() - entry.resolved_at > STREAM_CACHE_TTL:
        _stream_cache.pop(track_id, None)
        return None
    return entry


# 匿名通道：码率最高的未加密条目（带 PlayAuth = 加密，直接回 URL 不可播，跳过）
def _pick_plain_audio_url(play_info_list: Any) -> str | None:
    entries = [
        item
        for item in (play_info_list or [])
        if isinstance(item, dict) and item.get("MainPlayUrl") and not item.get("PlayAuth")
    ]
    if not entries:
        return None
    return max(entries, key=lambda item: item.get("Bitrate") or 0)["MainPlayUrl"]


async def _anonymous_preview_url(track_id: str) -> str:
    body = await music_info(track_id)
    player_info_url = ((body.get("track_player") or {}).get("url_player_info")) if isinstance(body, dict) else None
    if not player_info_url:
        raise RuntimeError("获取失败")
    res = await http_fetch(player_info_url, method="get", timeout=15000)
    play_info = res.body if res.status_code == 200 and isinstance(res.body, dict) else {}
    play_info_list = (((play_info.get("Result") or {}).get("Data") or {}).get("PlayInfoList")) or []
    url = _pick_plain_audio_url(play_info_list)
    if not url:
        raise RuntimeError("获取失败")
    return str(url)


# 登录态通道：官方 PC 客户端签名 X-Helios/X-Medusa（签名服务器或固定值，见 mssdk.py）
def _pick_best_entry(play_info_list: Any) -> dict[str, Any] | None:
    entries = [item for item in (play_info_list or []) if isinstance(item, dict) and item.get("MainPlayUrl")]
    if not entries:
        return None
    return max(entries, key=lambda item: item.get("Bitrate") or 0)


async def _fetch_track_player(url: str, headers: dict[str, str], request_body: str) -> dict[str, Any]:
    res = await http_fetch(url, method="post", headers=headers, body=request_body, timeout=15000)
    body = res.body if isinstance(res.body, dict) else {}
    track_player = body.get("track_player") or {}
    if res.status_code != 200 or not track_player.get("url_player_info"):
        err_msg = (body.get("status_info") or {}).get("status_msg") or body.get("message") or ""
        raise RuntimeError(f"track_v2 获取失败: HTTP {res.status_code} {err_msg}".rstrip())
    return track_player


def _track_v2_body(track_id: str) -> str:
    # 必须与 JS JSON.stringify 逐字节一致（紧凑分隔符）；x-ss-stub 按 body 内容计算
    return json.dumps(
        {"track_id": track_id, "media_type": "track", "queue_type": "search_one_track", "scene_name": "search"},
        separators=(",", ":"),
    )


async def _fetch_track_player_by_pc(track_id: str) -> dict[str, Any]:
    session_id = get_session_id()
    if not mssdk_available():
        raise RuntimeError(
            "PC 签名不可用（16689 未探测到签名服务，可配置 QS_SIGN_SERVER 或 data/qs-sign.json，见 qs-signer 仓库）"
        )
    url = f"{TRACK_V2_URL}?{urlencode(pc_common_params())}"
    request_body = _track_v2_body(track_id)
    headers: dict[str, str] = {
        "content-type": "application/json; charset=utf-8",
        "user-agent": PC_UA,
        "x-luna-background-type": "foreground",
        "x-luna-is-background-req": "0",
        "x-luna-is-local-user": "0",
        "x-tt-trace-id": f"00-{secrets.token_hex(8)}-{secrets.token_hex(8)}-01",
        "x-ss-stub": hashlib.md5(request_body.encode()).hexdigest().upper(),
    }
    if session_id:
        headers["cookie"] = get_cookie() or f"sessionid={session_id}"
    signed = await sign_qishui_request(url, headers, request_body)
    if not signed:
        raise RuntimeError("官方签名失败")
    return await _fetch_track_player(url, {**headers, **signed}, request_body)


async def _resolve_signed_stream(track_id: str) -> StreamEntry:
    track_player = await _fetch_track_player_by_pc(track_id)

    play_info_res = await http_fetch(track_player["url_player_info"], method="get", timeout=15000)
    play_info = play_info_res.body if isinstance(play_info_res.body, dict) else {}
    play_info_list = (((play_info.get("Result") or {}).get("Data") or {}).get("PlayInfoList")) or []
    entry = _pick_best_entry(play_info_list)
    if entry is None:
        raise RuntimeError("无可用音频资源")

    audio_res = await http_fetch(entry["MainPlayUrl"], method="get", timeout=30000)
    encrypted = audio_res.raw
    if not encrypted:
        raise RuntimeError("音频下载失败")

    buffer = encrypted
    content_type = "audio/mp4"
    if entry.get("PlayAuth"):
        hex_key = resolve_audio_key({"spade_a": entry["PlayAuth"]})
        decrypted = decrypt_audio_buffer(encrypted, hex_key)
        buffer = decrypted["buffer"]
        content_type = decrypted["content_type"]

    import time

    stream_entry = StreamEntry(
        buffer=buffer,
        content_type=content_type,
        bitrate=entry.get("Bitrate") or 0,
        size=len(buffer),
        resolved_at=time.time(),
    )
    _cache_put(track_id, stream_entry)
    return stream_entry


async def get_stream_buffer(track_id: str) -> StreamEntry:
    """解析明文音频流（/stream/qs/:id 用；先查缓存，未命中重新解析，并发请求共享同一解析任务）。"""
    cached = get_cached_stream(track_id)
    if cached is not None:
        return cached
    work = _inflight.get(track_id)
    if work is None:
        work = asyncio.create_task(_resolve_signed_stream(track_id))
        _inflight[track_id] = work
        try:
            return await work
        finally:
            _inflight.pop(track_id, None)
    return await work


async def get_music_url(song_info: dict[str, Any], type_: str) -> dict[str, str]:
    track_id = str((song_info or {}).get("songmid") or "")
    if get_session_id():
        # 登录态：签名 track_v2 + 服务端解密，/url 阶段完成解析（快速失败），/stream 直接消费缓存
        try:
            entry = await get_stream_buffer(track_id)
            print(f"[qs/url] {track_id} 解密流就绪 bitrate={entry.bitrate} size={entry.size}")
            return {"type": type_, "url": f"{STREAM_PREFIX}{track_id}"}
        except Exception as err:  # noqa: BLE001
            print(f"[qs/url] {track_id} 登录态取流失败，降级匿名试听: {err}")
            return {"type": type_, "url": await _anonymous_preview_url(track_id)}
    # 匿名：未加密试听直链
    return {"type": type_, "url": await _anonymous_preview_url(track_id)}
