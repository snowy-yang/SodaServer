"""业务路由 —— 搜索、取流、歌词、封面、曲目元数据、解密流代理。

处理函数返回 `ok(data)`，由 main.Envelope 统一包成 { code: 0, data }；
抛出 ApiError 由全局异常处理器转成对应 HTTP 状态码。
路径保留 MusicServer 的 /qs 段（/search/qs/...、/url/qs/...），点歌端换 base_url 即可接入
"""

from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, Path, Query, Request
from fastapi.responses import Response

from app.http.api_error import upstream
from app.http.envelope import ok
from app.http.stream_sign import sign_stream
from app.qs import get_pic, music_info
from app.qs.lyric import get_lyric
from app.qs.music_search import search as music_search
from app.qs.stream import SUPPORT_QUALITIES, get_music_url, get_stream_buffer

router = APIRouter()

QUALITIES = SUPPORT_QUALITIES

# ---- 常用 OpenAPI 响应片段 ----


def _envelope_ref(name: str) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "code": {"type": "integer", "description": "0 表示成功", "example": 0},
            "data": {"$ref": f"#/components/schemas/{name}"},
        },
        "required": ["code", "data"],
    }


_ERROR_ENVELOPE = {"$ref": "#/components/schemas/ErrorEnvelope"}


def _responses(
    data_ref: str, *, not_found_doc: bool = True, upstream_doc: bool = True
) -> dict[int | str, dict[str, Any]]:
    """组装路由的 responses：200 信封 + 通用错误码文档。"""
    docs: dict[int | str, dict[str, Any]] = {
        "200": {"description": "成功", "content": {"application/json": {"schema": _envelope_ref(data_ref)}}},
        "422": {"description": "参数校验失败", "content": {"application/json": {"schema": _ERROR_ENVELOPE}}},
    }
    if not_found_doc:
        docs["404"] = {"description": "接口不存在", "content": {"application/json": {"schema": _ERROR_ENVELOPE}}}
    if upstream_doc:
        docs["502"] = {"description": "上游平台失败", "content": {"application/json": {"schema": _ERROR_ENVELOPE}}}
    return docs


_P_ID = Path(..., description="汽水 track id", examples=["7079108541549643812"])


# ---------- 搜索 ----------


async def _search(keyword: str, page: int, limit: int) -> Any:
    try:
        result = await music_search(keyword, page)
    except Exception as err:
        raise upstream(str(err)) from err
    return {"source": "qs", "list": result.get("list") or [], "page": page, "limit": limit}


_SEARCH_DOC = (
    "PC 通道每页固定 20 条（上游不接受 count，limit 参数不改变页大小，仅回显）。"
    "搜索依赖 PC 签名（QS_SIGN_SERVER 或 data/qs-sign.json），签名不可用时返回 502。"
)


@router.get(
    "/search/qs/{keyword}",
    summary="搜索歌曲（第 1 页）",
    description=_SEARCH_DOC,
    tags=["搜索"],
    responses=_responses("SearchResult", not_found_doc=False),
)
async def search_first(
    keyword: str = Path(..., description="搜索关键词（URL 编码的中文可直接使用）", examples=["稻香"]),
    limit: int = Query(20, ge=1, le=100, description="每页条数"),
) -> Any:
    return ok(await _search(keyword, 1, limit))


@router.get(
    "/search/qs/{keyword}/{page}",
    summary="搜索歌曲（指定页）",
    description=_SEARCH_DOC,
    tags=["搜索"],
    responses=_responses("SearchResult", not_found_doc=False),
)
async def search_page(
    keyword: str = Path(..., description="搜索关键词", examples=["稻香"]),
    page: int = Path(..., ge=1, description="页码（省略时为第 1 页）", examples=[2]),
    limit: int = Query(20, ge=1, le=100, description="每页条数"),
) -> Any:
    return ok(await _search(keyword, page, limit))


# ---------- 歌曲资源 ----------


