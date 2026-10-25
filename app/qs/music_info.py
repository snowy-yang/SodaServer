"""汽水音乐曲目信息（H5 SEO 通道）。

接口: https://beta-luna.douyin.com/luna/h5/seo_track?track_id=<id>&device_platform=web
匿名可访问（无签名要求）：返回曲目元数据（name/artists/album/duration/bit_rates/preview）、
逐字歌词（lyric.content）与试听播放信息（track_player.url_player_info / video_model）
注意：seo_track 属分享页/SEO 数据面，track_player 里只有 30 秒试听资源；
完整音频需真机凭证走 /luna/pc/track_v2，且音频经 CENC 加密，见 stream.py 头注释
"""

from __future__ import annotations

from typing import Any

from app.qs.lib.http import http_fetch

SEO_TRACK_URL = "https://beta-luna.douyin.com/luna/h5/seo_track"


async def music_info(track_id: str) -> dict[str, Any]:
    res = await http_fetch(
        f"{SEO_TRACK_URL}?track_id={track_id}&device_platform=web",
        method="get",
        headers={
            "User-Agent": "Mozilla/5.0 (Linux; Android 10) AppleWebKit/537.36",
            "X-Requested-With": "XMLHttpRequest",
        },
        timeout=15000,
    )
    body = res.body if isinstance(res.body, dict) else {}
    seo_track = body.get("seo_track") or {}
    if res.status_code != 200 or not seo_track.get("track"):
        raise RuntimeError("获取歌曲信息失败")
    return body


def cover_url(album: Any) -> str | None:
    """专辑封面 URL：urls[0] 为 CDN 前缀，uri 为资源路径，拼尺寸模板得方形封面（社区通用做法）。"""
    url_cover = (album or {}).get("url_cover") if isinstance(album, dict) else None
    uri = (url_cover or {}).get("uri")
    if not uri:
        return None
    urls = (url_cover or {}).get("urls")
    prefix = urls[0] if isinstance(urls, list) and urls else "https://p3-luna.douyinpic.com/img/"
    return f"{prefix}{uri}~c5_500x500.jpg"


async def get_pic(track_id: str) -> str | None:
    body = await music_info(track_id)
    return cover_url((body.get("seo_track") or {}).get("track", {}).get("album"))
