"""汽水音乐扫码登录 —— 复刻官方 PC 客户端（SodaMusic）的扫码链路。

接口:
  GET  https://api.qishui.com/passport/web/get_qrcode/       → token + 初始化 Cookie
  GET  二维码内容不是响应里的 qrcode 图，而是用 qrcode_index_url 的 token 重建的官方扫码页：
       https://bff-pc.qishui.com/light/invoke/scan_login?token=<token>&os=Windows&computer_name=<主机名>
       （官方 renderer getHandleScanCodeH5Img 即此算法；响应自带的 qrcode 图指向旧页
        ucenter_web/app/sdk-next，该页已 404，抖音扫码会提示「无法访问页面」）
  POST https://api.qishui.com/passport/web/check_qrconnect/  → 轮询状态，确认后 Set-Cookie 带 sessionid

状态映射沿用本仓库约定：800 过期/拒绝 / 801 等待扫码 / 802 已扫码待确认 / 803 登录成功（自动保存）
"""

from __future__ import annotations

import re
import socket
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

from app.http.api_error import ApiError
from app.qs.lib import qrcode
from app.qs.lib.http import http_fetch
from app.qs.lib.store import get_credentials, set_credentials

PASSPORT_HOST = "https://api.qishui.com"
SCAN_LOGIN_URL = "https://bff-pc.qishui.com/light/invoke/scan_login"
WEB_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"

PASSPORT_FIXED = {
    "aid": "386088",
    "passport_jssdk_version": "2.4.13",
    "passport_jssdk_type": "normal",
    "is_from_ttaccountsdk": "1",
    "next": "https://api.qishui.com",
    "need_logo": "false",
    "need_short_url": "false",
    "is_frontier": "true",
    "is_new_login": "1",
    "version_code": "30020100",
}


@dataclass
class DeviceIds:
    device_id: str
    install_id: str


def get_device_ids() -> DeviceIds:
    """PC 设备身份（同步读，首次懒生成并持久化）。

    passport 登录与 PC 签名公共参数共用，持久化于 data/auth.json 的 `_qs_passport_device`，
    保证同一实例「同一设备」。
    """
    existing = get_credentials("_qs_passport_device") or {}
    if existing.get("deviceId") and existing.get("installId"):
        return DeviceIds(str(existing["deviceId"]), str(existing["installId"]))
    now = int(time.time() * 1000)
    ids = DeviceIds(str(now), str(now + 1))
    set_credentials("_qs_passport_device", {"deviceId": ids.device_id, "installId": ids.install_id})
    return ids


def _common_params() -> dict[str, str]:
    """各 passport 请求共用的公共参数（设备身份 + 平台/版本）。"""
    ids = get_device_ids()
    return {
        "passport_jssdk_version": PASSPORT_FIXED["passport_jssdk_version"],
        "passport_jssdk_type": PASSPORT_FIXED["passport_jssdk_type"],
        "is_from_ttaccountsdk": PASSPORT_FIXED["is_from_ttaccountsdk"],
        "aid": PASSPORT_FIXED["aid"],
        "language": "zh",
        "is_new_login": PASSPORT_FIXED["is_new_login"],
        "device_id": ids.device_id,
        "install_id": ids.install_id,
        "did": ids.device_id,
        "iid": ids.install_id,
        "device_platform": "PC",
        "version_code": PASSPORT_FIXED["version_code"],
    }


def _web_headers(cookie: str = "") -> dict[str, str]:
    headers = {"User-Agent": WEB_UA}
    if cookie:
        headers["Cookie"] = cookie
    return headers


def _cookie_pairs_from(set_cookies: list[str]) -> list[str]:
    return [str(c).split(";")[0].strip() for c in set_cookies if str(c).split(";")[0].strip()]


