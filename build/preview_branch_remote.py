"""离屏渲染「分支」页签，用于出交付截图：选中远程分支 + 删除按钮。

用法：python build/preview_branch_remote.py <输出 png>
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

WORK = tempfile.mkdtemp(prefix="gitdesk-preview-")
os.environ["GITDESK_DATA_DIR"] = os.path.join(WORK, "appdata")
OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join("build", "preview-delete-remote.png")


def git(*args, cwd=None):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)


def build_repo() -> str:
    base = os.path.join(WORK, "demo")
    bare = os.path.join(base, "remote.git")
    repo = os.path.join(base, "repo")
    os.makedirs(base, exist_ok=True)
    git("init", "--bare", "--initial-branch=main", bare)
    git("clone", bare, repo)
    git("config", "user.name", "GitDesk")
    git("config", "user.email", "dev@gitdesk.local", cwd=repo)
    with open(os.path.join(repo, "app.py"), "w", encoding="utf-8") as fh:
        fh.write("print('hello')\n")
    git("add", "-A", cwd=repo)
    git("commit", "-m", "初始化项目", cwd=repo)
    git("push", "-u", "origin", "main", cwd=repo)
    for name, text in (("feature/分支图", "graph"), ("feature/login", "login")):
        git("checkout", "-b", name, cwd=repo)
        with open(os.path.join(repo, f"{text}.py"), "w", encoding="utf-8") as fh:
            fh.write(f"# {text}\n")
        git("add", "-A", cwd=repo)
        git("commit", "-m", f"实现 {text}", cwd=repo)
        git("push", "origin", name, cwd=repo)
        git("checkout", "main", cwd=repo)
    git("branch", "feature/login", cwd=repo)
    git("fetch", "origin", cwd=repo)
    return repo


def main() -> int:
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    from gitdesk.core.logger import setup_logging
    from gitdesk.ui.main_window import MainWindow

    setup_logging()
    app = QApplication(sys.argv)
    window = MainWindow()
    window.resize(1240, 860)
    window.setAttribute(Qt.WA_DontShowOnScreen, True)
    window.show()

    window.goto_key("repos")
    app.processEvents()
    window.repos_page._register_path(build_repo())
    detail = window.repos_page.detail
    for _ in range(400):
        app.processEvents()
        if not window.tasks.busy:
            break
        time.sleep(0.02)

    for i in range(detail.tabs.count()):
        if detail.tabs.tabText(i) == "分支":
            detail.tabs.setCurrentIndex(i)
            break
    for _ in range(400):
        app.processEvents()
        if not window.tasks.busy:
            break
        time.sleep(0.02)
    app.processEvents()

    target = -1
    for r in range(detail.remote_table.rowCount()):
        item = detail.remote_table.item(r, 0)
        if item and "feature/login" in item.text():
            target = r
            break
    print("远程行数:", detail.remote_table.rowCount(), "命中行:", target)
    if target >= 0:
        detail.remote_table.setCurrentCell(target, 0)
        detail._branch_source = "remote"
        detail.btn_branch_del.setFocus()
    app.processEvents()
    time.sleep(0.3)
    app.processEvents()

    print("识别结果:", detail._selected_branch())
    window.grab().save(OUT)
    print("saved", OUT)
    window.repos_page.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
