"""qs 平台核心单元测试：工具、歌词解析、passport 状态机、签名参数、取流编排与 CENC 解密。

全部离线：不发真实网络请求（http_fetch 层由 tests/test_http.py 的 monkeypatch 覆盖）。
"""

from __future__ import annotations

import base64
import struct
import time

import app.qs.mssdk as mssdk
import app.qs.stream as stream
import pytest
from app.http.api_error import ApiError
from app.qs import lyric as qs_lyric
from app.qs import music_search, passport
from app.qs.audiodecrypt import (
    MP4Box,
    decrypt_audio_buffer,
    decrypt_spade_a,
    is_hex_key,
    resolve_audio_key,
)
from app.qs.lib import utils
from app.qs.lib.store import clear_credentials, get_credentials, set_credentials
from app.qs.stream import StreamEntry
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

# ---------- lib/utils ----------


def test_size_formate() -> None:
    assert utils.size_formate(None) == "0 B"
    assert utils.size_formate(0) == "0 B"
    assert utils.size_formate(512) == "512.00 B"
    assert utils.size_formate(1024) == "1.00 KB"
    assert utils.size_formate(1024 * 1024) == "1.00 MB"


def test_format_play_time() -> None:
    assert utils.format_play_time(0) == "--/--"
    assert utils.format_play_time(None) == "--/--"
    assert utils.format_play_time(65) == "01:05"
    assert utils.format_play_time(600.4) == "10:00"


# ---------- lyric ----------


def test_parse_timed_lyric() -> None:
    content = "[1000,2000]<0,100,0>你好 <100,200,0>世界\n[3500,500]<0,300,0>啦\n[6000,100]<0,100,0>   "
    info = qs_lyric.parse_timed_lyric(content)
    assert info["lyric"] == "[00:01.0]你好 世界\n[00:03.500]啦"
    assert info["yrc"] == "[00:01.0]<0,100>你好 <100,200>世界\n[00:03.500]<0,300>啦"


def test_parse_timed_lyric_empty() -> None:
    assert qs_lyric.parse_timed_lyric(None) == {"lyric": "", "yrc": ""}
    assert qs_lyric.parse_timed_lyric("not-a-lyric") == {"lyric": "", "yrc": ""}


async def test_get_lyric_rejects_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_info(track_id: str) -> dict:
        return {"lyric": {"content": ""}}

    monkeypatch.setattr(qs_lyric, "music_info", fake_info)
    with pytest.raises(RuntimeError, match="获取歌词失败"):
        await qs_lyric.get_lyric("1")


# ---------- passport ----------


def test_decide_qr_status_branches() -> None:
    assert passport.decide_qr_status({}) == passport.QrDecision("waiting", "等待扫码", "", 0)
    assert passport.decide_qr_status({"status": "2"}).state == "scanned"
    assert passport.decide_qr_status({"status": "confirmed"}).state == "confirmed"

    mfa = passport.decide_qr_status({"error_code": 2046})
    assert mfa.state == "mfa"
    assert passport.decide_qr_status({"account_flow": "verify"}).state == "mfa"

    limited = passport.decide_qr_status({"error_code": 7})
    assert limited.state == "waiting" and limited.rate_limited

    expired = passport.decide_qr_status({"status": "expired"})
    assert expired.state == "expired"
    refused = passport.decide_qr_status({"status": "refused"})
    assert "拒绝" in refused.message
    assert passport.decide_qr_status({"description": "二维码已过期"}).state == "expired"

    failed = passport.decide_qr_status({"status": "error", "error_code": 9})
    assert failed.state == "failed"


def test_build_scan_url() -> None:
    url = passport.build_scan_url("https://bff-pc.qishui.com/x?token=abc123&os=x", "我的电脑")
    assert url.startswith("https://bff-pc.qishui.com/light/invoke/scan_login?")
    assert "token=abc123" in url
    assert "os=Windows" in url
    assert "computer_name=%E6%88%91%E7%9A%84%E7%94%B5%E8%84%91" in url

    with pytest.raises(ApiError, match="缺少 token"):
        passport.build_scan_url("https://example.com/no-token")


def test_normalize_mobile() -> None:
    assert passport.normalize_mobile("13800138000") == "86 13800138000"
    assert passport.normalize_mobile("86 13800138000") == "86 13800138000"
    assert passport.normalize_mobile("86-13800138000") == "86 13800138000"
    assert passport.normalize_mobile("13800138000", "1") == "1 13800138000"


