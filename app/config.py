"""环境变量与路径配置。

对应原 src/env.ts：读取项目根目录 .env，已存在的真实环境变量优先。
必须在任何读取 os.environ 的模块导入之前调用 load_env()（main.py 第一行）。
"""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _parse_line(line: str) -> tuple[str, str] | None:
    line = line.strip()
    if not line or line.startswith("#"):
        return None
    if line.startswith("export "):
        line = line[7:].strip()
    key, sep, value = line.partition("=")
    if not sep:
        return None
    key = key.strip()
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1]
    return (key, value) if key else None


def load_env(path: Path | None = None) -> None:
    """加载 .env（不覆盖已存在的环境变量）；缺失或解析失败时静默跳过。"""
    env_path = path or ROOT / ".env"
    if not env_path.exists():
        return
    try:
        text = env_path.read_text(encoding="utf-8")
    except OSError as err:
        print(f"[env] .env 读取失败，已忽略: {err}")
        return
    for line in text.splitlines():
        parsed = _parse_line(line)
        if parsed:
            os.environ.setdefault(parsed[0], parsed[1])


def data_dir() -> Path:
    """凭据存储目录（DATA_DIR 可覆盖，测试用）。"""
    custom = os.environ.get("DATA_DIR")
    return Path(custom) if custom else ROOT / "data"


def port() -> int:
    """默认端口 16690：避开 MusicServer 常驻实例（16688）与 qs-signer/mssdk-sign（8899/16689/16889）。"""
    return int(os.environ.get("PORT") or 16690)


def auth_key() -> str:
    return os.environ.get("AUTH_KEY") or ""


def rate_limit() -> int:
    """每 IP 每分钟请求数，0 关闭（默认 120）。"""
    raw = os.environ.get("RATE_LIMIT")
    return 120 if raw is None or raw == "" else int(raw)
