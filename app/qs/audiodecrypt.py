"""CENC 音频解密（AES-CTR 逐样本）—— 移植自 guowenye/qishui-api src/audioDecryptor.js。

spade_a/PlayAuth → 32 位 hex key；密音频（m4a CENC / flac）→ 明文音频 Buffer
"""

from __future__ import annotations

import base64
import binascii
import re
import struct
from typing import Any

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

_ENCRYPTED_BOX_TYPES = {"senc", "saio", "saiz", "sinf", "schi", "tenc", "schm", "frma"}
_CONTAINER_BOX_TYPES = {"moov", "trak", "mdia", "minf", "stbl", "stsd"}
_MAX_SAMPLE_COUNT = 200000
_MAX_CHUNK_COUNT = 200000
_MAX_STSC_ENTRY_COUNT = 20000


def _decode_base36(code_point: int) -> int:
    if 48 <= code_point <= 57:
        return code_point - 48
    if 97 <= code_point <= 122:
        return code_point - 97 + 10
    return 0xFF


def _bit_count(number: int) -> int:
    return number.bit_count()


def _decrypt_spade_inner(spade_key: bytes) -> bytes:
    result = bytearray(spade_key)
    buffer = bytes([0xFA, 0x55]) + spade_key
    for index in range(len(result)):
        value = (spade_key[index] ^ buffer[index]) - _bit_count(index) - 21
        while value < 0:
            value += 0xFF
        result[index] = value
    return bytes(result)


def decrypt_spade(spade_key_bytes: bytes) -> str:
    length = len(spade_key_bytes)
    if length < 3:
        return ""
    padding_length = (spade_key_bytes[0] ^ spade_key_bytes[1] ^ spade_key_bytes[2]) - 48
    if length < padding_length + 2:
        return ""
    inner_input = spade_key_bytes[1 : length - padding_length]
    decoded = _decrypt_spade_inner(inner_input)
    if not decoded:
        return ""
    skip_bytes = _decode_base36(decoded[0])
    decoded_message_length = length - padding_length - 2
    end_index = 1 + decoded_message_length - skip_bytes
    if end_index > len(decoded):
        return ""
    try:
        return decoded[1:end_index].decode("utf-8")
    except UnicodeDecodeError:
        return ""


def decrypt_spade_a(spade_a: Any) -> str:
    try:
        return decrypt_spade(base64.b64decode(str(spade_a or "").strip()))
    except (ValueError, binascii.Error):
        return ""


_HEX_KEY_RE = re.compile(r"^[a-f0-9]{32}$", re.I)


def is_hex_key(value: Any) -> bool:
    return isinstance(value, str) and bool(_HEX_KEY_RE.match(value.strip()))


def resolve_audio_key(source: dict[str, Any] | None = None) -> str:
    source = source or {}
    raw_hex_key = source.get("hex_key") or source.get("key")
    if is_hex_key(raw_hex_key):
        return str(raw_hex_key).strip().lower()

    raw_spade_a = source.get("spade_a") or source.get("spadeA")
    if not raw_spade_a:
        raise ValueError("spade_a 或 hex_key 不能为空")
    hex_key = decrypt_spade_a(raw_spade_a).strip()
    if not is_hex_key(hex_key):
        raise ValueError("spade_a 或 hex_key 无效")
    return hex_key.lower()


def hex_to_bytes(hex_key: str) -> bytes:
    if not is_hex_key(hex_key):
        raise ValueError("hex_key 必须是 32 位十六进制 AES-128 密钥")
    return bytes.fromhex(hex_key.strip())


def _read_uint32_be(file_data: memoryview, offset: int) -> int:
    if not isinstance(offset, int) or offset < 0 or offset + 4 > len(file_data):
        raise ValueError("音频容器结构无效")
    return struct.unpack_from(">I", file_data, offset)[0]


def _write_uint32_be(value: int) -> bytes:
    return struct.pack(">I", value)


