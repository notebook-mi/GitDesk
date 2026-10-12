"""运行期路径解析：绿色优先，目录不可写时回退到 %APPDATA%。"""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "GitDesk"


def is_frozen() -> bool:
    """是否运行在 PyInstaller 打包后的 exe 中。"""
    return bool(getattr(sys, "frozen", False))


def app_root() -> Path:
    """程序所在目录（开发态为项目根目录）。"""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def resource_path(*parts: str) -> Path:
    """读取随程序分发的静态资源（打包后位于临时解包目录）。"""
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return Path(base).joinpath(*parts)
    return app_root().joinpath(*parts)


def _writable(p: Path) -> bool:
    try:
        p.mkdir(parents=True, exist_ok=True)
        probe = p / ".write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except Exception:
        return False


_DATA_CACHE: Path | None = None


def data_dir() -> Path:
    """数据根目录。可用环境变量 GITDESK_DATA_DIR 覆盖。"""
    global _DATA_CACHE
    if _DATA_CACHE is not None:
        return _DATA_CACHE

    env = os.environ.get("GITDESK_DATA_DIR")
    if env:
        p = Path(env).expanduser()
        p.mkdir(parents=True, exist_ok=True)
        _DATA_CACHE = p
        return p

    if is_frozen():
        portable = app_root() / f"{APP_NAME}Data"
        if _writable(portable):
            _DATA_CACHE = portable
            return portable

    base = os.environ.get("APPDATA") or str(Path.home())
    p = Path(base) / APP_NAME
    p.mkdir(parents=True, exist_ok=True)
    _DATA_CACHE = p
    return p


def log_dir() -> Path:
    p = data_dir() / "logs"
    p.mkdir(parents=True, exist_ok=True)
    return p


def config_file() -> Path:
    return data_dir() / "config.json"


def repos_file() -> Path:
    return data_dir() / "repos.json"


def default_clone_dir() -> Path:
    home = Path.home()
    for name in ("Documents", "\u6587\u6863"):
        candidate = home / name
        if candidate.is_dir():
            return candidate / "GitDeskRepos"
    return home / "GitDeskRepos"