def _cookie_map_from(set_cookies: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for pair in _cookie_pairs_from(set_cookies):
        idx = pair.find("=")
        if idx > 0:
            out[pair[:idx].strip()] = pair[idx + 1 :].strip()
    return out


def _merge_cookies(current: str, fresh: dict[str, str]) -> str:
    for pair in current.split(";"):
        idx = pair.find("=")
        if idx > 0:
            name = pair[:idx].strip()
            if name:
                fresh[name] = fresh.get(name) or pair[idx + 1 :].strip()
    return "; ".join(f"{k}={v}" for k, v in fresh.items())


# 登录态 cookie 名（passport 确认后下发；sessionid 为必需项）
SESSION_COOKIE_NAMES = ["sessionid", "sessionid_ss", "sid_guard", "sid_tt"]


def session_id_of(cookie: str) -> str:
    match = re.search(r"(?:^|;\s*)sessionid=([^;]+)", cookie or "")
    return match.group(1) if match else ""


def _has_session_cookie(cookie: str) -> bool:
    return any(re.search(rf"(?:^|;\s*){name}=", cookie) for name in SESSION_COOKIE_NAMES)


# get_qrcode 的 Set-Cookie 需在轮询时原样带上（内存暂存，随二维码生命周期）
_qr_cookies: dict[str, str] = {}


def build_scan_url(index_url: str, computer_name: str | None = None) -> str:
    """官方扫码页地址：响应 qrcode_index_url 里的 token + PC 标识（官方 renderer 同款算法）。"""
    try:
        token = (parse_qs(urlparse(str(index_url)).query).get("token") or [""])[0]
    except ValueError:
        token = ""
    if not token:
        raise ApiError(502, f"二维码地址缺少 token: {str(index_url)[:80]}")
    host = (computer_name or socket.gethostname() or "Windows-PC").strip() or "Windows-PC"
    query = urlencode({"token": token, "os": "Windows", "computer_name": host})
    # 抖音/客户端把 + 解析成空格，官方实现同样显式替换
    return f"{SCAN_LOGIN_URL}?{query}".replace("+", "%20")


async def create_qr() -> dict[str, Any]:
    """创建扫码登录二维码（本地渲染官方扫码页，响应自带的 qrcode 图已失效不可用）。"""
    query = urlencode(
        {
            **_common_params(),
            "next": PASSPORT_FIXED["next"],
            "need_logo": PASSPORT_FIXED["need_logo"],
            "need_short_url": PASSPORT_FIXED["need_short_url"],
        }
    )
    res = await http_fetch(
        f"{PASSPORT_HOST}/passport/web/get_qrcode/?{query}", method="get", headers=_web_headers(), timeout=15000
    )
    data = res.body.get("data") or {} if isinstance(res.body, dict) else {}
    if res.status_code != 200 or not data.get("token"):
        import json

        raise ApiError(502, f"获取二维码失败: HTTP {res.status_code} {json.dumps(res.body, ensure_ascii=False)[:120]}")
    scan_url = build_scan_url(data.get("qrcode_index_url") or "")
    qrimg = qrcode.to_data_url(scan_url)
    _qr_cookies[data["token"]] = _merge_cookies("", _cookie_map_from(res.set_cookies))
    return {
        "token": str(data["token"]),
        "qrimg": qrimg,
        "expireTime": int(data.get("expire_time") or 0),
        # 官方 PC 客户端扫码文案要求「打开汽水音乐 APP → 搜索框右侧 扫一扫」（passport 的
        # copywriting 写的是抖音 APP，但扫描目标是 bff-pc 的 scan_login 页，实际须汽水 App）
        "scanApp": "qishui",
        "scanUrl": scan_url,
    }


@dataclass
class QrDecision:
    state: str  # waiting / scanned / confirmed / expired / failed / mfa
    message: str
    status: str
    error_code: int
    rate_limited: bool = False


# 官方状态语义（SodaMusic renderer use-scan-code-login）：
#   1/new 等待扫码；2/scanned 已扫码待确认；3/confirmed 已确认；4/5/refused/expired 过期或被拒
def decide_qr_status(data: dict[str, Any]) -> QrDecision:
    status = str(data.get("status") or "").lower()
    error_code = int(data.get("error_code") or 0)
    account_flow = str(data.get("account_flow") or "").lower()
    description = str(data.get("description") or "")

    if error_code == 2046 or account_flow == "verify":
        return QrDecision(
            "mfa", "需要手机端二次验证（本服务无法自动完成，请在汽水 App 内完成验证后重试）", status, error_code
        )
    if error_code == 7:
        return QrDecision("waiting", "接口限流，请降低轮询频率", status, error_code, rate_limited=True)
    if status in ("3", "confirmed"):
        return QrDecision("confirmed", "已确认，等待登录态下发", status, error_code)
    if status in ("2", "scanned"):
        return QrDecision("scanned", "已扫码，待确认", status, error_code)
    if (
        status in ("4", "5", "refused", "expired")
        or error_code == 4
        or re.search(r"expire|过期|失效", description, re.I)
    ):
        refused = status == "refused" or re.search(r"refus|拒绝", description, re.I)
        return QrDecision("expired", "已在手机端拒绝登录" if refused else "二维码已失效", status, error_code)
    if status in ("error", "failed") or (error_code != 0 and status not in ("new", "1", "")):
        return QrDecision("failed", description or f"扫码失败 (code={error_code})", status, error_code)
    return QrDecision("waiting", "等待扫码", status, error_code)


async def check_qr(token: str) -> dict[str, Any]:
    """轮询扫码状态（确认后可能还需再轮询一次才下发登录态）。"""
    query = urlencode(_common_params())
    form = urlencode(
        {
            "need_logo": PASSPORT_FIXED["need_logo"],
            "need_short_url": PASSPORT_FIXED["need_short_url"],
            "is_frontier": PASSPORT_FIXED["is_frontier"],
            "token": token,
            "is_new_login": PASSPORT_FIXED["is_new_login"],
            "next": PASSPORT_FIXED["next"],
        }
    )
    res = await http_fetch(
        f"{PASSPORT_HOST}/passport/web/check_qrconnect/?{query}",
        method="post",
        headers={"Content-Type": "application/x-www-form-urlencoded", **_web_headers(_qr_cookies.get(token, ""))},
        body=form,
        timeout=15000,
    )
    if res.status_code != 200:
        raise ApiError(502, f"轮询扫码状态失败: HTTP {res.status_code}")
    data = res.body.get("data") or {} if isinstance(res.body, dict) else {}
    decision = decide_qr_status(data)

    # 采集本次轮询下发的 Set-Cookie（确认后登录态就在这里）
    cookie = _merge_cookies(_qr_cookies.get(token, ""), _cookie_map_from(res.set_cookies))

    # 确认后若响应体带 redirect_url（浏览器里跳这一跳才落 sessionid），服务端补跳一次收 cookie
    if decision.state == "confirmed" and not _has_session_cookie(cookie):
        redirect_url = str(data.get("redirect_url") or "")
        if redirect_url:
            try:
                hop = await http_fetch(redirect_url, method="get", headers=_web_headers(cookie), timeout=15000)
                cookie = _merge_cookies(cookie, _cookie_map_from(hop.set_cookies))
            except Exception:  # noqa: BLE001 - 补跳失败不致命：下一轮轮询仍可能下发登录态
                pass

    sessionid = session_id_of(cookie)
    if sessionid and _has_session_cookie(cookie):
        set_credentials("qs", {"sessionid": sessionid, "cookie": cookie})
        _qr_cookies.pop(token, None)
        profile = data.get("user_info") or {}
        return {
            "code": 803,
            "message": "登录成功",
            "nickname": profile.get("nickname") or profile.get("name"),
            "status": decision.status,
            "errorCode": decision.error_code,
        }

    _qr_cookies[token] = cookie

    if decision.state == "confirmed":
        return {"code": 802, "message": decision.message, "status": decision.status, "errorCode": decision.error_code}
    if decision.state in ("expired", "failed"):
        _qr_cookies.pop(token, None)
        return {"code": 800, "message": decision.message, "status": decision.status, "errorCode": decision.error_code}
    if decision.state in ("scanned", "mfa"):
        return {"code": 802, "message": decision.message, "status": decision.status, "errorCode": decision.error_code}
    return {"code": 801, "message": decision.message, "status": decision.status, "errorCode": decision.error_code}


def status() -> dict[str, Any]:
    """当前登录态。"""
    cred = get_credentials("qs") or {}
    return {"logged": True, "updatedAt": cred.get("updatedAt")} if cred.get("sessionid") else {"logged": False}


def get_session_id() -> str:
    """登录态 sessionid（未登录返回空串）。"""
    return str((get_credentials("qs") or {}).get("sessionid") or "")


# ---------- 手机验证码登录（接口与官方 PC 客户端 login.asar 的 WebInterfaceSdk 同款）----------
# POST /passport/web/send_code/  { mobile: "86 13800138000", type: 24 }
# POST /passport/web/sms_login/  { mobile, code } → Set-Cookie sessionid


def normalize_mobile(mobile: str, cc: str = "86") -> str:
    """官方 SDK 的 mobile 参数是 "区号 号码" 空格拼接（如 "86 13800138000"）。"""
    s = str(mobile or "").strip()
    with_cc = re.match(r"^(\d{1,3})[\s-]+(\d{4,15})$", s)
    if with_cc:
        return f"{with_cc.group(1)} {with_cc.group(2)}"
    return f"{cc} {re.sub(r'\D', '', s)}"


async def _sms_upstream(path: str, form: dict[str, str], cookie: str = "") -> dict[str, Any]:
    query = urlencode(_common_params())
    res = await http_fetch(
        f"{PASSPORT_HOST}{path}?{query}",
        method="post",
        headers={"Content-Type": "application/x-www-form-urlencoded", **_web_headers(cookie)},
        body=urlencode(form),
        timeout=15000,
    )
    if res.status_code != 200:
        raise ApiError(502, f"汽水接口 HTTP {res.status_code}")
    body = res.body if isinstance(res.body, dict) else {}
    return {"data": body.get("data") or {}, "message": str(body.get("message") or ""), "set_cookies": res.set_cookies}


# send_code 下发的 cookie（验证码会话），sms_login 时带上
_sms_cookies: dict[str, str] = {}


async def send_sms_code(mobile: str, cc: str | None = None) -> dict[str, Any]:
    norm = normalize_mobile(mobile, cc or "86")
    result = await _sms_upstream(
        "/passport/web/send_code/", {"mobile": norm, "type": "24"}
    )  # 官方登录页固定 24（登录注册）；16 为仅注册
    data, message = result["data"], result["message"]
    if message != "success":
        raise ApiError(400, str(data.get("description") or f"验证码发送失败 (code={data.get('error_code')})"))
    _sms_cookies[norm] = _merge_cookies("", _cookie_map_from(result["set_cookies"]))
    return {"sent": True, "mobile": norm}


def _sms_fail_message(data: dict[str, Any]) -> str:
    """官方登录页的失败文案映射（login-7a4b1afd.js catch 分支）。"""
    if data.get("error_code") == 1075:
        return "账号注销中，请前往抖音放弃注销"
    if data.get("error_code") == 2046:
        return "需要二次验证，请在汽水 App 完成安全验证后重试"
    return str(data.get("description") or f"登录失败 (code={data.get('error_code')})")


async def sms_login(mobile: str, code: str, cc: str | None = None) -> dict[str, Any]:
    norm = normalize_mobile(mobile, cc or "86")
    result = await _sms_upstream(
        "/passport/web/sms_login/",
        {"mobile": norm, "code": str(code or "").strip()},
        _sms_cookies.get(norm, ""),
    )
    data, message = result["data"], result["message"]
    cookie = _merge_cookies(_sms_cookies.get(norm, ""), _cookie_map_from(result["set_cookies"]))
    _sms_cookies.pop(norm, None)

    # 成功但 sessionid 未随本响应下发时补跳 redirect_url（与扫码确认同款）
    if message == "success" and not session_id_of(cookie) and data.get("redirect_url"):
        try:
            hop = await http_fetch(str(data["redirect_url"]), method="get", headers=_web_headers(cookie), timeout=15000)
            cookie = _merge_cookies(cookie, _cookie_map_from(hop.set_cookies))
        except Exception:  # noqa: BLE001 - 补跳失败不致命：sessionid 缺失时按失败处理
            pass

    sessionid = session_id_of(cookie)
    if message == "success" and sessionid:
        set_credentials("qs", {"sessionid": sessionid, "cookie": cookie})
        return {"code": 803, "message": "登录成功", "errorCode": 0}
    raise ApiError(400, "登录响应缺少 sessionid" if message == "success" else _sms_fail_message(data))


async def login_by_session_id(input_value: str) -> dict[str, Any]:
    """手动绑定 sessionid（汽水 App / 官方 PC 客户端抓包，或已登录浏览器 Cookie）。

    接受裸 sessionid 值或完整 cookie 串（须含 sessionid= 字段）；完整串优先整体保存，
    sid_guard 等伴随字段有利于延长登录态寿命。passport 无可用的免签校验接口
    （/passport/web/user_info/ 对本 aid 恒报 20016 无权限），不做上游验证，失效会在取流时暴露。
    """
    raw = str(input_value or "").strip()
    sessionid = session_id_of(raw) or (raw if re.fullmatch(r"[\w-]{10,}", raw) else "")
    if not sessionid:
        raise ApiError(422, "需提供 sessionid 值或含 sessionid= 字段的 cookie")
    cookie = raw if "=" in raw else f"sessionid={sessionid}"
    set_credentials("qs", {"sessionid": sessionid, "cookie": cookie})
    return {"logged": True, "sessionid": sessionid}


def get_cookie() -> str:
    """登录态完整 Cookie（未登录返回空串；取流优先用它，sessionid 只是最小集）。"""
    return str((get_credentials("qs") or {}).get("cookie") or "")