def _read_ascii(file_data: memoryview, start: int, end: int) -> str:
    return bytes(file_data[start:end]).decode("ascii", errors="replace")


class MP4Box:
    __slots__ = ("data", "offset", "size", "type")

    def __init__(self, file_data: memoryview, offset: int) -> None:
        self.offset = offset
        self.size = _read_uint32_be(file_data, offset)
        self.type = _read_ascii(file_data, offset + 4, offset + 8)
        self.data = file_data[offset + 8 : offset + self.size]

    @staticmethod
    def find_box(file_data: memoryview, box_type: str, offset: int = 0, end: int | None = None) -> MP4Box | None:
        search_end = len(file_data) if end is None else end
        position = offset
        while position < search_end:
            if position + 8 > search_end:
                break
            box_size = _read_uint32_be(file_data, position)
            if box_size == 0 or box_size > search_end - position or box_size < 8:
                break
            if _read_ascii(file_data, position + 4, position + 8) == box_type:
                return MP4Box(file_data, position)
            position += box_size
        return None


def _require_box(box: MP4Box | None, name: str) -> MP4Box:
    if box is None:
        raise ValueError(f"音频容器缺少 {name} box")
    return box


def _parse_stsz(stsz_data: memoryview) -> list[int]:
    sample_size = _read_uint32_be(stsz_data, 4)
    sample_count = _read_uint32_be(stsz_data, 8)
    if sample_count > _MAX_SAMPLE_COUNT:
        raise ValueError("音频样本数量超过限制")
    if sample_size != 0:
        return [sample_size] * sample_count
    if 12 + sample_count * 4 > len(stsz_data):
        raise ValueError("stsz box 样本大小数据不完整")
    return [_read_uint32_be(stsz_data, 12 + index * 4) for index in range(sample_count)]


def _parse_stsc(stsc_data: memoryview) -> list[dict[str, int]]:
    entry_count = _read_uint32_be(stsc_data, 4)
    if entry_count > _MAX_STSC_ENTRY_COUNT:
        raise ValueError("stsc entry 数量超过限制")
    if 8 + entry_count * 12 > len(stsc_data):
        raise ValueError("stsc box 数据不完整")
    entries = []
    for index in range(entry_count):
        base_offset = 8 + index * 12
        entries.append(
            {
                "first_chunk": _read_uint32_be(stsc_data, base_offset),
                "samples_per_chunk": _read_uint32_be(stsc_data, base_offset + 4),
                "id": _read_uint32_be(stsc_data, base_offset + 8),
            }
        )
    return entries


def _parse_senc(senc_data: memoryview) -> list[bytes]:
    sample_count = _read_uint32_be(senc_data, 4)
    if sample_count > _MAX_SAMPLE_COUNT:
        raise ValueError("senc 样本数量超过限制")
    if 8 + sample_count * 8 > len(senc_data):
        raise ValueError("senc box IV 数据不完整")
    ivs: list[bytes] = []
    position = 8
    for _ in range(sample_count):
        # CTR IV：前 8 字节来自 senc，其余补零
        ivs.append(bytes(senc_data[position : position + 8]) + bytes(8))
        position += 8
    return ivs


def _calculate_chunk_offsets(
    sample_sizes: list[int], stsc_entries: list[dict[str, int]], chunk_count: int, base_offset: int
) -> list[int]:
    offsets: list[int] = []
    current_offset = base_offset
    sample_index = 0
    for chunk_index in range(1, chunk_count + 1):
        offsets.append(current_offset)
        samples_per_chunk = 0
        for entry_index, current_entry in enumerate(stsc_entries):
            next_entry = stsc_entries[entry_index + 1] if entry_index + 1 < len(stsc_entries) else None
            if chunk_index >= current_entry["first_chunk"] and (
                next_entry is None or chunk_index < next_entry["first_chunk"]
            ):
                samples_per_chunk = current_entry["samples_per_chunk"]
                break
        for _ in range(samples_per_chunk):
            if sample_index < len(sample_sizes):
                current_offset += sample_sizes[sample_index]
            sample_index += 1
    return offsets


