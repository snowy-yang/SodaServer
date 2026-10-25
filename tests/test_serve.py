"""启动器（scripts/serve.py）的循环/服务器选择逻辑测试（不启动真实服务器）。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts import serve


def test_pick_server_auto_windows_prefers_granian(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(serve, "IS_WINDOWS", True)
    monkeypatch.setattr(serve, "has_module", lambda name: name in ("granian", "winloop"))
    assert serve.pick_server("auto") == "granian"

    monkeypatch.setattr(serve, "has_module", lambda name: False)
    assert serve.pick_server("auto") == "uvicorn"


def test_pick_server_auto_unix_prefers_granian_rloop(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(serve, "IS_WINDOWS", False)
    monkeypatch.setattr(serve, "has_module", lambda name: name in ("granian", "rloop", "uvloop", "uvicorn"))
    assert serve.pick_server("auto") == "granian"


def test_pick_server_auto_unix_falls_back_to_uvicorn_uvloop(monkeypatch: pytest.MonkeyPatch) -> None:
    # 例如 granian 已安装但 rloop 不可用：默认改为 uvicorn+uvloop
    monkeypatch.setattr(serve, "IS_WINDOWS", False)
    monkeypatch.setattr(serve, "has_module", lambda name: name in ("granian", "uvloop", "uvicorn"))
    assert serve.pick_server("auto") == "uvicorn"


def test_available_loops_follow_installed_packages(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(serve, "has_module", lambda name: name == "winloop")
    assert serve.available_loops("granian") == ["auto", "asyncio", "winloop"]
    assert serve.available_loops("uvicorn") == ["auto", "asyncio"]

    monkeypatch.setattr(serve, "has_module", lambda name: name in ("rloop", "uvloop"))
    assert serve.available_loops("granian") == ["auto", "asyncio", "rloop", "uvloop"]
    assert serve.available_loops("uvicorn") == ["auto", "asyncio", "uvloop"]


def test_explicit_unavailable_loop_fails_with_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(serve, "has_module", lambda name: False)
    with pytest.raises(SystemExit) as error:
        serve.resolve_loop("granian", "rloop")
    message = str(error.value)
    assert "rloop" in message and "可用" in message


def test_uvicorn_rejects_granian_only_loops() -> None:
    with pytest.raises(SystemExit):
        serve.resolve_loop("uvicorn", "winloop")
    with pytest.raises(SystemExit):
        serve.resolve_loop("uvicorn", "rloop")


def test_uvicorn_auto_and_asyncio_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(serve, "IS_WINDOWS", True)
    assert serve.resolve_loop("uvicorn", "auto") == "asyncio"
    assert serve.resolve_loop("uvicorn", "asyncio") == "asyncio"


def test_default_loop_granian_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(serve, "IS_WINDOWS", True)
    monkeypatch.setattr(serve, "has_module", lambda name: name == "winloop")
    assert serve.default_loop("granian") == "winloop"


def test_default_loop_granian_unix_prefers_rloop(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(serve, "IS_WINDOWS", False)
    monkeypatch.setattr(serve, "has_module", lambda name: name in ("rloop", "uvloop"))
    assert serve.default_loop("granian") == "rloop"


def test_default_loop_granian_unix_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(serve, "IS_WINDOWS", False)
    monkeypatch.setattr(serve, "has_module", lambda name: name == "uvloop")
    assert serve.default_loop("granian") == "uvloop"

    monkeypatch.setattr(serve, "has_module", lambda name: False)
    assert serve.default_loop("granian") == "asyncio"


def test_default_loop_uvicorn(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(serve, "IS_WINDOWS", False)
    monkeypatch.setattr(serve, "has_module", lambda name: name == "uvloop")
    assert serve.default_loop("uvicorn") == "uvloop"

    # Windows 上 uvicorn 只有 asyncio（即使装了 winloop 也不会用）
    monkeypatch.setattr(serve, "IS_WINDOWS", True)
    monkeypatch.setattr(serve, "has_module", lambda name: name == "winloop")
    assert serve.default_loop("uvicorn") == "asyncio"


def test_default_loop_windows_without_winloop(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(serve, "IS_WINDOWS", True)
    monkeypatch.setattr(serve, "has_module", lambda name: False)
    assert serve.default_loop("granian") == "asyncio"


def test_resolve_loop_auto_uses_platform_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(serve, "IS_WINDOWS", False)
    monkeypatch.setattr(serve, "has_module", lambda name: name in ("rloop", "uvloop"))
    assert serve.resolve_loop("granian", "auto") == "rloop"
    assert serve.resolve_loop("uvicorn", "auto") == "uvloop"


def test_build_command_granian(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(serve, "has_module", lambda name: True)
    command = serve.build_command("granian", host="0.0.0.0", port=16690, workers=2, loop="winloop", reload=True)
    assert command[:3] == [sys.executable, "-m", "granian"]
    assert command[command.index("--loop") + 1] == "winloop"
    assert command[command.index("--workers") + 1] == "2"
    assert "--reload" in command
    assert command[-1] == serve.APP_TARGET


def test_build_command_uvicorn() -> None:
    command = serve.build_command("uvicorn", host="127.0.0.1", port=9000, workers=1, loop="auto", reload=False)
    assert command[:3] == [sys.executable, "-m", "uvicorn"]
    assert command[3] == serve.APP_TARGET
    assert "--reload" not in command
