"""全局限流 —— per-IP 滑动窗口（内存实现，无额外依赖）。

RATE_LIMIT 环境变量：每 IP 每分钟允许的请求数（默认 120；设 0 关闭）。
在鉴权之前计数（401 爆破秘钥同样受限）；超限返回 429 + Retry-After。
"""

from __future__ import annotations

import time

from starlette.requests import Request
from starlette.responses import JSONResponse

WINDOW_SECONDS = 60.0


def make_rate_limiter(limit_per_minute: int):
    hits: dict[str, list[float]] = {}
    last_cleanup = 0.0

    async def middleware(request: Request, call_next):
        nonlocal last_cleanup
        if request.method == "OPTIONS" or limit_per_minute <= 0:
            return await call_next(request)

        now = time.monotonic()
        # 惰性清理：每窗口顺带删除已过期的 IP 记录，防内存无限增长
        if now - last_cleanup > WINDOW_SECONDS:
            threshold = now - WINDOW_SECONDS
            for ip in [ip for ip, times in hits.items() if not times or times[-1] <= threshold]:
                hits.pop(ip, None)
            last_cleanup = now

        ip = request.client.host if request.client else "unknown"
        times = [t for t in hits.get(ip, []) if t > now - WINDOW_SECONDS]
        times.append(now)
        hits[ip] = times
        if len(times) > limit_per_minute:
            retry_after = max(1, int(times[0] + WINDOW_SECONDS - now) + 1)
            return JSONResponse(
                {"code": 429, "msg": f"请求过于频繁（限 {limit_per_minute} 次/分钟），请 {retry_after} 秒后重试"},
                status_code=429,
                headers={"Retry-After": str(retry_after)},
            )
        return await call_next(request)

    return middleware
