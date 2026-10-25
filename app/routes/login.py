"""登录路由 —— 汽水音乐扫码 / 短信验证码 / sessionid 绑定；凭据持久化于 data/auth.json（qs 键）。

成功返回 `ok(data)`（{ code: 0, data }）；可预期失败抛 ApiError（passport 层自带状态码），
其它异常按 502 上报（与原实现 `fail(reply, err)` 的 `err.status ?? 502` 一致）。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body

from app.http.api_error import ApiError, upstream
from app.http.envelope import ok
from app.qs import passport
from app.qs.lib.store import clear_credentials

router = APIRouter()


async def _call(fn: Any, *args: Any, **kwargs: Any) -> Any:
    """调用登录实现并包成信封；ApiError 原样抛（保留状态码），其余转 502。"""
    try:
        return ok(await fn(*args, **kwargs))
    except ApiError:
        raise
    except Exception as err:
        raise upstream(str(err)) from err


@router.post(
    "/login/qs/qr",
    summary="获取汽水音乐扫码登录二维码",
    description="用「汽水音乐 APP」扫一扫（官方 PC 客户端同款文案：打开汽水音乐 APP → 搜索框右侧扫一扫；"
    "抖音 APP 扫可能不完成确认）后轮询 /login/qs/qr/check 完成登录。"
    "返回 qrimg（data URL）可直接展示，token 需原样回传；二维码内容为官方 PC 客户端同款扫码页"
    "（bff-pc.qishui.com/light/invoke/scan_login，另见 scanUrl 字段）。轮询间隔建议 ≥2 秒。"
    "登录后 qs 取流走签名 track_v2，解锁完整曲目。",
    tags=["登录"],
)
async def qr_create() -> Any:
    return await _call(passport.create_qr)


@router.post(
    "/login/qs/qr/check",
    summary="轮询汽水音乐扫码状态",
    description="code：800 二维码过期/被拒 / 801 等待扫码 / 802 已扫码待确认（含需二次验证）/ 803 登录成功（自动保存凭据）。",
    tags=["登录"],
)
async def qr_check(token: str = Body(..., embed=True, description="createQr 返回的 token")) -> Any:
    return await _call(passport.check_qr, token)


@router.post(
    "/login/qs/sms",
    summary="发送汽水音乐登录短信验证码",
    description="手机号验证码登录第一步（官方 PC 客户端同款 /passport/web/send_code/，type=24）。"
    'mobile 支持 "13800138000" 或自带区号 "86 13800138000"；海外号码用 cc 参数指定区号。'
    "发送成功后用 /login/qs/sms/login 提交验证码完成登录。",
    tags=["登录"],
)
async def sms_send(
    mobile: str = Body(..., embed=True, description="手机号"),
    cc: str | None = Body(None, description="区号（省略时为 86）"),
) -> Any:
    return await _call(passport.send_sms_code, mobile, cc)


@router.post(
    "/login/qs/sms/login",
    summary="汽水音乐短信验证码登录",
    description="提交 /login/qs/sms 发送的验证码完成登录，成功自动保存凭据并返回 803。"
    "失败经 msg 返回官方文案（1200-1300 为验证码/手机号错误；2046 需去汽水 App 完成安全验证；1075 账号注销中）。",
    tags=["登录"],
)
async def sms_login(
    mobile: str = Body(..., embed=True, description="手机号"),
    code: str = Body(..., embed=True, description="短信验证码"),
    cc: str | None = Body(None, description="区号（省略时为 86）"),
) -> Any:
    return await _call(passport.sms_login, mobile, code, cc)


@router.post(
    "/login/qs",
    summary="绑定汽水音乐 sessionid",
    description="sessionid 从汽水 App / 官方 PC 客户端抓包获取（登录态 Cookie 的 sessionid 字段），"
    "也可传完整 cookie 串（须含 sessionid= 字段，整体保存）。无上游校验接口，不做有效性验证，"
    "失效会在取流时报错。推荐优先用 /login/qs/qr 扫码或 /login/qs/sms 短信登录。",
    tags=["登录"],
)
async def session_bind(
    sessionid: str | None = Body(None, description="裸 sessionid 值"),
    cookie: str | None = Body(None, description="完整 cookie 串（须含 sessionid= 字段）"),
) -> Any:
    return await _call(passport.login_by_session_id, sessionid or cookie)


@router.delete("/login/qs", summary="退出汽水音乐登录", tags=["登录"])
async def qs_logout() -> Any:
    clear_credentials("qs")
    return ok({"logged": False})


@router.get(
    "/login/status",
    summary="查看登录状态",
    description="qs 为已存凭据（含最近更新时间）。登录后取流走签名 track_v2 解锁完整曲目。",
    tags=["登录"],
)
async def login_status() -> Any:
    return ok({"qs": passport.status()})
