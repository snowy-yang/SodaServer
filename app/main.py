"""FastAPI 应用装配 —— 中间件、异常处理、OpenAPI 文档、路由注册。

与原 Node 版（Fastify）语义对齐：
  * 成功响应统一包成 { code: 0, data }（由路由处理函数调用 `ok()` 显式构造，
    文档页、OpenAPI 规范与 /stream 媒体流为 raw 响应）
  * 业务错误 { code: <HTTP 状态码>, msg, detail? }
  * 422 参数校验失败、404 路由不存在、405 方法不允许（带 Allow 头）、502 上游失败
  * 全部响应 Cache-Control: no-store
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import __version__, config
from app.http.api_error import ApiError
from app.http.auth_gate import auth_gate
from app.http.rate_limit import make_rate_limiter
from app.openapi.components import COMPONENT_SCHEMAS
from app.openapi.ui import redoc_html
from app.qs.lib.http import close_session

API_TITLE = "SodaServer 汽水音乐 API"
API_DESCRIPTION = (
    "汽水音乐独立服务端：搜索、播放地址（含登录态解密完整曲目）、歌词、封面、曲目信息与账号登录。"
    "搜索与登录态取流依赖 PC 签名（QS_SIGN_SERVER 签名服务或 data/qs-sign.json 固定值，见 qs-signer 仓库）。"
)


@asynccontextmanager
async def _lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """启动期副作用 —— PC 签名服务探测（对应 TS 版「启动即探测一次，异步进行不阻塞导入」）。"""
    start_probe()
    try:
        yield
    finally:
        await close_session()
        await stop_probe()


def create_app() -> FastAPI:
    config.load_env()

    app = FastAPI(
        title=API_TITLE,
        description=API_DESCRIPTION,
        version=__version__,
        lifespan=_lifespan,
        openapi_url="/openapi.json",
        docs_url="/docs",
        redoc_url=None,
        openapi_tags=[
            {"name": "搜索", "description": "歌曲搜索"},
            {"name": "歌曲资源", "description": "播放地址、歌词、封面、曲目信息"},
            {"name": "登录", "description": "账号登录（汽水 App 扫码 / 短信验证码 / sessionid 绑定）与状态"},
            {"name": "系统", "description": "健康检查、平台信息、文档"},
        ],
    )
    app.state.started_at = time.time()
    app.state.auth_key = config.auth_key()

    # ---- 中间件顺序（后注册者在外层）：CORS → no-store → 限流 → 鉴权 ----
    # 限流必须在鉴权之外：错误秘钥的 401 也要计数，才能真正防爆破。
    app.middleware("http")(auth_gate)
    app.middleware("http")(make_rate_limiter(config.rate_limit()))

    @app.middleware("http")
    async def _no_store(request: Request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        return response

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
        max_age=600,
    )

    # ---- 异常处理 ----
    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError):
        payload: dict[str, Any] = {"code": exc.status, "msg": str(exc)}
        if exc.detail is not None:
            payload["detail"] = exc.detail
        return JSONResponse(payload, status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError):
        errors = json.loads(json.dumps(exc.errors(), default=str))
        msg = "参数校验失败"
        if errors:
            first = errors[0]
            loc = ".".join(str(part) for part in first.get("loc", []) if part not in ("body", "query", "path"))
            msg = f"{loc}: {first.get('msg', msg)}" if loc else str(first.get("msg", msg))
        return JSONResponse({"code": 422, "msg": msg, "detail": errors}, status_code=422)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException):
        if exc.status_code == 404:
            allowed = _allowed_methods(request)
            if allowed:
                return JSONResponse(
                    {"code": 405, "msg": f"方法 {request.method} 不允许，请使用 {', '.join(allowed)}"},
                    status_code=405,
                    headers={"Allow": ", ".join(allowed)},
                )
            return JSONResponse({"code": 404, "msg": f"接口不存在: {request.url.path}"}, status_code=404)
        # Starlette 对方法不允许会抛 405 并在 exc.headers 带 Allow 列表（响应头必须显式透传）
        headers = dict(exc.headers or {})
        if exc.status_code == 405 and headers.get("Allow"):
            return JSONResponse(
                {"code": 405, "msg": f"方法 {request.method} 不允许，请使用 {headers['Allow']}"},
                status_code=405,
                headers=headers,
            )
        detail = exc.detail if isinstance(exc.detail, str) else "请求失败"
        return JSONResponse({"code": exc.status_code, "msg": detail}, status_code=exc.status_code, headers=headers)

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):
        print(f"[{time.strftime('%Y-%m-%dT%H:%M:%S')}] {request.method} {request.url.path} 内部错误: {exc!r}")
        return JSONResponse({"code": 500, "msg": "服务器内部错误"}, status_code=500)

    # ---- 文档与入口 ----
    @app.get("/redoc", include_in_schema=False)
    async def redoc():
        return HTMLResponse(redoc_html(API_TITLE))

    @app.get("/", include_in_schema=False)
    async def root():
        return RedirectResponse("/docs", status_code=302)

    # ---- 业务路由 ----
    from app.routes.login import router as login_router
    from app.routes.music import router as music_router
    from app.routes.system import router as system_router

    app.include_router(music_router)
    app.include_router(login_router)
    app.include_router(system_router)

    _install_openapi(app)
    return app


def _allowed_methods(request: Request) -> list[str]:
    """路径存在但方法不允许 → 405 的 Allow 列表（OPTIONS/HEAD 不计入）。"""
    path = request.url.path
    methods: list[str] = []
    for route in request.app.routes:
        pattern = getattr(route, "path", "")
        if not pattern or not _match_path(pattern, path):
            continue
        for method in getattr(route, "methods", None) or []:
            if method not in {"OPTIONS", "HEAD"} and method not in methods:
                methods.append(method)
    return methods


def _match_path(pattern: str, path: str) -> bool:
    """把 `/url/qs/{id}` 这类路径模板转成正则做匹配。"""
    regex = re.sub(r"\{[^}]+\}", "\u0000", pattern)
    regex = re.escape(regex).replace("\u0000", "[^/]+")
    return re.fullmatch(regex, path) is not None


def _install_openapi(app: FastAPI) -> None:
    """把领域模型挂进 components，并保留 FastAPI 自动生成的路径参数文档。"""
    from fastapi.openapi.utils import get_openapi

    def custom_openapi():
        if app.openapi_schema:
            return app.openapi_schema
        schema = get_openapi(title=API_TITLE, version=__version__, description=API_DESCRIPTION, routes=app.routes)
        components = schema.setdefault("components", {}).setdefault("schemas", {})
        for name, definition in COMPONENT_SCHEMAS.items():
            components.setdefault(name, definition)
        app.openapi_schema = schema
        return schema

    app.openapi = custom_openapi  # type: ignore[method-assign]


_probe_task: asyncio.Task[None] | None = None


def start_probe() -> None:
    """启动签名服务探测（幂等；进程内只探一次，见 app.qs.mssdk）。"""
    global _probe_task
    from app.qs.mssdk import probe_pending

    if _probe_task is None or _probe_task.done():
        if probe_pending():
            _probe_task = asyncio.create_task(_run_probe())


async def _run_probe() -> None:
    from app.qs.mssdk import ensure_probe

    try:
        await ensure_probe()
    except Exception as err:  # noqa: BLE001 - 探测失败等价于「无签名服务」，不影响启动
        print(f"[qs/mssdk] 签名服务探测失败: {err}")


async def stop_probe() -> None:
    global _probe_task
    if _probe_task is not None and not _probe_task.done():
        _probe_task.cancel()
        try:
            await _probe_task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
    _probe_task = None


app = create_app()