def _update_stco(stco_data: memoryview, offsets: list[int]) -> bytes:
    chunk_count = _read_uint32_be(stco_data, 4)
    if len(offsets) < chunk_count:
        raise ValueError("stco 偏移数量不足")
    header = bytes(stco_data[:8])
    body = b"".join(_write_uint32_be(offsets[index]) for index in range(chunk_count))
    return header + body


def _process_box_tree(
    file_data: memoryview,
    offset: int,
    size: int,
    new_mdat_offset: int,
    context: dict[str, Any],
) -> bytes:
    parts: list[bytes] = []
    position = offset + 8
    end = offset + size

    while position < end:
        if position + 8 > end:
            parts.append(bytes(file_data[position:end]))
            break
        box_size = _read_uint32_be(file_data, position)
        if box_size < 8 or box_size > end - position:
            parts.append(bytes(file_data[position:end]))
            break
        box_type = _read_ascii(file_data, position + 4, position + 8)

        if box_type in _ENCRYPTED_BOX_TYPES:
            position += box_size
            continue

        if box_type == "enca":
            inner = _process_box_tree(file_data, position, box_size, new_mdat_offset, context)
            parts.append(_write_uint32_be(len(inner) + 8))
            parts.append(b"mp4a")
            parts.append(inner)
            position += box_size
            continue

        if box_type == "stco":
            offsets = _calculate_chunk_offsets(
                context["sample_sizes"], context["stsc_entries"], context["chunk_count"], new_mdat_offset
            )
            updated_body = _update_stco(file_data[position + 8 : position + box_size], offsets)
            parts.append(_write_uint32_be(len(updated_body) + 8))
            parts.append(b"stco")
            parts.append(updated_body)
            position += box_size
            continue

        if box_type in _CONTAINER_BOX_TYPES:
            inner = _process_box_tree(file_data, position, box_size, new_mdat_offset, context)
            parts.append(_write_uint32_be(len(inner) + 8))
            parts.append(box_type.encode("ascii"))
            parts.append(inner)
            position += box_size
            continue

        parts.append(bytes(file_data[position : position + box_size]))
        position += box_size

    return b"".join(parts)


def _scan_for_flac_metadata(stsd_data: memoryview) -> memoryview | None:
    marker = b"dfLa"
    for index in range(4, len(stsd_data) - 4):
        if bytes(stsd_data[index : index + 4]) == marker:
            box_size = _read_uint32_be(stsd_data, index - 4)
            if box_size >= 8 and index - 4 + box_size <= len(stsd_data):
                return stsd_data[index + 4 : index - 4 + box_size]
    return None


def _decrypt_ctr(encrypted_sample: bytes, key_bytes: bytes, iv: bytes) -> bytes:
    decryptor = Cipher(algorithms.AES(key_bytes), modes.CTR(iv)).decryptor()
    return decryptor.update(encrypted_sample) + decryptor.finalize()


