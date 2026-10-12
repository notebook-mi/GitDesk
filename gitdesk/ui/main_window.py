"""主窗口：侧边导航 + 页面容器 + 全局状态提示。"""

from __future__ import annotations

import os

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from .. import __version__
from ..core import platapi
from ..core.config import Config, RepoStore
from ..core.logger import get_logger
from .pages.accounts import AccountsPage
from .pages.explore import ExplorePage
from .pages.logs import LogsPage
from .pages.repos import ReposPage
from .pages.settings import SettingsPage
from .pages.trending import TrendingPage
from .style import PALETTE, STYLESHEET, apply_light_theme
from .widgets import StatusDot, Toast
from .workers import TaskCenter, shutdown_all

log = get_logger("ui")


class Sidebar(QFrame):
    """左侧导航栏。"""

    navigate = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Sidebar")
        self.setFixedWidth(228)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 20, 16, 18)
        layout.setSpacing(6)

        brand = QLabel("GitDesk")
        brand.setObjectName("AppTitle")
        layout.addWidget(brand)
        sub = QLabel("GitHub · Gitee 仓库管理器")
        sub.setObjectName("AppSubtitle")
        layout.addWidget(sub)
        layout.addSpacing(16)

        self.buttons: list[QPushButton] = []
        for index, (text, icon) in enumerate(
            [
                ("远程仓库", "☁"),
                ("本地仓库", "▣"),
                ("账号设置", "⚿"),
                ("操作日志", "☰"),
                ("设置", "⚙"),
                ("GitHub 热门", "✦"),
            ]
        ):
            button = QPushButton(f"  {icon}   {text}")
            button.setObjectName("NavButton")
            button.setCheckable(True)
            button.setAutoExclusive(True)
            button.setCursor(Qt.PointingHandCursor)
            button.clicked.connect(lambda _=False, i=index: self.navigate.emit(i))
            layout.addWidget(button)
            self.buttons.append(button)
        self.buttons[0].setChecked(True)

        layout.addStretch(1)

        # 连接状态
        status_title = QLabel("连接状态")
        status_title.setObjectName("Faint")
        layout.addWidget(status_title)
        self.chips: dict[str, tuple[StatusDot, QLabel]] = {}
        for key, meta in platapi.PLATFORMS.items():
            row = QHBoxLayout()
            row.setSpacing(7)
            row.setContentsMargins(2, 0, 0, 0)
            dot = StatusDot()
            name = QLabel(meta.label)
            name.setObjectName("Muted")
            row.addWidget(dot)
            row.addWidget(name)
            row.addStretch(1)
            container = QWidget()
            container.setLayout(row)
            layout.addWidget(container)
            self.chips[key] = (dot, name)

        layout.addSpacing(8)
        version = QLabel(f"版本 {__version__} · 内置 Git 引擎")
        version.setObjectName("AppSubtitle")
        layout.addWidget(version)

    def set_active(self, index: int) -> None:
        for i, button in enumerate(self.buttons):
            button.setChecked(i == index)

    def update_connections(self, cfg: Config) -> None:
        for key, (dot, label) in self.chips.items():
            if cfg.is_connected(key):
                dot.set_color(PALETTE["success"])
                label.setStyleSheet(f"color: {PALETTE['text']};")
            else:
                dot.set_color("#c9d1d9")
                label.setStyleSheet(f"color: {PALETTE['faint']};")


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        # 锁定浅色主题：深色系统主题会让按调色板自绘的控件变黑（如瀑布流背景）
        apply_light_theme()
        self.setWindowTitle(f"GitDesk · GitHub / Gitee 仓库管理器 v{__version__}")
        self.resize(1280, 820)
        self.setMinimumSize(1040, 680)
        self.setStyleSheet(STYLESHEET)

        self.cfg = Config()
        self.store = RepoStore()
        self.tasks = TaskCenter(self)

        central = QWidget()
        central.setObjectName("AppBody")
        central.setAttribute(Qt.WA_StyledBackground, True)
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.sidebar = Sidebar()
        self.sidebar.navigate.connect(self.goto)
        root.addWidget(self.sidebar)

        self.stack = QStackedWidget()
        self.stack.setObjectName("PageStack")
        self.stack.setAttribute(Qt.WA_StyledBackground, True)
        self.accounts_page = AccountsPage(self.cfg, self.tasks)
        self.explore_page = ExplorePage(self.cfg, self.store, self.tasks)
        self.repos_page = ReposPage(self.cfg, self.store, self.tasks)
        self.logs_page = LogsPage()
        self.settings_page = SettingsPage(self.cfg)
        self.trending_page = TrendingPage(self.cfg, self.store, self.tasks)

        for page in (
            self.explore_page,
            self.repos_page,
            self.accounts_page,
            self.logs_page,
            self.settings_page,
            self.trending_page,
        ):
            self.stack.addWidget(page)
        root.addWidget(self.stack, 1)
        self.setCentralWidget(central)

        # 页面索引：供 goto() 与外部跳转复用，避免散落的魔法数字
        self.page_index = {
            "explore": 0,
            "repos": 1,
            "accounts": 2,
            "logs": 3,
            "settings": 4,
            "trending": 5,
        }

        self.toast = Toast(central)

        # ---- 状态栏 ----
        bar = QStatusBar()
        self.setStatusBar(bar)
        self.status_label = QLabel("就绪")
        self.busy_label = QLabel("")
        self.busy_label.setObjectName("Faint")
        bar.addWidget(self.status_label, 1)
        bar.addPermanentWidget(self.busy_label)

        # ---- 信号连接 ----
        self.accounts_page.accounts_changed.connect(self._on_accounts_changed)
        self.explore_page.repo_cloned.connect(self._on_repo_cloned)
        self.explore_page.notify.connect(self.notify)
        self.repos_page.set_notifier(self.notify)
        self.repos_page.request_accounts.connect(lambda: self.goto(self.page_index["accounts"]))
        self.trending_page.repo_cloned.connect(self._on_repo_cloned)
        self.trending_page.notify.connect(self.notify)
        self.trending_page.request_explore.connect(
            lambda: self.goto(self.page_index["explore"])
        )
        self.settings_page.saved.connect(self._on_settings_saved)
        self.tasks.busy_changed.connect(self._on_busy_changed)

        self._initialised = False
        self.goto(0)
        self._initialised = True
        self.sidebar.update_connections(self.cfg)

        if not (self.cfg.is_connected("github") or self.cfg.is_connected("gitee")):
            self.goto(self.page_index["accounts"])
            self.notify("请先在「账号设置」中完成 GitHub 或 Gitee 的授权配置。", "info", 6000)
        self._restore_geometry()

    # ------------------------------------------------------------------ #

    def goto(self, index: int) -> None:
        self.stack.setCurrentIndex(index)
        self.sidebar.set_active(index)
        page = self.stack.currentWidget()
        if page is self.explore_page:
            self.explore_page.reload()
        elif page is self.repos_page:
            self.repos_page.reload_settings()
        elif page is self.accounts_page:
            self.accounts_page.reload()
        elif page is self.settings_page:
            self.settings_page.load_values()
        elif page is self.trending_page:
            self.trending_page.auto_load()
        titles = ["远程仓库", "本地仓库", "账号设置", "操作日志", "设置", "GitHub 热门"]
        self.status_label.setText(f"当前位置：{titles[index]}")

    def goto_key(self, key: str) -> None:
        """按名称跳转页面，避免依赖硬编码索引。"""
        index = self.page_index.get(key)
        if index is not None:
            self.goto(index)

    def notify(self, message: str, level: str = "info", timeout_ms: int = 4200) -> None:
        self.toast.show_message(message, level, timeout_ms)
        self.status_label.setText(message)

    # ------------------------------------------------------------------ #

    def _on_accounts_changed(self) -> None:
        self.sidebar.update_connections(self.cfg)
        self.notify("账号配置已更新。", "success")

    def _on_repo_cloned(self, path: str) -> None:
        self.repos_page.add_cloned(path)
        self.goto_key("repos")
        self.notify(f"已克隆并纳入管理：{os.path.basename(path)}", "success")

    def _on_settings_saved(self) -> None:
        self.repos_page.reload_settings()
        self.notify("设置已保存。", "success")

    def _on_busy_changed(self, busy: bool, label: str) -> None:
        self.busy_label.setText(f"⏳ {label}…" if busy else "")

    # ------------------------------------------------------------------ #

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if self.toast.isVisible():
            self.toast._reposition()

    def _restore_geometry(self) -> None:
        geometry = self.cfg.get_setting("window_geometry", "")
        if isinstance(geometry, str) and geometry:
            parts = geometry.split(",")
            if len(parts) == 4:
                try:
                    x, y, w, h = (int(p) for p in parts)
                    self.setGeometry(x, y, max(1040, w), max(680, h))
                except ValueError:
                    pass

    def closeEvent(self, event) -> None:  # noqa: N802
        try:
            self.repos_page.shutdown()
            self.logs_page.shutdown()
            rect = self.geometry()
            self.cfg.set_setting(
                "window_geometry", f"{rect.x()},{rect.y()},{rect.width()},{rect.height()}"
            )
            log.info("GitDesk 退出")
            shutdown_all(6000)
        except Exception as exc:  # noqa: BLE001
            log.warning("退出清理时出现异常: %s", exc)
        super().closeEvent(event)
