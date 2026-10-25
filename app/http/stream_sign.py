"""流代理签名 —— /stream/* 的无头客户端兼容方案。

/url 返回流地址时附加 `?exp=<秒级时间戳>&sig=<HMAC-SHA256(authKey, "path:exp") 前 32 位>`。
与裸 ?key= 的区别：签名值泄露只能在该时效窗口内拉这一条流，不能反推出秘钥、不能调用其它接口。
"""

from __future__ import annotations

import hashlib
import hmac
import time

# 流地址有效期（秒），与 qs 解密流缓存 TTL（10 分钟）对齐；过期后客户端重新 /url 即可
STREAM_SIGN_TTL = 10 * 60


def _sign(auth_key: str, data: str) -> str:
    return hmac.new(auth_key.encode(), data.encode(), hashlib.sha256).hexdigest()[:32]


def sign_stream(auth_key: str, path: str) -> str:
    """生成流地址的签名参数（path 为不含 query 的路径，如 /stream/qs/123）。"""
    exp = int(time.time() + STREAM_SIGN_TTL)
    return f"exp={exp}&sig={_sign(auth_key, f'{path}:{exp}')}"


def verify_stream_sign(auth_key: str, path: str, exp: object, sig: object) -> bool:
    """校验流地址签名（常量时间比较 + 时效窗口）。"""
    try:
        exp_num = int(float(str(exp)))
    except (TypeError, ValueError, OverflowError):
        return False
    sig_str = str(sig or "")
    if not sig_str:
        return False
    if time.time() > exp_num:  # 过期即拒（客户端重新 /url 取新地址）
        return False
    return hmac.compare_digest(_sign(auth_key, f"{path}:{exp_num}"), sig_str)
