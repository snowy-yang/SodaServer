"""汽水 PC 通道签名（X-Helios / X-Medusa）—— 三种供给方式，按优先级：

  1) 签名服务器（显式）：环境变量 QS_SIGN_SERVER 指向签名服务（如 http://127.0.0.1:16889，
     可选 QS_SIGN_TOKEN 配 Bearer 鉴权），HTTP POST /sign。
     契约兼容独立仓库 qs-signer（gitea.4i.hk/hhhge/qs-signer，与 SodaServer 同级）
     的 mssdk-sign 服务（请求 {url, headers, body?}，响应 {ok, headers, added?}）。
  2) 签名服务器（自动探测）：QS_SIGN_SERVER 未配置时，进程启动探测本机 16689 端口——
     GET /healthz 应答 ok:true 且 service:"mssdk-sign" 才认定为汽水签名服务并自动接入
     （16689 上是别的服务则忽略，端口没人听静默跳过，均回落到 3）。
  3) 固定值兜底：无签名服务器时，两个加密头直接用抓包固定值 ——
     环境变量 QS_HELIOS / QS_MEDUSA，或 data/qs-sign.json：
       { "helios": "…", "medusa": "…", "device_id": "…", "install_id": "…" }
     签名与设备/URL 绑定：固定值必须搭配抓包时的同一套 device_id/install_id
     （json 里带上即自动对齐 URL 参数），时效自负。
  两者都没有：PC 签名不可用 —— 搜索/取流走 track_v2 的通道直接报错（匿名试听不受影响）。

官方只对 qishui.com 且 **非** /passport/、非 /ttwid/ 的请求签名 —— 登录链路用不到本模块。
签名覆盖 URL + 全部请求头；POST 的 x-ss-stub（body 的 MD5 大写）既是签名输入也要随请求发出。
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any

from app.config import ROOT
from app.qs.lib.http import http_fetch
from app.qs.passport import get_device_ids

FIXED_SIGN_FILE = ROOT / "data" / "qs-sign.json"
SIGN_TIMEOUT_MS = 5000

# —— 端口探测：QS_SIGN_SERVER 未配置时，启动探测本机 16689 是不是汽水签名服务 ——
PROBE_BASE = "http://127.0.0.1:16689"
PROBE_TIMEOUT_MS = 2000

# 探测状态：_PENDING=探测未完成；None=探测过、不是签名服务；字符串=探测到的签名服务地址
_PENDING = object()
_probed_server: str | object | None = _PENDING
_probe_lock = asyncio.Lock()


def _env(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def probe_pending() -> bool:
    """探测是否尚未完成（供启动器决定是否需要发起探测）。"""
    return _probed_server is _PENDING


def _is_sign_service(json: Any) -> bool:
    """healthz 应答 ok:true 且 service:"mssdk-sign" 才认——16689 上可能是无关服务。"""
    return isinstance(json, dict) and json.get("ok") is True and json.get("service") == "mssdk-sign"


async def _probe_once() -> None:
    global _probed_server
    try:
        res = await http_fetch(f"{PROBE_BASE}/healthz", method="get", timeout=PROBE_TIMEOUT_MS)
        json_body = res.body if isinstance(res.body, dict) else None
        if res.status_code == 200 and _is_sign_service(json_body):
            _probed_server = PROBE_BASE
            version = json_body.get("version", "?") if json_body else "?"
            dll = json_body.get("dllVersion", "?") if json_body else "?"
            print(f"[qs/mssdk] 探测到本机 16689 是汽水签名服务（mssdk-sign v{version}，dll {dll}），自动接入")
        else:
            _probed_server = None
            service = json_body.get("service") if json_body else "非 JSON"
            print(f"[qs/mssdk] 本机 16689 有服务但不是汽水签名服务（healthz service={service}），忽略")
    except Exception:  # noqa: BLE001 - 端口没人监听是常态，静默
        _probed_server = None


async def ensure_probe() -> None:
    """确保探测完成（进程内只探一次）；显式配置了 QS_SIGN_SERVER 则永不探测。"""
    if _probed_server is not _PENDING or _env("QS_SIGN_SERVER"):
        return
    async with _probe_lock:
        if _probed_server is not _PENDING or _env("QS_SIGN_SERVER"):
            return
        await _probe_once()


# 固定值配置（helios/medusa 必填；device_id/install_id 应与抓包时同一套）
class FixedSignConfig:
    __slots__ = ("device_id", "helios", "install_id", "medusa")

    def __init__(self, helios: str, medusa: str, device_id: str, install_id: str) -> None:
        self.helios = helios
        self.medusa = medusa
        self.device_id = device_id
        self.install_id = install_id


_fixed_config_warned = False


def _read_fixed_config() -> FixedSignConfig | None:
    """读固定值：环境变量 QS_HELIOS/QS_MEDUSA 优先，其次 data/qs-sign.json；没有返回 None。"""
    global _fixed_config_warned
    env_helios, env_medusa = _env("QS_HELIOS"), _env("QS_MEDUSA")
    if env_helios and env_medusa:
        return FixedSignConfig(env_helios, env_medusa, _env("QS_SIGN_DEVICE_ID"), _env("QS_SIGN_INSTALL_ID"))
    if not FIXED_SIGN_FILE.exists():
        return None
    try:
        data = json.loads(FIXED_SIGN_FILE.read_text(encoding="utf-8"))
        helios = str(data.get("helios") or "").strip()
        medusa = str(data.get("medusa") or "").strip()
        if helios and medusa:
            return FixedSignConfig(
                helios,
                medusa,
                str(data.get("device_id") or "").strip(),
                str(data.get("install_id") or "").strip(),
            )
        if not _fixed_config_warned:
            _fixed_config_warned = True
            print(f"[qs/mssdk] {FIXED_SIGN_FILE} 缺少 helios/medusa 字段，固定值模式不启用")
    except (ValueError, OSError) as err:
        if not _fixed_config_warned:
            _fixed_config_warned = True
            print(f"[qs/mssdk] 固定值配置解析失败: {err}")
    return None


class SignMode:
    """当前签名供给方式：kind='server'（server/token）或 kind='fixed'（fixed）。"""

    __slots__ = ("fixed", "kind", "server", "token")

    def __init__(
        self,
        kind: str,
        server: str = "",
        token: str = "",
        fixed: FixedSignConfig | None = None,
    ) -> None:
        self.kind = kind
        self.server = server
        self.token = token
        self.fixed = fixed


def _load_sign_mode() -> SignMode | None:
    """显式服务器 > 探测接入的服务器 > 固定值 > 不可用（None）。"""
    server = _env("QS_SIGN_SERVER").rstrip("/")
    if server:
        return SignMode("server", server=server, token=_env("QS_SIGN_TOKEN"))
    if isinstance(_probed_server, str):
        # 探测接入；签名服务开了鉴权时同样吃 QS_SIGN_TOKEN
        return SignMode("server", server=_probed_server, token=_env("QS_SIGN_TOKEN"))
    fixed = _read_fixed_config()
    return SignMode("fixed", fixed=fixed) if fixed else None


def mssdk_available() -> bool:
    """PC 签名是否可用。

    只看配置与探测结果（服务器宕机等运行期失败由签名调用返回 None、调用方降级处理）；
    探测未完成时先放行——紧随其后的 sign_qishui_request 会等探测结果再定供给。
    """
    return _load_sign_mode() is not None or _probed_server is _PENDING


async def _sign_via_server(
    server: str,
    token: str,
    url: str,
    headers: dict[str, str],
    body: str | None = None,
) -> dict[str, str] | None:
    try:
        request_headers = {"content-type": "application/json"}
        if token:
            request_headers["authorization"] = f"Bearer {token}"
        res = await http_fetch(
            f"{server}/sign",
            method="post",
            headers=request_headers,
            body=json.dumps({"url": url, "headers": headers, "body": body}, ensure_ascii=False),
            timeout=SIGN_TIMEOUT_MS,
        )
        json_body = res.body if isinstance(res.body, dict) else None
        if res.status_code != 200 or not json_body or not json_body.get("ok"):
            error = json_body.get("error", "") if json_body else ""
            print(f"[qs/mssdk] 签名服务返回异常: HTTP {res.status_code} {error}".rstrip())
            return None
        # signer/server.mjs 把 x-ss-stub 并进 headers，mssdk-sign 放 added —— 合并即可
        signed = {**(json_body.get("headers") or {}), **(json_body.get("added") or {})}
        if "X-Helios" not in signed and "x-helios" not in signed:
            print("[qs/mssdk] 签名结果缺少 X-Helios")
            return None
        return {str(k): str(v) for k, v in signed.items()}
    except Exception as err:  # noqa: BLE001
        print(f"[qs/mssdk] 签名服务不可达: {err}")
        return None


async def sign_qishui_request(
    url: str,
    headers: dict[str, str],
    body: str | None = None,
) -> dict[str, str] | None:
    """给一个 qishui.com 请求签名。

    headers 必须是「真实要发出去的那一份」（含 cookie / x-ss-stub）；
    body 为 POST body（给了且 headers 没带 x-ss-stub 时服务端会自动补）。
    返回需追加的头；不可用时返回 None（调用方自行降级处理）。
    """
    await ensure_probe()  # 探测未完成时先等它出结果，再决定供给方式
    mode = _load_sign_mode()
    if mode is None:
        return None
    if mode.kind == "fixed" and mode.fixed is not None:
        return {"X-Helios": mode.fixed.helios, "X-Medusa": mode.fixed.medusa}
    return await _sign_via_server(mode.server, mode.token, url, headers, body)


# 官方 PC 端 UA（签名与 UA 绑定同一客户端版本）
PC_UA = "LunaPC/3.7.0(452316191)"


def pc_common_params() -> dict[str, str]:
    """官方 PC 端公共查询参数（与签名设备成套；track_v2 / search 等 luna_pc 接口共用）。"""
    mode = _load_sign_mode()
    # 固定值是针对抓包设备签的，必须用配套设备；服务器现签则用本地设备（URL 带什么签什么）
    fixed = (
        mode.fixed
        if (mode is not None and mode.kind == "fixed" and mode.fixed is not None and mode.fixed.device_id)
        else None
    )
    if fixed:
        device_id, install_id = fixed.device_id, fixed.install_id
    else:
        ids = get_device_ids()
        device_id, install_id = ids.device_id, ids.install_id
    return {
        "aid": "386088",
        "app_name": "luna_pc",
        "region": "cn",
        "geo_region": "cn",
        "os_region": "cn",
        "sim_region": "",
        "device_id": device_id,
        "cdid": "",
        "iid": install_id,
        "version_name": "3.7.0",
        "version_code": "30070000",
        "channel": "official",
        "build_mode": "master",
        "network_carrier": "",
        "ac": "wifi",
        "tz_name": "Asia/Shanghai",
        "resolution": "",
        "device_platform": "windows",
        "device_type": "Windows",
        "os_version": "Windows 11",
        "fp": device_id,
    }
