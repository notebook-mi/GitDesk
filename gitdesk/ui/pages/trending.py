"""GitHub 热门项目页：Top N 榜单以瀑布流卡片展示，简介统一翻译为中文。"""

from __future__ import annotations

import os

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...core import trending
from ...core.gitengine import Credential, GitEngine
from ...core.logger import get_logger
from ..masonry import MasonryView, RepoCard
from ..widgets import TransferPanel, confirm, open_url, show_error

log = get_logger("ui.trending")

LIMITS = ((10, "Top 10"), (15, "Top 15"), (20, "Top 20"), (30, "Top 30"))
GITHUB_TRENDING_URL = "https://github.com/trending"


class TrendingPage(QWidget):
    """GitHub 热门榜单（瀑布流）。"""

    repo_cloned = Signal(str)
    notify = Signal(str, str)   # (消息, 级别)
    request_explore = Signal()  # 需要用户先去设置克隆目录

    def __init__(self, cfg, store, tasks, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.cfg = cfg
        self.store = store
        self.tasks = tasks
        self._payload: dict | None = None
        self._task = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 16)
        layout.setSpacing(12)

        head = QHBoxLayout()
        head.setSpacing(10)
        title_box = QVBoxLayout()
        title_box.setSpacing(2)
        title = QLabel("GitHub 热门项目")
        title.setObjectName("H1")
        self.subtitle = QLabel(
            "实时抓取 GitHub 趋势榜与星标总榜，简介自动翻译为中文；卡片支持一键克隆到本地。"
        )
        self.subtitle.setObjectName("Faint")
        self.subtitle.setWordWrap(True)
        title_box.addWidget(title)
        title_box.addWidget(self.subtitle)
        head.addLayout(title_box, 1)
        self.open_board_btn = QPushButton("打开 GitHub 榜单")
        self.open_board_btn.setProperty("variant", "ghost")
        self.open_board_btn.clicked.connect(lambda: open_url(GITHUB_TRENDING_URL))
        head.addWidget(self.open_board_btn, 0, Qt.AlignTop)
        layout.addLayout(head)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)
        toolbar.addWidget(QLabel("榜单"))
        self.board_combo = QComboBox()
        for key, label in trending.BOARDS:
            self.board_combo.addItem(label, key)
        self.board_combo.setFixedWidth(190)
        self.board_combo.currentIndexChanged.connect(self._on_board_changed)
        toolbar.addWidget(self.board_combo)

        self.period_label = QLabel("时间")
        toolbar.addWidget(self.period_label)
        self.period_combo = QComboBox()
        for key, label in trending.PERIODS:
            self.period_combo.addItem(label, key)
        self.period_combo.setCurrentIndex(1)  # 默认本周
        self.period_combo.setFixedWidth(100)
        self.period_combo.currentIndexChanged.connect(lambda _: self.refresh())
        toolbar.addWidget(self.period_combo)

        toolbar.addWidget(QLabel("数量"))
        self.limit_combo = QComboBox()
        for value, label in LIMITS:
            self.limit_combo.addItem(label, value)
        self.limit_combo.setFixedWidth(100)
        toolbar.addWidget(self.limit_combo)

        self.translate_check = QCheckBox("简介翻译成中文")
        self.translate_check.setChecked(True)
        self.translate_check.setToolTip("自动识别语言并统一翻译为简体中文，结果会缓存到本地")
        toolbar.addWidget(self.translate_check)

        toolbar.addStretch(1)
        self.refresh_btn = QPushButton("刷新榜单")
        self.refresh_btn.setProperty("variant", "primary")
        self.refresh_btn.clicked.connect(self.refresh)
        toolbar.addWidget(self.refresh_btn)
        layout.addLayout(toolbar)

        self.masonry = MasonryView(spacing=14, min_card_width=318, max_columns=4)
        layout.addWidget(self.masonry, 1)

        # 下载详情：从榜单克隆同样是「下载」，与远程仓库页保持一致的进度 + 详情
        self.transfer = TransferPanel()
        layout.addWidget(self.transfer)

        footer = QHBoxLayout()
        footer.setSpacing(8)
        self.status_label = QLabel("点击「刷新榜单」加载 GitHub 热门项目。")
        self.status_label.setObjectName("Faint")
        self.status_label.setWordWrap(True)
        footer.addWidget(self.status_label, 1)
        self.meta_label = QLabel("")
        self.meta_label.setObjectName("Faint")
        footer.addWidget(self.meta_label)
        layout.addLayout(footer)

        self._on_board_changed()

    # ---------------- 交互 ---------------- #

    def _on_board_changed(self) -> None:
        is_stars = self.board_combo.currentData() == "stars"
        self.period_combo.setEnabled(not is_stars)
        self.period_label.setEnabled(not is_stars)
        self.period_combo.setToolTip(
            "星标总榜为历史累计，与时间范围无关" if is_stars else "趋势榜的时间范围"
        )

    def auto_load(self) -> None:
        """进入页面时若还没有数据则自动加载一次。"""
        if self._payload is None and not self._is_busy():
            self.refresh()

    def _is_busy(self) -> bool:
        return self._task is not None and self._task.isRunning()

    # ---------------- 数据加载 ---------------- #

    def refresh(self) -> None:
        if self._is_busy():
            self.status_label.setText("榜单加载中，请稍候…")
            return
        kind = self.board_combo.currentData()
        period = self.period_combo.currentData()
        limit = self.limit_combo.currentData() or 10
        self.refresh_btn.setEnabled(False)
        self.refresh_btn.setText("加载中…")
        self.status_label.setText("正在获取 GitHub 榜单…")
        log.info("开始加载 GitHub 榜单: %s/%s Top%s", kind, period, limit)

        self._task = self.tasks.run(
            trending.fetch_board,
            kind,
            period,
            limit=limit,
            token=self.cfg.token("github"),
            translate=bool(self.translate_check.isChecked()),
            with_avatars=True,
            on_progress=self._on_progress,
            on_success=self._render,
            on_error=self._on_error,
            label="加载 GitHub 热门榜单",
        )

    def _on_progress(self, text: str) -> None:
        self.status_label.setText(text)
        self.transfer.set_message(text[:200])

    def _on_error(self, message: str, detail: str) -> None:
        self._task = None
        self.refresh_btn.setEnabled(True)
        self.refresh_btn.setText("刷新榜单")
        self.status_label.setText(message)
        show_error(self, "加载失败", message, detail)

    def _render(self, payload: dict) -> None:
        self._task = None
        self.refresh_btn.setEnabled(True)
        self.refresh_btn.setText("刷新榜单")
        self._payload = payload
        items = payload.get("items") or []

        self.masonry.clear_cards()
        for index, repo in enumerate(items, start=1):
            card = RepoCard(repo, repo.get("rank") or index)
            card.open_requested.connect(self._on_open)
            card.clone_requested.connect(self.clone_repo)
            card.copy_requested.connect(self._on_copy)
            self.masonry.add_card(card)
        self.masonry.refresh_layout()

        if not items:
            self.status_label.setText("本次没有获取到任何项目，请稍后重试。")
            self.meta_label.setText("")
            return
        self.status_label.setText(
            f"已展示 {len(items)} 个项目 · {payload.get('translated_state', '')}"
            f" · 单击「查看详情」跳转到 GitHub 仓库页"
        )
        self.meta_label.setText(
            f"{payload.get('source_label', '')} · 抓取于 {payload.get('fetched_at', '')}"
        )
        log.info("榜单渲染完成: %d 条", len(items))

    # ---------------- 卡片动作 ---------------- #

    def _on_open(self, url: str) -> None:
        if not url:
            return
        open_url(url)
        self.notify.emit(f"已在浏览器中打开：{url}", "info")

    def _on_copy(self, url: str) -> None:
        if not url:
            return
        clipboard = QGuiApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(url)
        self.notify.emit("克隆地址已复制到剪贴板", "success")

    def clone_repo(self, repo: dict) -> None:
        base = str(self.cfg.get_setting("clone_dir", "") or "").strip()
        if not base:
            self.notify.emit("请先到「远程仓库」页选择克隆目标目录。", "warning")
            self.request_explore.emit()
            return
        name = repo.get("name") or (repo.get("full_name", "").split("/")[-1])
        target = os.path.join(base, name)
        if os.path.isdir(target) and os.listdir(target):
            if not confirm(
                self,
                "目录已存在",
                f"目标目录已存在且非空：\n{target}\n\n继续将可能覆盖其中内容，是否继续？",
                danger=True,
            ):
                return

        credential = Credential(
            username=self.cfg.username("github"), token=self.cfg.token("github")
        )
        self.status_label.setText(f"正在克隆 {repo.get('full_name', name)} → {target}")
        self.transfer.begin(
            f"正在克隆 {repo.get('full_name', name)} → {target}", kind="下载 · 克隆"
        )
        self.notify.emit(f"开始克隆 {repo.get('full_name', name)}", "info")
        log.info("从热门榜单克隆 %s 到 %s", repo.get("clone_url"), target)

        self.tasks.run(
            GitEngine.clone,
            repo.get("clone_url") or f"https://github.com/{repo.get('full_name')}.git",
            target,
            credential=credential,
            platform="github",
            on_progress=self._on_progress,
            on_detail=self._on_detail,
            on_success=lambda path: self._on_cloned(path, repo),
            on_error=self._on_clone_failed,
            label=f"克隆 {name}",
        )

    def _on_detail(self, payload: dict) -> None:
        self.transfer.update_detail(payload)

    def _on_cloned(self, path: str, repo: dict) -> None:
        self.transfer.finish(f"克隆完成：{os.path.basename(path)}", ok=True)
        self.store.add(
            path,
            platform="github",
            full_name=repo.get("full_name", ""),
            remote_url=repo.get("clone_url", ""),
            source="clone",
        )
        self.status_label.setText(f"克隆完成：{path}")
        log.info("榜单克隆完成: %s", path)
        self.repo_cloned.emit(path)

    def _on_clone_failed(self, message: str, detail: str) -> None:
        self.transfer.finish("克隆失败", ok=False)
        self.status_label.setText(message)
        show_error(self, "克隆失败", message, detail)

    # ---------------- 其它 ---------------- #

    def clear_cache(self) -> None:
        from ...core import translator

        count = translator.clear_cache()
        self.notify.emit(f"已清理 {count} 条翻译缓存。", "success")


__all__ = ["TrendingPage"]
