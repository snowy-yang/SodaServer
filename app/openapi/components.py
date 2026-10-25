"""OpenAPI 领域模型 —— 注册为 FastAPI 共享 components schema。

路由的 response 通过 `{$ref: "#/components/schemas/名称"}` 引用。
注：additionalProperties: true 保证序列化时不剥离未声明字段。
"""

from __future__ import annotations

from typing import Any


def ref(name: str) -> dict[str, Any]:
    return {"$ref": f"#/components/schemas/{name}"}


COMPONENT_SCHEMAS: dict[str, Any] = {
    "ErrorEnvelope": {
        "type": "object",
        "properties": {
            "code": {"type": "integer", "description": "与 HTTP 状态码一致", "example": 422},
            "msg": {"type": "string", "description": "错误描述"},
        },
        "additionalProperties": True,
        "required": ["code", "msg"],
    },
    "QualityInfo": {
        "type": "object",
        "properties": {"size": {"type": "string", "example": "9.8M"}},
        "additionalProperties": True,
    },
    "SongType": {
        "type": "object",
        "properties": {
            "type": {"type": "string", "example": "128k", "enum": ["128k", "320k"]},
            "size": {"type": "string", "example": "9.8M"},
        },
        "additionalProperties": True,
    },
    "SongInfo": {
        "type": "object",
        "properties": {
            "songmid": {"type": "string", "description": "汽水 track id"},
            "albumId": {"type": "string"},
            "name": {"type": "string", "description": "歌名"},
            "singer": {"type": "string", "description": "歌手"},
            "source": {"type": "string", "description": "来源平台 id（恒为 qs）"},
            "interval": {"type": "string", "description": "时长，格式 mm:ss"},
            "albumName": {"type": "string"},
            "img": {"type": ["string", "null"], "description": "封面地址"},
            "types": {"type": "array", "items": ref("SongType"), "description": "可用音质列表"},
            "_types": {"type": "object", "additionalProperties": ref("QualityInfo")},
            "typeUrl": {"type": "object"},
        },
        "additionalProperties": True,
    },
    "SearchResult": {
        "type": "object",
        "properties": {
            "source": {"type": "string", "description": "来源平台 id（恒为 qs）"},
            "list": {"type": "array", "items": ref("SongInfo")},
            "page": {"type": "integer"},
            "limit": {"type": "integer"},
        },
        "additionalProperties": True,
    },
    "MusicUrlResult": {
        "type": "object",
        "properties": {
            "type": {
                "type": "string",
                "example": "128k",
                "description": "实际命中的音质（登录态为解密完整曲目，匿名只有 30 秒试听）",
            },
            "url": {
                "type": "string",
                "description": "播放地址；登录态为 /stream/qs/:id 代理地址（已补全 origin 并附签名）",
            },
        },
        "additionalProperties": True,
    },
    "LyricInfo": {
        "type": "object",
        "properties": {
            "lyric": {"type": "string", "description": "原文歌词（LRC）"},
            "tlyric": {"type": "string", "description": "翻译歌词（汽水无翻译，恒为空串）"},
            "yrc": {"type": "string", "description": "逐字歌词"},
        },
        "additionalProperties": True,
        "required": ["lyric", "tlyric"],
    },
    "PicResult": {
        "type": "object",
        "properties": {"url": {"type": "string", "description": "封面地址"}},
        "additionalProperties": True,
    },
}
