"""启动器 —— 默认 granian + 平台首选事件循环（Windows: winloop / Unix: rloop）。

用法（在仓库根目录执行）：

    uv run python scripts/serve.py                    # Windows: granian+winloop；Unix: granian+rloop
    uv run python scripts/serve.py --server uvicorn   # Unix 默认 uvloop，Windows 默认 asyncio
    uv run python scripts/serve.py --loop rloop       # 显式指定（平台/安装不支持时直接报错）
    uv run python scripts/serve.py --loop uvloop
    uv run python scripts/serve.py --loop winloop

事件循环可用性（与依赖的平台 marker 一致）：
    asyncio   始终可用
    rloop     rloop 包，仅 Unix（Rust 事件循环）
    uvloop    uvloop 包，仅 Unix
    winloop   winloop 包，仅 Windows（uvloop 的 Windows 实现）

`auto` 在启动器内解析成具体组合，不交给服务器：
    服务器：Windows → granian；Unix → granian+rloop，rloop 不可用时退回 uvicorn+uvloop
    循环：  granian → Windows winloop / Unix rloop→uvloop→asyncio；
            uvicorn → Unix uvloop→asyncio / Windows asyncio

环境变量（参数优先）：SERVER / LOOP / HOST / PORT / WORKERS / RELOAD。
PORT 默认读取 .env 后的 16690；HOST 默认 0.0.0.0（局域网播放器可直连）。
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import __version__, config  # noqa: E402

APP_TARGET = "app.main:app"
SERVERS = ("auto", "uvicorn", "granian")
LOOPS = ("auto", "asyncio", "rloop", "uvloop", "winloop")

#: 每个循环对应的可导入包（asyncio 无依赖）
LOOP_PACKAGES = {"rloop": "rloop", "uvloop": "uvloop", "winloop": "winloop"}
IS_WINDOWS = sys.platform.startswith("win")


def has_module(name: str) -> bool:
    """模块是否可导入（不实际导入，避免触发事件循环初始化）。"""
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def pick_server(requested: str) -> str:
    """auto：平台默认组合。

    Windows：granian（+winloop）；Unix：granian+rloop，rloop 不可用时退回 uvicorn+uvloop，
    最后才回退到已安装的服务器（循环由 default_loop 再降级）。
    """
    if requested not in SERVERS:
        raise SystemExit(f"不支持的 server: {requested}（可选：{', '.join(SERVERS)}）")
    if requested != "auto":
        return requested
    if IS_WINDOWS:
        return "granian" if has_module("granian") else "uvicorn"
    if has_module("granian") and has_module("rloop"):
        return "granian"
    if has_module("uvicorn") and has_module("uvloop"):
        return "uvicorn"
    return "granian" if has_module("granian") else "uvicorn"


def available_loops(server: str) -> list[str]:
    """当前平台/安装状态下该服务器可用的事件循环（含 auto）。"""
    loops = ["auto", "asyncio"]
    if server == "granian":
        loops.extend(name for name in ("rloop", "uvloop", "winloop") if has_module(LOOP_PACKAGES[name]))
    elif has_module("uvloop"):
        loops.append("uvloop")
    return loops


def default_loop(server: str) -> str:
    """平台默认循环：granian = Windows winloop / Unix rloop；uvicorn = Unix uvloop / Windows asyncio。

    依赖未安装（例如只用 uvloop 时没装 rloop）则按优先级回退，直到 asyncio。
    """
    if server == "granian":
        order = ("winloop", "asyncio") if IS_WINDOWS else ("rloop", "uvloop", "asyncio")
    else:
        order = ("asyncio",) if IS_WINDOWS else ("uvloop", "asyncio")
    for loop in order:
        if loop == "asyncio":
            return loop
        package = LOOP_PACKAGES.get(loop)
        if package and has_module(package):
            return loop
    return "asyncio"


def resolve_loop(server: str, loop: str) -> str:
    """auto → 平台默认循环；显式请求不可用时给出可操作的原因（不做静默降级）。"""
    if loop not in LOOPS:
        raise SystemExit(f"不支持的 loop: {loop}（可选：{', '.join(LOOPS)}）")
    if loop == "auto":
        return default_loop(server)
    if server == "uvicorn":
        if loop == "asyncio":
            return loop
        if loop == "uvloop":
            if not has_module("uvloop"):
                raise SystemExit("uvloop 未安装（仅 Unix 可用）：uv sync 或改用 --loop auto")
            return loop
        raise SystemExit(f"uvicorn 不支持 --loop {loop}；请改用 --server granian（granian 支持 rloop/uvloop/winloop）")

    package = LOOP_PACKAGES.get(loop)
    if package and not has_module(package):
        hint = "仅 Windows 可用" if loop == "winloop" else "仅 Unix 可用"
        raise SystemExit(
            f"granian 的 {loop} 事件循环不可用（{package} 未安装，{hint}）。可用：{', '.join(available_loops(server))}"
        )
    return loop


def _env_bool(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def build_command(
    server: str,
    *,
    host: str,
    port: int,
    workers: int,
    loop: str,
    reload: bool,
) -> list[str]:
    command = [sys.executable, "-m", server]
    if server == "granian":
        command += ["--interface", "asgi", "--host", host, "--port", str(port)]
        command += ["--workers", str(workers), "--loop", loop]
        if reload:
            command.append("--reload")
        if reload and not has_module("watchfiles"):
            print("[serve] 警告：granian --reload 需要 watchfiles（uv sync 已包含）", flush=True)
        command.append(APP_TARGET)
    else:
        command += [APP_TARGET, "--host", host, "--port", str(port)]
        command += ["--workers", str(workers), "--loop", loop]
        if reload:
            command.append("--reload")
    return command


def _arg_or_env(value: str | None, env_name: str, default: str | None = None) -> str | None:
    return value if value is not None else os.environ.get(env_name, default)


def main(argv: list[str] | None = None) -> int:
    config.load_env()

    parser = argparse.ArgumentParser(description="SodaServer 启动器（uvicorn / granian）")
    parser.add_argument("--server", choices=SERVERS, help="HTTP 服务器（默认 auto：优先 granian）")
    parser.add_argument("--loop", choices=LOOPS, help="事件循环（默认 auto）")
    parser.add_argument("--host", help="监听地址（默认 0.0.0.0）")
    parser.add_argument("--port", type=int, help="端口（默认 PORT 或 16690）")
    parser.add_argument("--workers", type=int, help="worker 进程数（默认 1）")
    parser.add_argument("--reload", action="store_true", help="开发热重载（强制单 worker）")
    args = parser.parse_args(argv)

    requested_server = str(_arg_or_env(args.server, "SERVER", "auto"))
    server = pick_server(requested_server)
    if not has_module(server):
        raise SystemExit(f"未安装 {server}：uv sync（或 uv add {server}）")
    if requested_server == "auto" and server == "uvicorn":
        reason = "未安装 granian" if not has_module("granian") else "rloop 不可用"
        print(f"[serve] 默认组合回退 uvicorn（{reason}）；uv sync 会安装 granian+rloop 默认栈", flush=True)

    loop = resolve_loop(server, str(_arg_or_env(args.loop, "LOOP", "auto")))
    host = str(_arg_or_env(args.host, "HOST", "0.0.0.0"))
    port = args.port or config.port()
    workers = args.workers or int(os.environ.get("WORKERS") or 1)
    reload = args.reload or _env_bool("RELOAD")

    if reload and workers > 1:
        print(f"[serve] --reload 与多 worker 不兼容，workers 从 {workers} 改为 1", flush=True)
        workers = 1

    command = build_command(server, host=host, port=port, workers=workers, loop=loop, reload=reload)
    print(
        f"[serve] SodaServer {__version__} | {server} | loop={loop} | http://{host}:{port} "
        f"| workers={workers}{' | reload' if reload else ''}",
        flush=True,
    )
    try:
        return subprocess.call(command)
    except KeyboardInterrupt:  # Ctrl+C 由子进程自行处理，父进程安静退出
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
