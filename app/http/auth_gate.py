"""全局调用秘钥鉴权 —— AUTH_KEY 环境变量启用（未设置则完全不鉴权）。

验证头统一为 X-Server-Key；全部路径（含文档页/健康检查/404）都要求秘钥，
未授权请求一律 401，避免被扫描器区分出接口存在性（404/401 差异）。
CORS 预检（OPTIONS）无响应体、不泄露内容，豁免。

/stream/* 流代理例外：播放器/下载器直接 GET 媒体地址、无法携带自定义头，
改用短时签名（?exp=&sig=，见 stream_sign.py）——不落裸秘钥到 URL/日志，
签名泄露仅能在时效窗口内拉当条流；能带头的客户端仍可用 X-Server-Key。
"""

from __future__ import annotations

import hmac

from starlette.requests import Request
from starlette.responses import JSONResponse

from app.http.stream_sign import verify_stream_sign


def _safe_equal(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())


async def auth_gate(request: Request, call_next):
    if request.method == "OPTIONS":
        return await call_next(request)

    auth_key = getattr(request.app.state, "auth_key", "") or ""
    if not auth_key:  # 未配置秘钥：完全不鉴权
        return await call_next(request)

    pathname = request.url.path
    header = request.headers.get("x-server-key", "")

    if pathname.startswith("/stream/"):
        query = request.query_params
        ok = (header and _safe_equal(header, auth_key)) or (
            not header and verify_stream_sign(auth_key, pathname, query.get("exp"), query.get("sig"))
        )
        if ok:
            return await call_next(request)
        return JSONResponse(
            {"code": 401, "msg": "无效或缺少调用秘钥：X-Server-Key 头，或 /url 返回流地址自带的 exp/sig 签名"},
            status_code=401,
        )

    if not header or not _safe_equal(header, auth_key):
        # 统一 401 文案（不区分路径存在与否，防扫描探测）
        return JSONResponse(
            {"code": 401, "msg": "无效或缺少调用秘钥：请在 X-Server-Key 请求头携带"},
            status_code=401,
        )
    return await call_next(request)