def decrypt_audio_buffer(input_data: bytes | bytearray | memoryview, hex_key: str) -> dict[str, Any]:
    if isinstance(input_data, (bytes, bytearray)):
        file_data = memoryview(bytes(input_data))
    elif isinstance(input_data, memoryview):
        file_data = input_data
    else:
        raise ValueError("音频数据必须是 Buffer、Uint8Array 或 ArrayBuffer")
    key_bytes = hex_to_bytes(hex_key)

    ftyp = MP4Box.find_box(file_data, "ftyp")
    moov = _require_box(MP4Box.find_box(file_data, "moov"), "moov")
    trak = _require_box(MP4Box.find_box(file_data, "trak", moov.offset + 8, moov.offset + moov.size), "trak")
    mdia = _require_box(MP4Box.find_box(file_data, "mdia", trak.offset + 8, trak.offset + trak.size), "mdia")
    minf = _require_box(MP4Box.find_box(file_data, "minf", mdia.offset + 8, mdia.offset + mdia.size), "minf")
    stbl = _require_box(MP4Box.find_box(file_data, "stbl", minf.offset + 8, minf.offset + minf.size), "stbl")
    stbl_end = stbl.offset + stbl.size
    stsd = _require_box(MP4Box.find_box(file_data, "stsd", stbl.offset + 8, stbl_end), "stsd")
    stsz = _require_box(MP4Box.find_box(file_data, "stsz", stbl.offset + 8, stbl_end), "stsz")
    stsc = _require_box(MP4Box.find_box(file_data, "stsc", stbl.offset + 8, stbl_end), "stsc")
    stco = _require_box(MP4Box.find_box(file_data, "stco", stbl.offset + 8, stbl_end), "stco")
    mdat = _require_box(MP4Box.find_box(file_data, "mdat"), "mdat")

    senc = MP4Box.find_box(file_data, "senc", stbl.offset + 8, stbl_end)
    if senc is None:
        senc = MP4Box.find_box(file_data, "senc", moov.offset + 8, moov.offset + moov.size)
    _require_box(senc, "senc")

    sample_sizes = _parse_stsz(stsz.data)
    stsc_entries = _parse_stsc(stsc.data)
    chunk_count = _read_uint32_be(stco.data, 4)
    if chunk_count > _MAX_CHUNK_COUNT:
        raise ValueError("音频 chunk 数量超过限制")
    if 8 + chunk_count * 4 > len(stco.data):
        raise ValueError("stco box 数据不完整")
    ivs = _parse_senc(_require_box(senc, "senc").data)
    if len(ivs) < len(sample_sizes):
        raise ValueError("senc IV 数量少于样本数量")
    if sum(sample_sizes) > mdat.size - 8:
        raise ValueError("样本总大小超过 mdat 数据范围")

    decrypted_samples: list[bytes] = []
    sample_offset = mdat.offset + 8
    for index, sample_size in enumerate(sample_sizes):
        if sample_offset + sample_size > len(file_data):
            raise ValueError("音频样本数据不完整")
        decrypted_samples.append(
            _decrypt_ctr(bytes(file_data[sample_offset : sample_offset + sample_size]), key_bytes, ivs[index])
        )
        sample_offset += sample_size

    flac_metadata = _scan_for_flac_metadata(stsd.data)
    if flac_metadata is not None:
        metadata_start = 4 if len(flac_metadata) > 4 else 0
        output = b"fLaC" + bytes(flac_metadata[metadata_start:]) + b"".join(decrypted_samples)
        return {
            "buffer": output,
            "extension": ".flac",
            "content_type": "audio/flac",
            "codec": "flac",
            "sample_count": len(sample_sizes),
        }

    context: dict[str, Any] = {"sample_sizes": sample_sizes, "stsc_entries": stsc_entries, "chunk_count": chunk_count}
    ftyp_size = ftyp.size if ftyp is not None else 0
    dummy_moov = _process_box_tree(file_data, moov.offset, moov.size, 0, context)
    new_mdat_offset = ftyp_size + len(dummy_moov) + 16
    clean_moov_data = _process_box_tree(file_data, moov.offset, moov.size, new_mdat_offset, context)
    clean_moov = _write_uint32_be(len(clean_moov_data) + 8) + b"moov" + clean_moov_data
    mdat_data = b"".join(decrypted_samples)
    new_mdat = _write_uint32_be(len(mdat_data) + 8) + b"mdat" + mdat_data
    final_parts: list[bytes] = []
    if ftyp is not None:
        final_parts.append(bytes(file_data[ftyp.offset : ftyp.offset + ftyp.size]))
    final_parts.append(clean_moov)
    final_parts.append(new_mdat)

    return {
        "buffer": b"".join(final_parts),
        "extension": ".m4a",
        "content_type": "audio/mp4",
        "codec": "mp4",
        "sample_count": len(sample_sizes),
    }