def test_session_id_of() -> None:
    assert passport.session_id_of("sid_guard=x; sessionid=abc") == "abc"
    assert passport.session_id_of("sessionid=abc") == "abc"
    assert passport.session_id_of("foo=bar") == ""


async def test_login_by_session_id() -> None:
    result = await passport.login_by_session_id("smoke_fake_sessionid_0123456789abcdef")
    assert result == {"logged": True, "sessionid": "smoke_fake_sessionid_0123456789abcdef"}

    cookie = "sid_guard=smoke; sessionid=abc12345678"
    result = await passport.login_by_session_id(cookie)
    assert result["sessionid"] == "abc12345678"
    cred = get_credentials("qs")
    assert cred is not None and cred["cookie"] == cookie  # 完整串整体保存

    with pytest.raises(ApiError) as err:
        await passport.login_by_session_id("foo=bar; baz=1")
    assert err.value.status == 422
    clear_credentials("qs")


def test_status_reflects_store() -> None:
    assert passport.status() == {"logged": False}
    set_credentials("qs", {"sessionid": "x", "cookie": "sessionid=x"})
    assert passport.status()["logged"] is True
    assert passport.get_session_id() == "x"
    assert passport.get_cookie() == "sessionid=x"
    clear_credentials("qs")
    assert passport.status() == {"logged": False}
    assert passport.get_session_id() == ""


def test_store_roundtrip_and_merge() -> None:
    set_credentials("qs", {"sessionid": "a"})
    set_credentials("qs", {"cookie": "sessionid=a"})  # 合并而非覆盖
    cred = get_credentials("qs")
    assert cred is not None
    assert cred["sessionid"] == "a" and cred["cookie"] == "sessionid=a"
    assert "updatedAt" in cred
    clear_credentials("qs")
    assert get_credentials("qs") is None


def test_get_device_ids_is_stable() -> None:
    clear_credentials("_qs_passport_device")
    ids = passport.get_device_ids()
    again = passport.get_device_ids()
    assert ids == again
    assert int(ids.install_id) == int(ids.device_id) + 1
    stored = get_credentials("_qs_passport_device")
    assert stored is not None and stored["deviceId"] == ids.device_id
    clear_credentials("_qs_passport_device")


# ---------- mssdk ----------


def test_pc_common_params_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("QS_SIGN_SERVER", raising=False)
    monkeypatch.delenv("QS_HELIOS", raising=False)
    monkeypatch.delenv("QS_MEDUSA", raising=False)
    monkeypatch.setattr(mssdk, "_probed_server", None)
    monkeypatch.setattr(mssdk, "FIXED_SIGN_FILE", mssdk.ROOT / "data" / "qs-sign-nonexist.json")
    clear_credentials("_qs_passport_device")
    params = mssdk.pc_common_params()
    assert params["aid"] == "386088"
    assert params["app_name"] == "luna_pc"
    assert params["device_platform"] == "windows"
    assert params["fp"] == params["device_id"]
    assert int(params["iid"]) == int(params["device_id"]) + 1
    clear_credentials("_qs_passport_device")