@router.get(
    "/url/qs/{id}",
    summary="获取播放地址",
    description="id 为汽水 track id。匿名只有 30 秒试听直链（授权曲；用户上传曲可匿名整曲）；"
    "登录态（/login/qs/*）走签名 track_v2 + 服务端解密，返回 /stream/qs/:id 代理地址"
    "（响应前已按请求 origin 补全为绝对地址；启用 AUTH_KEY 时附带短时 exp/sig 签名，播放器拉流免带自定义头）。"
    "登录态取流失败自动降级匿名试听。",
    tags=["歌曲资源"],
    responses=_responses("MusicUrlResult", not_found_doc=False),
)
async def url_qs(
    request: Request,
    id: str = _P_ID,
    quality: str = Query("128k", pattern="^(128k|320k)$", description="音质"),
) -> Any:
    try:
        res = await get_music_url({"songmid": id}, quality)
        if not res.get("url"):
            raise RuntimeError("获取播放地址失败")
        # 解密流代理地址返回相对路径，按请求的协议与 host 补全（兼容本机/局域网播放器）；
        # 启用 AUTH_KEY 时附带短时签名（播放器拉流无法携带自定义头，签名不暴露秘钥本身）
        url = str(res["url"])
        if url.startswith("/"):
            auth_key = os.environ.get("AUTH_KEY") or ""
            suffix = ""
            if auth_key:
                sign_path = url.split("?")[0]
                suffix = f"{'&' if '?' in url else '?'}{sign_stream(auth_key, sign_path)}"
            url = f"{request.url.scheme}://{request.headers.get('host', '')}{url}{suffix}"
        return ok({"type": res.get("type") or quality, "url": url})
    except Exception as err:
        raise upstream(str(err)) from err


# 解密流代理：/url/qs 登录态返回的相对地址，本端点完成「签名 track_v2 → 密音频下载 →
# spade key + AES-CTR 解密 → 回明文流」；媒体响应不走 { code, data } 信封
@router.get(
    "/stream/qs/{id}",
    summary="解密流代理",
    description="取流响应中返回的相对地址（/url/qs 登录态），服务端下载上游密流并在本地解密后返回明文音频；"
    "不接受客户端自行构造。解密结果缓存 10 分钟，过期后客户端重新 /url/qs 取地址。",
    tags=["歌曲资源"],
    response_class=Response,
    responses={200: {"content": {"audio/*": {}}, "description": "明文音频"}, 502: {"description": "上游平台失败"}},
)
async def stream_qs(id: str = _P_ID) -> Response:
    try:
        entry = await get_stream_buffer(str(id))
        return Response(
            content=entry.buffer,
            media_type=entry.content_type or "audio/mp4",
            headers={"Accept-Ranges": "none"},
        )
    except Exception as err:
        raise upstream(str(err)) from err


@router.get(
    "/lyric/qs/{id}",
    summary="获取歌词",
    description="逐字歌词来源为 seo_track（匿名可用），返回 LRC 原文与 YRC 逐字时间轴。",
    tags=["歌曲资源"],
    responses=_responses("LyricInfo", not_found_doc=False),
)
async def lyric_qs(id: str = _P_ID) -> Any:
    try:
        return ok(await get_lyric(id))
    except Exception as err:
        raise upstream(str(err)) from err


@router.get(
    "/pic/qs/{id}",
    summary="获取封面",
    tags=["歌曲资源"],
    responses=_responses("PicResult", not_found_doc=False),
)
async def pic_qs(id: str = _P_ID) -> Any:
    try:
        return ok({"url": await get_pic(id)})
    except Exception as err:
        raise upstream(str(err)) from err


@router.get(
    "/info/qs/{id}",
    summary="获取曲目元数据",
    description="seo_track 全量数据（匿名可用）：曲目（name/artists/album/duration/bit_rates）、逐字歌词原始内容、"
    "试听播放信息（track_player）。/url /lyric /pic 均基于此通道。",
    tags=["歌曲资源"],
)
async def info_qs(id: str = _P_ID) -> Any:
    try:
        return ok(await music_info(id))
    except Exception as err:
        raise upstream(str(err)) from err
