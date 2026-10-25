"""API 业务错误 —— 路由处理中抛出，由全局异常处理器转换为对应 HTTP 状态码的 JSON 响应。"""

from __future__ import annotations

from typing import Any


class ApiError(Exception):
    """业务错误：status 同时作为响应体中的 code。"""

    def __init__(self, status: int, msg: str, detail: Any = None) -> None:
        super().__init__(msg)
        self.status = status
        self.code = status
        self.detail = detail


def not_found(msg: str, detail: Any = None) -> ApiError:
    """资源不存在（未知路由）。"""
    return ApiError(404, msg, detail)


def upstream(msg: str, detail: Any = None) -> ApiError:
    """上游平台请求失败。"""
    return ApiError(502, msg, detail)