def test_load_sign_mode_prefers_explicit_server(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QS_SIGN_SERVER", "http://127.0.0.1:16889/")
    monkeypatch.setenv("QS_SIGN_TOKEN", "tok")
    mode = mssdk._load_sign_mode()
    assert mode is not None and mode.kind == "server"
    assert mode.server == "http://127.0.0.1:16889"  # 尾部斜杠已去除
    assert mode.token == "tok"


def test_fixed_config_from_file(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.delenv("QS_SIGN_SERVER", raising=False)
    monkeypatch.delenv("QS_HELIOS", raising=False)
    monkeypatch.delenv("QS_MEDUSA", raising=False)
    monkeypatch.setattr(mssdk, "_probed_server", None)
    sign_file = tmp_path / "qs-sign.json"
    sign_file.write_text('{"helios": "h", "medusa": "m", "device_id": "d1", "install_id": "i1"}', encoding="utf-8")
    monkeypatch.setattr(mssdk, "FIXED_SIGN_FILE", sign_file)
    mode = mssdk._load_sign_mode()
    assert mode is not None and mode.kind == "fixed"
    assert mode.fixed is not None and mode.fixed.helios == "h"

    # 签名参数应使用固定值配套的设备（签名与设备绑定）
    params = mssdk.pc_common_params()
    assert params["device_id"] == "d1" and params["iid"] == "i1"

    monkeypatch.setenv("QS_HELIOS", "eh")
    monkeypatch.setenv("QS_MEDUSA", "em")
    mode = mssdk._load_sign_mode()
    assert mode is not None and mode.kind == "fixed" and mode.fixed is not None and mode.fixed.helios == "eh"


def test_mssdk_available_when_probe_pending(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("QS_SIGN_SERVER", raising=False)
    monkeypatch.setattr(mssdk, "FIXED_SIGN_FILE", mssdk.ROOT / "data" / "qs-sign-nonexist.json")
    monkeypatch.setattr(mssdk, "_probed_server", mssdk._PENDING)
    assert mssdk.mssdk_available() is True  # 探测未完成先放行，签名调用会等探测结果
    monkeypatch.setattr(mssdk, "_probed_server", None)
    assert mssdk.mssdk_available() is False


# ---------- music_search（结果整形，不发请求） ----------


def test_handle_result_maps_track() -> None:
    raw_list = [
        {
            "entity": {
                "track": {
                    "id": 7079108541549643812,
                    "name": "小半",
                    "duration": 254000,
                    "artists": [{"name": "陈粒"}, {"name": ""}],
                    "album": {
                        "id": "a1",
                        "name": "小半",
                        "url_cover": {"uri": "x.jpg", "urls": ["https://p9-luna.douyinpic.com/img/"]},
                    },
                    "preview": {"bit_rates": [{"size": 500000}]},
                }
            }
        },
        {"entity": {"track": {}}},  # 无 id 丢弃
    ]
    songs = music_search._handle_result(raw_list)
    assert len(songs) == 1
    song = songs[0]
    assert song["songmid"] == "7079108541549643812"
    assert song["singer"] == "陈粒"
    assert song["name"] == "小半"
    assert song["albumName"] == "小半"
    assert song["source"] == "qs"
    assert song["interval"] == "04:14"
    assert song["types"] == [{"type": "128k", "size": "488.28 KB"}]
    assert song["img"] == "https://p9-luna.douyinpic.com/img/x.jpg~c5_500x500.jpg"


def test_handle_result_empty() -> None:
    assert music_search._handle_result(None) == []


def test_handle_search_body_paging() -> None:
    # result_groups 里是上游原始实体（handleSearchBody 内部再整形），不是处理后的歌曲
    body = {
        "result_groups": [
            {"data": [], "has_more": True},
            {"data": [{"entity": {"track": {"id": 1, "duration": 61000}}}], "has_more": True},
        ]
    }
    result = music_search._handle_search_body(body, cursor=20, page=2)
    assert len(result["list"]) == 1
    assert result["total"] == 22  # cursor + 命中数 + has_more 哨兵
    assert result["allPage"] == 3
    assert result["source"] == "qs"

    body = {"result_groups": [{"data": [{"entity": {"track": {"id": 2, "duration": 61000}}}], "has_more": False}]}
    result = music_search._handle_search_body(body, cursor=0, page=1)
    assert result["total"] == 1 and result["allPage"] == 1


# ---------- stream（缓存与编排在本地完成的部分） ----------


def test_track_v2_body_is_compact_json() -> None:
    assert stream._track_v2_body("123") == (
        '{"track_id":"123","media_type":"track","queue_type":"search_one_track","scene_name":"search"}'
    )


def test_pick_entries_prefer_highest_bitrate() -> None:
    play_list = [
        {"MainPlayUrl": "low", "Bitrate": 64000},
        {"MainPlayUrl": "enc", "Bitrate": 132000, "PlayAuth": "x"},
        {"MainPlayUrl": "high", "Bitrate": 260000},
    ]
    best = stream._pick_best_entry(play_list)
    assert best is not None and best["MainPlayUrl"] == "high"
    # 匿名通道跳过加密条目
    assert stream._pick_plain_audio_url(play_list) == "high"


def test_stream_cache_lru_and_ttl() -> None:
    stream._stream_cache.clear()

    def put(tid: str, age: float = 0) -> None:
        stream._cache_put(
            tid, StreamEntry(buffer=b"x", content_type="audio/mp4", bitrate=0, size=1, resolved_at=time.time() - age)
        )

    put("a")
    put("b")
    put("c")
    put("d")  # 超出容量：最早插入且未被刷新的 a 被淘汰（与 TS 版一致，读取不刷新 LRU 顺序）
    assert stream.get_cached_stream("a") is None
    assert stream.get_cached_stream("b") is not None
    assert stream.get_cached_stream("c") is not None
    assert stream.get_cached_stream("d") is not None

    put("old", age=stream.STREAM_CACHE_TTL + 1)
    assert stream.get_cached_stream("old") is None  # TTL 过期
    stream._stream_cache.clear()


def test_stream_cache_ttl_expiry(monkeypatch: pytest.MonkeyPatch) -> None:
    stream._stream_cache.clear()
    entry = StreamEntry(
        buffer=b"x", content_type="audio/mp4", bitrate=0, size=1, resolved_at=time.time() - stream.STREAM_CACHE_TTL - 1
    )
    stream._stream_cache["k"] = entry
    assert stream.get_cached_stream("k") is None
    assert "k" not in stream._stream_cache  # 过期即清除


async def test_get_music_url_anonymous_passthrough(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_preview(track_id: str) -> str:
        return "https://example.com/preview.m4a"

    monkeypatch.setattr(stream, "get_session_id", lambda: "")
    monkeypatch.setattr(stream, "_anonymous_preview_url", fake_preview)
    result = await stream.get_music_url({"songmid": "42"}, "128k")
    assert result == {"type": "128k", "url": "https://example.com/preview.m4a"}


async def test_get_music_url_signed_path_and_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    async def ok_buffer(track_id: str) -> stream.StreamEntry:
        return StreamEntry(buffer=b"x", content_type="audio/mp4", bitrate=128, size=1, resolved_at=time.time())

    async def preview(track_id: str) -> str:
        return "https://example.com/preview.m4a"

    monkeypatch.setattr(stream, "get_session_id", lambda: "sid")
    monkeypatch.setattr(stream, "get_stream_buffer", ok_buffer)
    result = await stream.get_music_url({"songmid": "42"}, "320k")
    assert result == {"type": "320k", "url": "/stream/qs/42"}

    async def boom(track_id: str) -> stream.StreamEntry:
        raise RuntimeError("签名失败")

    monkeypatch.setattr(stream, "get_stream_buffer", boom)
    monkeypatch.setattr(stream, "_anonymous_preview_url", preview)
    result = await stream.get_music_url({"songmid": "42"}, "320k")
    assert result == {"type": "320k", "url": "https://example.com/preview.m4a"}  # 登录态失败降级匿名试听


# ---------- audiodecrypt ----------


def test_is_hex_key() -> None:
    assert is_hex_key("00112233445566778899AABBCCDDEEFF")
    assert is_hex_key("  00112233445566778899aabbccddeeff  ")
    assert not is_hex_key("00112233445566778899aabbccddeee")  # 31 位
    assert not is_hex_key("00112233445566778899aabbccddeefff")  # 33 位
    assert not is_hex_key(None)


def test_resolve_audio_key_errors() -> None:
    with pytest.raises(ValueError, match="不能为空"):
        resolve_audio_key({})
    with pytest.raises(ValueError, match="无效"):
        resolve_audio_key({"spade_a": "not-base64!!"})


def _make_spade(key_hex: str) -> bytes:
    """构造 decrypt_spade 的合法输入：解码后恰为 key_hex（skip 字节选 '0'）。

    解密方向 decoded[i] = (inner[i] ^ buffer[i]) - popcount(i) - 21，其中 buffer[0:2] = FA 55、
    buffer[i] = inner[i-2]（i≥2）；这里按 buffer 逐字节反解出 inner。
    """
    msg = bytes([48]) + key_hex.encode()  # 解码流：skip 字节('0'=无跳过) + 32 字节 key
    inner = bytearray([(msg[0] + 0 + 21) ^ 0xFA])
    inner.append((msg[1] + 1 + 21) ^ 0x55)
    for i in range(2, len(msg)):
        x = msg[i] + i.bit_count() + 21
        assert x <= 255
        inner.append(x ^ inner[i - 2])
    spade = bytearray(b"\x00")
    spade.extend(inner)
    spade.extend(b"\x00\x00")
    # padding_length = spade[0]^spade[1]^spade[2] - 48 = 2
    spade[0] = (48 + 2) ^ spade[1] ^ spade[2]
    return bytes(spade)


def test_decrypt_spade_roundtrip() -> None:
    key_hex = "00112233445566778899aabbccddeeff"
    spade = _make_spade(key_hex)
    assert decrypt_spade_a(base64.b64encode(spade).decode()) == key_hex
    assert decrypt_spade_a("!!!not-base64!!!") == ""
    assert decrypt_spade_a(None) == ""


def _box(box_type: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", 8 + len(payload)) + box_type + payload


def _build_encrypted_m4a(
    key_hex: str, samples: list[bytes], ivs: list[bytes], flac_stsd: bool = False
) -> tuple[bytes, bytes]:
    """构造一个最小 CENC m4a（ftyp+moov+mdat），返回 (密文文件, 期望明文 mdat payload)。"""
    key_bytes = bytes.fromhex(key_hex)
    encrypted = b"".join(
        Cipher(algorithms.AES(key_bytes), modes.CTR(iv)).encryptor().update(sample)
        for sample, iv in zip(samples, ivs, strict=True)
    )

    stsd_children = _box(b"dfLa", struct.pack(">I", 0) + b"meta") if flac_stsd else _box(b"mp4a", bytes(12))
    stsd = _box(b"stsd", struct.pack(">II", 0, 1) + stsd_children)
    stsz = _box(b"stsz", struct.pack(">III", 0, 0, len(samples)) + b"".join(struct.pack(">I", len(s)) for s in samples))
    stsc = _box(b"stsc", struct.pack(">IIIII", 0, 1, 1, len(samples), 1))
    stco = _box(b"stco", struct.pack(">II", 0, 1) + struct.pack(">I", 0))  # 偏移占位，解密时重写
    senc = _box(b"senc", struct.pack(">II", 0, len(samples)) + b"".join(iv[:8] for iv in ivs))
    stbl = _box(b"stbl", stsd + stsz + stsc + stco + senc)
    moov = _box(b"moov", _box(b"trak", _box(b"mdia", _box(b"minf", stbl))))
    ftyp = _box(b"ftyp", b"M4A " + struct.pack(">I", 0))
    mdat_offset = len(ftyp) + len(moov) + 8
    file_bytes = ftyp + moov + _box(b"mdat", encrypted)
    mdat_box = MP4Box.find_box(memoryview(file_bytes), "mdat")
    assert mdat_box is not None and mdat_box.offset + 8 == mdat_offset
    return file_bytes, b"".join(samples)  # 解密结果应还原为原始明文样本


def test_decrypt_audio_buffer_m4a() -> None:
    key_hex = "00112233445566778899aabbccddeeff"
    ivs = [b"12345678" + bytes(8), b"87654321" + bytes(8)]
    samples = [bytes(range(100)), b"Z" * 50]
    file_bytes, expected_plain = _build_encrypted_m4a(key_hex, samples, ivs)

    result = decrypt_audio_buffer(file_bytes, key_hex)
    assert result["content_type"] == "audio/mp4"
    assert result["codec"] == "mp4"
    assert result["sample_count"] == 2
    output = result["buffer"]
    assert output.startswith(file_bytes[:16])  # ftyp 原样保留
    out_mdat = MP4Box.find_box(memoryview(output), "mdat")
    assert out_mdat is not None
    assert bytes(out_mdat.data) == expected_plain  # 解密回明文
    # moov 已重建且 stco 偏移指向新 mdat（stco 嵌套在 trak/mdia/minf/stbl 下，直接按标记定位）
    stco_idx = output.rfind(b"stco")
    assert stco_idx > 0
    new_offset = struct.unpack_from(">I", output, stco_idx + 12)[0]  # 跳过 ver/flags + entry_count
    assert new_offset == out_mdat.offset + 8


def test_decrypt_audio_buffer_flac() -> None:
    key_hex = "ffeeddccbbaa99887766554433221100"
    ivs = [b"abcdef01" + bytes(8)]
    samples = [b"\x01" * 64]
    file_bytes, expected_plain = _build_encrypted_m4a(key_hex, samples, ivs, flac_stsd=True)

    result = decrypt_audio_buffer(file_bytes, key_hex)
    assert result["content_type"] == "audio/flac"
    assert result["buffer"] == b"fLaC" + b"meta" + expected_plain


def test_decrypt_audio_buffer_rejects_bad_container() -> None:
    with pytest.raises(ValueError, match="moov"):
        decrypt_audio_buffer(b"\x00" * 64, "00112233445566778899aabbccddeeff")
    with pytest.raises(ValueError, match="hex_key"):
        decrypt_audio_buffer(b"\x00" * 64, "short")
