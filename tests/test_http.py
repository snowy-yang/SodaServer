"""HTTP 层回归测试：信封、标准语义、文档、鉴权、限流、流签名、路由补全与登录存储。

测试通过 tests/conftest.py 的临时 DATA_DIR 隔离，不会写真实 data/auth.json。
HTTP 请求用 aiohttp 打到同一个进程内的 uvicorn，避免依赖 Starlette TestClient/httpx。
所有上游依赖（搜索/取流/歌词）均 monkeypatch，不发起真实外网请求。
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import socket
import threading
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import quote

import aiohttp
import app.routes.music as music
import pytest
import uvicorn
from app.main import create_app
from app.qs.stream import StreamEntry


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class _LiveServer:
    """在后台线程运行真实 uvicorn，测试用 aiohttp 访问。"""

    def __init__(self, app: Any) -> None:
        self.port = _free_port()
        config = uvicorn.Config(
            app,
            host="127.0.0.1",
            port=self.port,
            log_level="error",
            access_log=False,
            lifespan="on",
        )
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def start(self) -> None:
        self.thread.start()
        deadline = time.time() + 10
        while not self.server.started:
            if not self.thread.is_alive() or time.time() > deadline:
                raise RuntimeError("测试服务器启动失败")
            time.sleep(0.01)

    def stop(self) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=5)


@asynccontextmanager
async def live_client(app: Any) -> AsyncIterator[tuple[aiohttp.ClientSession, str]]:
    server = _LiveServer(app)
    await asyncio.to_thread(server.start)
    try:
        async with aiohttp.ClientSession() as session:
            yield session, f"http://127.0.0.1:{server.port}"
    finally:
        await asyncio.to_thread(server.stop)


# ---------- 系统 ----------


async def test_health_envelope() -> None:
    async with live_client(create_app()) as (session, base):
        async with session.get(f"{base}/health") as response:
            assert response.status == 200
            assert response.headers["cache-control"] == "no-store"
            payload = await response.json()
    assert payload["code"] == 0
    assert payload["data"]["status"] == "ok"
    assert isinstance(payload["data"]["uptime"], int)


async def test_sources_single_qs_platform() -> None:
    async with live_client(create_app()) as (session, base):
        async with session.get(f"{base}/sources") as response:
            payload = await response.json()
    platform = payload["data"]["platforms"][0]
    assert platform["id"] == "qs"
    assert platform["name"] == "汽水音乐"
    assert "128k" in platform["qualities"]
    caps = platform["capabilities"]
    assert caps["search"] and caps["url"] and caps["lyric"] and caps["pic"]
    assert not caps["boards"] and not caps["parse"]


# ---------- 标准 HTTP 语义 ----------


async def test_unknown_path_is_404() -> None:
    async with live_client(create_app()) as (session, base):
        async with session.get(f"{base}/not-exist-path") as response:
            assert response.status == 404
            payload = await response.json()
    assert payload["code"] == 404
    assert "接口不存在" in payload["msg"]


async def test_wrong_method_is_405_with_allow() -> None:
    async with live_client(create_app()) as (session, base):
        async with session.post(f"{base}/search/qs/{quote('稻香')}") as response:
            assert response.status == 405
            assert "GET" in response.headers["allow"]
            payload = await response.json()
    assert payload["code"] == 405


async def test_page_validation_is_422() -> None:
    async with live_client(create_app()) as (session, base):
        async with session.get(f"{base}/search/qs/abc/0") as response:
            assert response.status == 422
            payload = await response.json()
    assert payload["code"] == 422


async def test_unknown_quality_is_422() -> None:
    async with live_client(create_app()) as (session, base):
        async with session.get(f"{base}/url/qs/123", params={"quality": "flac"}) as response:
            assert response.status == 422


async def test_root_redirects_to_docs() -> None:
    async with live_client(create_app()) as (session, base):
        async with session.get(f"{base}/", allow_redirects=False) as response:
            assert response.status == 302
            assert "/docs" in response.headers["location"]


async def test_docs_and_openapi() -> None:
    async with live_client(create_app()) as (session, base):
        async with session.get(f"{base}/docs") as response:
            assert response.status == 200
            assert "swagger" in (await response.text()).lower()
        async with session.get(f"{base}/redoc") as response:
            assert response.status == 200
            assert "redoc" in (await response.text()).lower()
        async with session.get(f"{base}/openapi.json") as response:
            spec = await response.json()
    assert spec["info"]["title"] == "SodaServer 汽水音乐 API"
    assert "/search/qs/{keyword}" in spec["paths"]
    assert "/stream/qs/{id}" in spec["paths"]
    assert "SongInfo" in spec["components"]["schemas"]


async def test_cors_headers_and_preflight() -> None:
    async with live_client(create_app()) as (session, base):
        async with session.get(f"{base}/health", headers={"Origin": "http://example.com"}) as response:
            assert response.status == 200
            assert response.headers["access-control-allow-origin"] == "*"
        async with session.options(
            f"{base}/health",
            headers={"Origin": "http://example.com", "Access-Control-Request-Method": "GET"},
        ) as response:
            assert response.status == 200
            assert response.headers["access-control-allow-origin"] == "*"


# ---------- 搜索与歌曲资源（上游 mock，不发起真实外网请求） ----------


async def test_search_wraps_result_list(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_search(keyword: str, page: int = 1) -> dict:
        return {
            "list": [{"name": keyword, "songmid": "1", "source": "qs"}],
            "allPage": 2,
            "limit": 30,
            "total": 21,
            "source": "qs",
        }

    monkeypatch.setattr(music, "music_search", fake_search)
    async with live_client(create_app()) as (session, base):
        async with session.get(f"{base}/search/qs/{quote('稻香')}?limit=2") as response:
            payload = await response.json()
    assert payload["code"] == 0
    assert payload["data"] == {
        "source": "qs",
        "list": [{"name": "稻香", "songmid": "1", "source": "qs"}],
        "page": 1,
        "limit": 2,
    }


async def test_search_upstream_failure_is_502(monkeypatch: pytest.MonkeyPatch) -> None:
    async def boom(keyword: str, page: int = 1) -> dict:
        raise RuntimeError("官方签名失败")

    monkeypatch.setattr(music, "music_search", boom)
    async with live_client(create_app()) as (session, base):
        async with session.get(f"{base}/search/qs/x") as response:
            assert response.status == 502
            payload = await response.json()
    assert payload["code"] == 502
    assert "签名" in payload["msg"]


async def test_url_relative_stream_address_gets_signed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTH_KEY", "secret")

    async def fake_url(song_info: dict, quality: str) -> dict:
        return {"type": quality, "url": "/stream/qs/7079108541549643812"}

    monkeypatch.setattr(music, "get_music_url", fake_url)
    async with live_client(create_app()) as (session, base):
        async with session.get(
            f"{base}/url/qs/7079108541549643812",
            params={"quality": "320k"},
            headers={"X-Server-Key": "secret"},
        ) as response:
            assert response.status == 200
            payload = await response.json()
    url = payload["data"]["url"]
    assert url.startswith("http://127.0.0.1:") and "/stream/qs/7079108541549643812" in url
    assert "exp=" in url and "sig=" in url
    assert payload["data"]["type"] == "320k"


async def test_url_absolute_address_passes_through(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_url(song_info: dict, quality: str) -> dict:
        return {"type": quality, "url": "https://example.com/preview.m4a"}

    monkeypatch.setattr(music, "get_music_url", fake_url)
    async with live_client(create_app()) as (session, base):
        async with session.get(f"{base}/url/qs/123") as response:
            payload = await response.json()
    assert payload["data"]["url"] == "https://example.com/preview.m4a"


async def test_url_failure_is_502(monkeypatch: pytest.MonkeyPatch) -> None:
    async def boom(song_info: dict, quality: str) -> dict:
        raise RuntimeError("获取失败")

    monkeypatch.setattr(music, "get_music_url", boom)
    async with live_client(create_app()) as (session, base):
        async with session.get(f"{base}/url/qs/123") as response:
            assert response.status == 502


async def test_stream_proxy_returns_decrypted_audio(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_buffer(track_id: str) -> StreamEntry:
        return StreamEntry(
            buffer=b"audio-bytes", content_type="audio/mp4", bitrate=128, size=11, resolved_at=time.time()
        )

    monkeypatch.setattr(music, "get_stream_buffer", fake_buffer)
    async with live_client(create_app()) as (session, base):
        async with session.get(f"{base}/stream/qs/123") as response:
            assert response.status == 200
            assert response.headers["content-type"].startswith("audio/mp4")
            assert response.headers["accept-ranges"] == "none"
            assert await response.read() == b"audio-bytes"


async def test_lyric_and_info_are_enveloped(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_lyric(track_id: str) -> dict:
        return {"lyric": "[00:01.0]x", "tlyric": "", "yrc": "[00:01.0]"}

    async def fake_info(track_id: str) -> dict:
        return {"seo_track": {"track": {"id": track_id}}}

    monkeypatch.setattr(music, "get_lyric", fake_lyric)
    monkeypatch.setattr(music, "music_info", fake_info)
    async with live_client(create_app()) as (session, base):
        async with session.get(f"{base}/lyric/qs/1") as response:
            payload = await response.json()
        assert payload["data"] == {"lyric": "[00:01.0]x", "tlyric": "", "yrc": "[00:01.0]"}
        async with session.get(f"{base}/info/qs/42") as response:
            payload = await response.json()
    assert payload["data"]["seo_track"]["track"]["id"] == "42"


# ---------- 登录 ----------


async def test_session_bind_status_logout() -> None:
    async with live_client(create_app()) as (session, base):
        async with session.post(
            f"{base}/login/qs", json={"sessionid": "smoke_fake_sessionid_0123456789abcdef"}
        ) as response:
            assert response.status == 200
            payload = await response.json()
        assert payload["data"]["logged"] is True

        async with session.post(
            f"{base}/login/qs",
            json={"cookie": "sid_guard=smoke; sessionid=smoke_fake_sessionid_0123456789abcdef"},
        ) as response:
            assert response.status == 200

        async with session.get(f"{base}/login/status") as response:
            payload = await response.json()
        assert payload["data"]["qs"]["logged"] is True
        assert "updatedAt" in payload["data"]["qs"]

        async with session.delete(f"{base}/login/qs") as response:
            assert response.status == 200

        async with session.get(f"{base}/login/status") as response:
            payload = await response.json()
    assert payload["data"]["qs"]["logged"] is False


async def test_bad_cookie_is_422() -> None:
    async with live_client(create_app()) as (session, base):
        async with session.post(f"{base}/login/qs", json={"cookie": "foo=bar; baz=1"}) as response:
            assert response.status == 422


async def test_qr_check_requires_token() -> None:
    async with live_client(create_app()) as (session, base):
        async with session.post(f"{base}/login/qs/qr/check", json={}) as response:
            assert response.status == 422


# ---------- AUTH_KEY 鉴权与流签名 ----------


async def test_auth_key_gates_every_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTH_KEY", "secret")
    async with live_client(create_app()) as (session, base):
        for path in ("/health", "/docs", "/openapi.json", "/redoc", "/not-exist-path", "/login/status", "/sources"):
            async with session.get(f"{base}{path}") as response:
                assert response.status == 401, path
        async with session.get(f"{base}/sources", headers={"X-Server-Key": "secret"}) as response:
            assert response.status == 200


async def test_stream_accepts_header_or_signature(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTH_KEY", "secret")

    async def fake_buffer(track_id: str) -> StreamEntry:
        return StreamEntry(buffer=b"x", content_type="audio/mp4", bitrate=0, size=1, resolved_at=time.time())

    monkeypatch.setattr(music, "get_stream_buffer", fake_buffer)
    async with live_client(create_app()) as (session, base):
        # 裸 ?key= 已废弃
        async with session.get(f"{base}/stream/qs/123", params={"key": "secret"}) as response:
            assert response.status == 401
        # X-Server-Key 头可通过
        async with session.get(f"{base}/stream/qs/123", headers={"X-Server-Key": "secret"}) as response:
            assert response.status == 200
        # 有效签名通过
        exp = int(time.time()) + 600
        sig = hmac.new(b"secret", f"/stream/qs/123:{exp}".encode(), hashlib.sha256).hexdigest()[:32]
        async with session.get(f"{base}/stream/qs/123", params={"exp": str(exp), "sig": sig}) as response:
            assert response.status == 200
        # 错签名 / 过期签名 401
        async with session.get(f"{base}/stream/qs/123", params={"exp": str(exp), "sig": "0" * 32}) as response:
            assert response.status == 401
        old_exp = int(time.time()) - 10
        old_sig = hmac.new(b"secret", f"/stream/qs/123:{old_exp}".encode(), hashlib.sha256).hexdigest()[:32]
        async with session.get(f"{base}/stream/qs/123", params={"exp": str(old_exp), "sig": old_sig}) as response:
            assert response.status == 401


async def test_stream_sign_rejects_infinite_exp(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTH_KEY", "secret")
    async with live_client(create_app()) as (session, base):
        async with session.get(f"{base}/stream/qs/x", params={"exp": "inf", "sig": "x"}) as response:
            assert response.status == 401


async def test_rate_limit_counts_unauthorized_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTH_KEY", "secret")
    monkeypatch.setenv("RATE_LIMIT", "2")
    statuses: list[int] = []
    async with live_client(create_app()) as (session, base):
        for _ in range(3):
            async with session.get(f"{base}/health", headers={"X-Server-Key": "wrong"}) as response:
                statuses.append(response.status)
    # 前两次进入鉴权返回 401，第三次在鉴权之前被限流拦截。
    assert statuses == [401, 401, 429]
