"""界面层冒烟测试（offscreen 模式，无需真实显示器）。"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

WORK = tempfile.mkdtemp(prefix="gitdesk-ui-")
os.environ["GITDESK_DATA_DIR"] = os.path.join(WORK, "appdata")

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

PASS, FAIL = [], []


def check(name: str, cond: bool, extra: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(f"  [{'PASS' if cond else 'FAIL'}] {name} {'' if cond else extra}")


def git(*args, cwd=None):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)


def make_repo() -> str:
    repo = os.path.join(WORK, "demo")
    git("init", "--initial-branch=main", repo)
    git("config", "user.name", "Tester", cwd=repo)
    git("config", "user.email", "tester@example.com", cwd=repo)
    os.makedirs(os.path.join(repo, "src"), exist_ok=True)
    with open(os.path.join(repo, "src", "main.py"), "w", encoding="utf-8") as fh:
        fh.write("print('hello')\n")
    with open(os.path.join(repo, "README.md"), "w", encoding="utf-8") as fh:
        fh.write("# demo\n")
    git("add", "-A", cwd=repo)
    git("commit", "-m", "初始提交", cwd=repo)
    git("remote", "add", "origin", "https://github.com/example/demo.git", cwd=repo)
    return repo


def main() -> int:
    from gitdesk.core.logger import setup_logging

    setup_logging()

    app = QApplication(sys.argv)

    from gitdesk.ui.main_window import MainWindow

    window = MainWindow()
    window.show()

    check("主窗口创建成功", window is not None)
    check("页面数量为 6", window.stack.count() == 6, str(window.stack.count()))
    check("导航按钮与页面一一对应",
          len(window.sidebar.buttons) == window.stack.count(),
          f"{len(window.sidebar.buttons)} vs {window.stack.count()}")

    for index in range(window.stack.count()):
        window.goto(index)
        app.processEvents()
        check(f"切换到页面 {index} 无异常", window.stack.currentIndex() == index)

    repo = make_repo()
    window.goto(1)
    app.processEvents()
    registered = window.repos_page._register_path(repo)
    check("导入本地仓库成功", registered)
    app.processEvents()

    detail = window.repos_page.detail
    check("详情面板已显示", detail.isVisible())
    check("详情面板绑定到正确路径", detail.path == os.path.abspath(repo), detail.path)

    # 等待后台任务（刷新状态 / 历史 / 分支）完成
    deadline = 0
    while window.tasks.busy and deadline < 200:
        app.processEvents()
        time.sleep(0.03)
        deadline += 1
    app.processEvents()
    time.sleep(0.1)
    app.processEvents()

    check("提交历史已渲染", detail.history_table.rowCount() == 1, str(detail.history_table.rowCount()))
    check("分支列表已渲染", detail.local_table.rowCount() >= 1, str(detail.local_table.rowCount()))
    check("远程列表已渲染", detail.remote_list.rowCount() == 1, str(detail.remote_list.rowCount()))
    check(
        "概览显示远程地址",
        "github.com" in detail.ov_labels["remotes"].text(),
        detail.ov_labels["remotes"].text(),
    )
    check("识别当前分支", detail.branch_badge.text() == "main", detail.branch_badge.text())

    # 制造一个未提交更改，验证变更检测渲染
    with open(os.path.join(repo, "README.md"), "a", encoding="utf-8") as fh:
        fh.write("\nchange\n")
    with open(os.path.join(repo, "new_file.txt"), "w", encoding="utf-8") as fh:
        fh.write("x")
    from gitdesk.core.gitengine import GitEngine

    status = GitEngine.status(repo)
    detail._status = status
    detail._signature = ""
    detail._render_changes()
    check("变更树根节点为 2 组", detail.change_tree.topLevelItemCount() == 2, str(detail.change_tree.topLevelItemCount()))
    check("变更摘要包含文件数", "2" in detail.change_summary.text(), detail.change_summary.text())

    # 概览渲染
    from gitdesk.core.gitengine import SyncState

    detail._sync = SyncState()
    detail._render_overview()
    check("概览工作区状态已更新", "未暂存" in detail.ov_labels["dirty"].text(), detail.ov_labels["dirty"].text())

    # 预警提示：分叉场景
    detail._sync = SyncState(ahead=1, behind=2, has_upstream=True, upstream="refs/remotes/origin/main")
    detail._check_notice()
    check(
        "分叉时显示冲突预警",
        detail.notice.isVisible() and "分叉" in detail.notice.text(),
        detail.notice.text(),
    )
    detail._sync = SyncState()
    detail._check_notice()
    check("无风险时提示自动隐藏", not detail.notice.isVisible(), detail.notice.text())

    # 日志页
    window.goto(3)
    app.processEvents()
    check("日志视图可写入", len(window.logs_page.view.toPlainText()) > 0)

    # 设置页
    window.goto(4)
    app.processEvents()
    window.settings_page.watch_check.setChecked(False)
    window.settings_page.save()
    check("设置保存生效", window.cfg.get_setting("watch_enabled") is False)
    window.settings_page.watch_check.setChecked(True)
    window.settings_page.save()

    # 账号页
    window.goto(2)
    app.processEvents()
    check("账号卡片存在", window.accounts_page.github_card is not None)

    window.repos_page.shutdown()
    window.close()
    app.processEvents()
    return 0


if __name__ == "__main__":
    code = main()
    print("\n" + "=" * 60)
    print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
    for item in FAIL:
        print("  ✗", item)
    sys.exit(0 if not FAIL else 1)
