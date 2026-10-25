"""统一成功信封 { code: 0, data }。

独立成模块是为了避免 routes → main → routes 的循环导入。
"""

from __future__ import annotations

import json
from typing import Any

from fastapi.responses import JSONResponse


class Envelope(JSONResponse):
    """成功响应：{ "code": 0, "data": <payload> }。"""

    def render(self, content: Any) -> bytes:
        return json.dumps({"code": 0, "data": content}, ensure_ascii=False).encode("utf-8")


def ok(data: Any, status_code: int = 200) -> Envelope:
    return Envelope(data, status_code=status_code)
