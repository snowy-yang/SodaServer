"""网络请求层。

对应原 qs/lib/http.ts（自 MusicServer sdk/common/request.ts 移植），把 Node 的
`{ promise, cancelHttp }` 句柄收敛为原生 `async def` —— 取消语义由调用方用
`asyncio.wait_for` / task 取消表达。

保持与原文一致的行为细节：
  * 不跟随重定向（Node http 默认行为；需要跟随的调用方自行处理 Location）
  * 不保留 cookie（每次请求独立；签名与登录链路自己管理 Cookie 头，依赖这一点）
  * body：dict/list 自动 JSON 序列化，str/bytes 原样发送；form 走 urlencoded
  * 未显式给 content-type 时按 body 形式补 application/json / x-www-form-urlencoded
  * 响应体：无论 content-type 都先尝试 JSON 解析，失败返回文本
  * 64MB 响应上限；gzip/deflate/br 自动解压
  * 错误文案与原文一致（请求超时 / 无法连接到服务器 / 请求失败）
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import aiohttp

MAX_BUFFER_SIZE = 64 * 1024 * 1024

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/69.0.3497.100 Safari/537.36"
    ),
}

MSG_TIMEOUT = "请求超时"
MSG_NOT_CONNECT = "无法连接到服务器"
MSG_FAIL = "请求失败"


@dataclass
class HttpResponse:
    status_code: int
    headers: dict[str, str] = field(default_factory=dict)
    body: Any = None
    raw: bytes = b""
    #: 原始多值 Set-Cookie（headers 折叠同名头后只留最后一个，登录链路必须按数组读）
    set_cookies: list[str] = field(default_factory=list)

    @property
    def statusCode(self) -> int:  # 兼容原文命名
        return self.status_code

    def header(self, name: str, default: str = "") -> str:
        return self.headers.get(name.lower(), default)


_session: aiohttp.ClientSession | None = None
_session_loop: asyncio.AbstractEventLoop | None = None


def _get_session() -> aiohttp.ClientSession:
    """进程级连接池。禁用 cookie jar 与自动重定向，语义对齐 Node http。

    trust_env=False：Node 的 http/https 不读系统代理，而 aiohttp 在 Windows 上会通过
    注册表拿到「Internet 选项」里的代理。走该代理会让出口 IP/风控画像与原版不一致，
    因此这里显式直连，与 Node 行为保持一致。
    """
    global _session, _session_loop
    loop = asyncio.get_running_loop()
    if _session is None or _session.closed or _session_loop is not loop:
        _session = aiohttp.ClientSession(
            cookie_jar=aiohttp.DummyCookieJar(),
            auto_decompress=True,
            trust_env=False,
            connector=aiohttp.TCPConnector(limit=200, ttl_dns_cache=300),
        )
        _session_loop = loop
    return _session


async def close_session() -> None:
    global _session
    if _session is not None and not _session.closed:
        await _session.close()
    _session = None


def _parse_body(raw: bytes) -> Any:
    if not raw:
        return None
    text = raw.decode("utf-8", errors="replace")
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return text


def _build_body(options: Mapping[str, Any]) -> tuple[bytes | None, bool]:
    form = options.get("form")
    if form:
        from urllib.parse import urlencode

        pairs: list[tuple[str, str]] = []
        for key, value in form.items():
            if value is None:
                continue
            # 对象值对齐 URLSearchParams 的 JSON.stringify（紧凑、无空格）
            pairs.append(
                (
                    key,
                    json.dumps(value, ensure_ascii=False, separators=(",", ":"))
                    if isinstance(value, (dict, list))
                    else str(value),
                )
            )
        return urlencode(pairs).encode(), True
    body = options.get("body")
    if body is None:
        return None, False
    if isinstance(body, bytes):
        return body, False
    if isinstance(body, str):
        return body.encode(), False
    # 必须与 JS 的 JSON.stringify 逐字节一致（紧凑分隔符）——上游签名对 body 内容敏感
    return json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode(), False


def _flatten_headers(headers: Mapping[str, Any]) -> dict[str, str]:
    return {str(k): str(v) for k, v in headers.items()}


async def http_fetch(url: str, **options: Any) -> HttpResponse:
    """发起 HTTP 请求并返回完整响应。

    options: method / headers / body / form / timeout(毫秒) / lookup / family
    """
    method = str(options.get("method") or "GET").upper()
    payload, is_form = _build_body(options)
    headers = {**DEFAULT_HEADERS, **_flatten_headers(options.get("headers") or {})}

    lowered = {k.lower() for k in headers}
    if payload is not None:
        if "content-type" not in lowered:
            headers["content-type"] = "application/x-www-form-urlencoded" if is_form else "application/json"
        if "content-length" not in lowered:
            headers["content-length"] = str(len(payload))

    timeout_ms = options.get("timeout")
    timeout = aiohttp.ClientTimeout(total=(timeout_ms / 1000) if timeout_ms else 60)

    session = _get_session()
    try:
        async with session.request(
            method,
            url,
            headers=headers,
            data=payload,
            timeout=timeout,
            allow_redirects=False,
        ) as resp:
            chunks: list[bytes] = []
            size = 0
            async for chunk in resp.content.iter_chunked(64 * 1024):
                size += len(chunk)
                if size > MAX_BUFFER_SIZE:
                    raise RuntimeError("Response too large")
                chunks.append(chunk)
            raw = b"".join(chunks)
            return HttpResponse(
                status_code=resp.status,
                headers={k.lower(): v for k, v in resp.headers.items()},
                body=_parse_body(raw),
                raw=raw,
                set_cookies=list(resp.headers.getall("set-cookie", [])),
            )
    except TimeoutError as err:
        raise TimeoutError(MSG_TIMEOUT) from err
    except aiohttp.ClientConnectorDNSError as err:
        raise ConnectionError(MSG_NOT_CONNECT) from err
    except aiohttp.ClientConnectionError as err:
        if isinstance(err, aiohttp.ServerDisconnectedError):
            raise ConnectionError(MSG_FAIL) from err
        raise ConnectionError(MSG_NOT_CONNECT) from err
    except aiohttp.ClientError as err:
        raise RuntimeError(MSG_FAIL) from err
