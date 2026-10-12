"""工作区目录结构读取。

「项目结构树」只读取磁盘上的工作区目录，不做任何 Git 对象访问，
因此可以独立于仓库状态运行，也便于单独测试。

约定：
* 始终跳过 ``.git`` 目录（版本库元数据，不属于「项目结构」）；
* 默认按「目录在前、文件在后，同类按名称排序」输出，便于阅读；
* 通过 ``max_entries`` 兜底，避免超大仓库把界面卡死。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

#: 目录名黑名单（不参与结构展示）
SKIP_DIRS = {".git"}

#: 单次扫描的条目上限，超出后截断并在结果中标记
DEFAULT_MAX_ENTRIES = 5000


@dataclass
class Entry:
    """一个目录项。"""

    name: str
    rel: str
    is_dir: bool
    size: int = 0
    child_count: int = 0
    readable: bool = True

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "rel": self.rel,
            "is_dir": self.is_dir,
            "size": self.size,
            "child_count": self.child_count,
            "readable": self.readable,
        }


@dataclass
class TreeStats:
    """整棵目录树的汇总。"""

    dirs: int = 0
    files: int = 0
    size: int = 0
    truncated: bool = False
    unreadable: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return self.dirs + self.files


def _join_rel(rel: str, name: str) -> str:
    return f"{rel}/{name}" if rel else name


def _sorted_entries(raw: list[Entry]) -> list[Entry]:
    """目录在前，文件在后；同类按名称不区分大小写排序。"""
    return sorted(raw, key=lambda e: (not e.is_dir, e.name.lower()))


def list_dir(
    root: str,
    rel: str = "",
    *,
    show_hidden: bool = True,
    dirs_only: bool = False,
    keyword: str = "",
    max_entries: int = DEFAULT_MAX_ENTRIES,
) -> list[dict]:
    """列出 ``root/rel`` 下的一级条目。

    参数
    ----
    show_hidden: 是否显示以 ``.`` 开头的隐藏项（``.git`` 始终隐藏）
    dirs_only:   只返回目录
    keyword:     名称包含该关键字（不区分大小写）才返回
    """
    base = os.path.join(root, rel) if rel else root
    raw: list[Entry] = []
    if not base or not os.path.isdir(base):
        return []
    lowered = keyword.strip().lower()
    try:
        with os.scandir(base) as scanner:
            for item in scanner:
                if len(raw) >= max_entries:
                    break
                name = item.name
                if name in SKIP_DIRS:
                    continue
                if not show_hidden and name.startswith("."):
                    continue
                try:
                    is_dir = item.is_dir(follow_symlinks=False)
                except OSError:
                    is_dir = False
                if dirs_only and not is_dir:
                    continue
                if lowered and lowered not in name.lower():
                    continue
                size = 0
                child_count = 0
                readable = True
                if is_dir:
                    try:
                        with os.scandir(item.path) as inner:
                            child_count = sum(1 for _ in inner)
                    except OSError:
                        readable = False
                else:
                    try:
                        size = item.stat(follow_symlinks=False).st_size
                    except OSError:
                        readable = False
                raw.append(
                    Entry(
                        name=name,
                        rel=_join_rel(rel, name),
                        is_dir=is_dir,
                        size=size,
                        child_count=child_count,
                        readable=readable,
                    )
                )
    except OSError:
        return []
    return [entry.as_dict() for entry in _sorted_entries(raw)]


def summarize(
    root: str,
    rel: str = "",
    *,
    max_entries: int = DEFAULT_MAX_ENTRIES,
    _state: dict | None = None,
) -> dict:
    """递归统计 ``root/rel`` 下的目录数、文件数与总大小。

    超出 ``max_entries`` 时停止遍历并标记 ``truncated``。
    """
    state = _state if _state is not None else {"dirs": 0, "files": 0, "size": 0, "seen": 0}
    base = os.path.join(root, rel) if rel else root
    if not base or not os.path.isdir(base):
        return {"dirs": 0, "files": 0, "size": 0, "truncated": False}
    try:
        with os.scandir(base) as scanner:
            for item in scanner:
                if item.name in SKIP_DIRS:
                    continue
                if state["seen"] >= max_entries:
                    break
                state["seen"] += 1
                try:
                    if item.is_dir(follow_symlinks=False):
                        state["dirs"] += 1
                        summarize(root, _join_rel(rel, item.name), max_entries=max_entries, _state=state)
                    else:
                        state["files"] += 1
                        try:
                            state["size"] += item.stat(follow_symlinks=False).st_size
                        except OSError:
                            pass
                except OSError:
                    continue
    except OSError:
        pass
    return {
        "dirs": state["dirs"],
        "files": state["files"],
        "size": state["size"],
        "truncated": state["seen"] >= max_entries,
    }


def filter_tree(
    root: str,
    keyword: str,
    *,
    dirs_only: bool = False,
    show_hidden: bool = True,
    max_entries: int = DEFAULT_MAX_ENTRIES,
    max_depth: int = 10,
) -> list[dict]:
    """按关键字递归过滤，返回「命中项 + 其祖先目录」构成的子树。

    与 ``list_dir(keyword=...)`` 只过滤当前层不同，这里做的是全局搜索：
    输入 ``main`` 时即使 ``main.py`` 藏在 ``src/`` 下也能被找到，
    父目录会作为路径保留并标记 ``force_expand``。
    """
    needle = (keyword or "").strip().lower()
    if not needle:
        return []
    budget = {"n": 0}

    def walk(rel: str, depth: int) -> list[dict]:
        if depth > max_depth:
            return []
        result: list[dict] = []
        for entry in list_dir(root, rel, show_hidden=show_hidden, max_entries=max_entries):
            if budget["n"] >= max_entries:
                break
            budget["n"] += 1
            hit = needle in str(entry.get("name", "")).lower()
            if not entry.get("is_dir"):
                if hit and not dirs_only:
                    result.append({**entry, "children": []})
                continue
            children = walk(str(entry.get("rel", "")), depth + 1)
            if hit or children:
                result.append({**entry, "children": children, "force_expand": True})
        return result

    return walk("", 0)


def format_size(size: int | float) -> str:
    """人类可读的文件大小。"""
    try:
        value = float(size)
    except (TypeError, ValueError):
        return "—"
    if value <= 0:
        return "—"
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024:
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{value:.1f} TB"


__all__ = [
    "Entry",
    "TreeStats",
    "SKIP_DIRS",
    "list_dir",
    "filter_tree",
    "summarize",
    "format_size",
]
