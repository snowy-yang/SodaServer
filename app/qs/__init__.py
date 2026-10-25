"""汽水平台核心（自足，不依赖 app.http/routes 的路由层）。

平台门面（对应原 qs/index.ts 的默认导出），匿名可用面（2026-10-01 实测）：
曲目信息 / 歌词 / 封面 / 30 秒试听取流（未加密直链）。
搜索与登录态取流需 PC 签名（QS_SIGN_SERVER 或固定值，见 mssdk.py 与 qs-signer 仓库）：
  登录态（/login/qs/qr 扫码）走 X-Helios/X-Medusa 签名 /luna/pc/track_v2，
  加密音频服务端解密后经 /stream/qs/:id 代理回明文流（完整曲目，2026-10-01 全链路实测）

模块对应原 qs/ 目录：
  mssdk.py          PC 通道签名（签名服务 → 固定值 三级供给）
  passport.py       登录链路（扫码 / 短信 / sessionid 绑定 / 设备身份）
  music_search.py   搜索（PC 通道 + 签名）
  music_info.py     曲目元数据 / 封面（seo_track 匿名通道）
  lyric.py          逐字歌词 → LRC/YRC
  stream.py         取流编排（匿名试听 / 登录态签名 track_v2 → 解密 → 缓存）
  audiodecrypt.py   CENC 音频解密（AES-CTR 逐样本 + spade_a PlayAuth → key）
  lib/              请求层、凭据存储、工具（对应原 qs/lib/）
"""

from __future__ import annotations

from typing import Any

from app.qs.lyric import get_lyric
from app.qs.music_info import get_pic, music_info
from app.qs.stream import get_music_url, get_stream_buffer

__all__ = [
    "get_lyric",
    "get_music_detail_page_url",
    "get_music_url",
    "get_pic",
    "get_stream_buffer",
    "music_info",
]


def get_music_detail_page_url(song_info: dict[str, Any]) -> str:
    return f"https://music.douyin.com/qishui/share/track?track_id={song_info.get('songmid')}"
