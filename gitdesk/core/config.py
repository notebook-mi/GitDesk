"""应用配置与本地仓库清单的持久化。"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from copy import deepcopy
from pathlib import Path
from typing import Any

from . import crypto
from .logger import get_logger
from .paths import config_file, default_clone_dir, repos_file

log = get_logger("config")

DEFAULT_CONFIG: dict[str, Any] = {
    "version": 1,
    "accounts": {
        "github": {"username": "", "token": "", "verified": False, "display": ""},
        "gitee": {"username": "", "token": "", "verified": False, "display": ""},
    },
    "settings": {
        "clone_dir": "",
        "page_size": 30,
        "watch_enabled": True,
        "watch_interval_ms": 5000,
        "proxy": "",
        "git_username": "GitDesk",
        "git_email": "gitdesk@localhost",
    },
}


def _atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


class Config:
    """配置文件读写；令牌字段以密文形式落盘。"""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or config_file()
        self._lock = threading.RLock()
        self._data = deepcopy(DEFAULT_CONFIG)
        self.load()

    # ---------- 基础 ----------

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> None:
        with self._lock:
            if not self._path.exists():
                self._apply_defaults()
                self.save()
                return
            try:
                raw = json.loads(self._path.read_text(encoding="utf-8"))
                if not isinstance(raw, dict):
                    raise ValueError("配置根节点必须是对象")
                merged = deepcopy(DEFAULT_CONFIG)
                for key, value in raw.items():
                    if key == "accounts" and isinstance(value, dict):
                        for platform, acc in value.items():
                            if platform in merged["accounts"] and isinstance(acc, dict):
                                merged["accounts"][platform].update(acc)
                    elif key == "settings" and isinstance(value, dict):
                        merged["settings"].update(value)
                    else:
                        merged[key] = value
                self._data = merged
            except Exception as exc:  # 配置损坏时重建，避免程序无法启动
                log.error("配置文件解析失败，已重建默认配置: %s", exc)
                self._data = deepcopy(DEFAULT_CONFIG)
            self._apply_defaults()

    def _apply_defaults(self) -> None:
        settings = self._data["settings"]
        if not settings.get("clone_dir"):
            settings["clone_dir"] = str(default_clone_dir())

    def save(self) -> None:
        with self._lock:
            try:
                _atomic_write_json(self._path, self._data)
            except Exception as exc:
                log.error("配置保存失败: %s", exc)

    # ---------- 账号 ----------

    def account(self, platform: str) -> dict:
        with self._lock:
            return dict(self._data["accounts"].get(platform, {}))

    def token(self, platform: str) -> str:
        return crypto.decrypt(self.account(platform).get("token", ""))

    def username(self, platform: str) -> str:
        return self.account(platform).get("username", "") or ""

    def is_connected(self, platform: str) -> bool:
        acc = self.account(platform)
        return bool(acc.get("verified") and acc.get("token"))

    def save_account(
        self,
        platform: str,
        token: str | None = None,
        username: str | None = None,
        verified: bool | None = None,
        display: str | None = None,
    ) -> None:
        with self._lock:
            acc = self._data["accounts"].setdefault(platform, {})
            if token is not None:
                acc["token"] = crypto.encrypt(token) if token else ""
            if username is not None:
                acc["username"] = username
            if verified is not None:
                acc["verified"] = verified
            if display is not None:
                acc["display"] = display
            self.save()

    def clear_account(self, platform: str) -> None:
        with self._lock:
            self._data["accounts"][platform] = deepcopy(
                DEFAULT_CONFIG["accounts"].get(platform, {})
            )
            self.save()

    # ---------- 设置 ----------

    def get_setting(self, key: str, default: Any = None) -> Any:
        with self._lock:
            return self._data["settings"].get(key, default)

    def set_setting(self, key: str, value: Any, save: bool = True) -> None:
        with self._lock:
            self._data["settings"][key] = value
            if save:
                self.save()

    def update_settings(self, values: dict) -> None:
        with self._lock:
            self._data["settings"].update(values)
            self.save()

    def as_dict(self) -> dict:
        with self._lock:
            return deepcopy(self._data)


class RepoStore:
    """本地已纳入管理的仓库清单。"""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or repos_file()
        self._lock = threading.RLock()
        self._items: list[dict] = []
        self.load()

    def load(self) -> None:
        with self._lock:
            if not self._path.exists():
                self._items = []
                return
            try:
                raw = json.loads(self._path.read_text(encoding="utf-8"))
                self._items = raw if isinstance(raw, list) else []
            except Exception as exc:
                log.error("仓库清单解析失败，已重置: %s", exc)
                self._items = []

    def save(self) -> None:
        with self._lock:
            try:
                _atomic_write_json(self._path, self._items)
            except Exception as exc:
                log.error("仓库清单保存失败: %s", exc)

    def all(self) -> list[dict]:
        with self._lock:
            return [dict(item) for item in self._items]

    def get(self, path: str) -> dict | None:
        key = os.path.normcase(os.path.abspath(path))
        with self._lock:
            for item in self._items:
                if os.path.normcase(os.path.abspath(item.get("path", ""))) == key:
                    return dict(item)
        return None

    def add(self, path: str, **extra) -> dict:
        record = {"path": os.path.abspath(path), "name": Path(path).name, "added_at": _now()}
        record.update(extra)
        with self._lock:
            existing = self.get(path)
            if existing:
                existing.update(extra)
                for idx, item in enumerate(self._items):
                    if os.path.normcase(os.path.abspath(item.get("path", ""))) == os.path.normcase(
                        os.path.abspath(path)
                    ):
                        self._items[idx] = existing
                self.save()
                return existing
            self._items.append(record)
            self.save()
            return record

    def update(self, path: str, **extra) -> None:
        key = os.path.normcase(os.path.abspath(path))
        with self._lock:
            for item in self._items:
                if os.path.normcase(os.path.abspath(item.get("path", ""))) == key:
                    item.update(extra)
                    break
            else:
                self._items.append({"path": os.path.abspath(path), "name": Path(path).name, **extra})
            self.save()

    def remove(self, path: str) -> None:
        key = os.path.normcase(os.path.abspath(path))
        with self._lock:
            self._items = [
                item
                for item in self._items
                if os.path.normcase(os.path.abspath(item.get("path", ""))) != key
            ]
            self.save()


def _now() -> str:
    import datetime

    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
