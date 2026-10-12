"""远程仓库页：浏览、搜索；左键选中，右键查看详情，一键克隆（可中止）。"""

from __future__ import annotations

import os

from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtGui import QAction, QGuiApplication
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...core import platapi
from ...core.gitengine import CANCEL_MESSAGE, Credential, GitEngine, GitError
from ...core.logger import get_logger
from ..widgets import (
    PathPicker,
    TransferPanel,
    confirm,
    elide,
    human_size,
    open_in_explorer,
    open_url,
    show_error,
    warn,
)

log = get_logger("ui.explore")

PAGE_SIZE = 30

COLUMNS = ["仓库", "描述", "语言", "星标", "可见性", "最近更新", "平台"]


class ExplorePage(QWidget):
    """远程仓库浏览器。"""

    repo_cloned = Signal(str)
    notify = Signal(str, str)   # (消息, 级别)

    def __init__(self, cfg, store, tasks, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.cfg = cfg
        self.store = store
        self.tasks = tasks
        self._rows: list[dict] = []
        self._page = 1
        self._has_next = False
        self._mode = "mine"
        self._clone_task = None
        self._busy = False
        self._menu: QMenu | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 20)
        layout.setSpacing(12)

        title = QLabel("远程仓库")
        title.setObjectName("H1")
        layout.addWidget(title)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)
        toolbar.addWidget(QLabel("平台"))
        self.platform_combo = QComboBox()
        for key, meta in platapi.PLATFORMS.items():
            self.platform_combo.addItem(meta.label, key)
        self.platform_combo.setFixedWidth(130)
        self.platform_combo.currentIndexChanged.connect(self._on_platform_changed)
        toolbar.addWidget(self.platform_combo)
        toolbar.addSpacing(6)

        self.mode_combo = QComboBox()
        self.mode_combo.addItem("我的仓库", "mine")
        self.mode_combo.addItem("搜索仓库", "search")
        self.mode_combo.setFixedWidth(120)
        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        toolbar.addWidget(self.mode_combo)

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("输入关键字搜索仓库，例如：vue、machine-learning")
        self.search_edit.returnPressed.connect(self._on_search)
        self.search_edit.setEnabled(False)
        toolbar.addWidget(self.search_edit, 1)

        self.action_btn = QPushButton("刷新列表")
        self.action_btn.setProperty("variant", "primary")
        self.action_btn.clicked.connect(self._on_search)
        toolbar.addWidget(self.action_btn)
        layout.addLayout(toolbar)

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(False)
        self.table.setShowGrid(False)
        # 左键单击只做选中；双击与右键只做「查看详情」，不会触发下载
        self.table.doubleClicked.connect(lambda _: self.open_detail())
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._on_context_menu)
        self.table.itemSelectionChanged.connect(self._on_selection_changed)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Interactive)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        for idx in range(2, len(COLUMNS)):
            header.setSectionResizeMode(idx, QHeaderView.ResizeToContents)
        self.table.setColumnWidth(0, 230)
        layout.addWidget(self.table, 1)

        pager = QHBoxLayout()
        pager.setSpacing(8)
        self.prev_btn = QPushButton("上一页")
        self.prev_btn.clicked.connect(lambda: self.load_page(self._page - 1))
        self.next_btn = QPushButton("下一页")
        self.next_btn.clicked.connect(lambda: self.load_page(self._page + 1))
        self.page_label = QLabel("第 1 页")
        self.page_label.setObjectName("Muted")
        self.count_label = QLabel("尚未加载数据")
        self.count_label.setObjectName("Faint")
        pager.addWidget(self.prev_btn)
        pager.addWidget(self.next_btn)
        pager.addWidget(self.page_label)
        pager.addStretch(1)
        pager.addWidget(self.count_label)
        layout.addLayout(pager)

        # ---- 选中项操作 ----
        action_row = QHBoxLayout()
        action_row.setSpacing(8)
        self.detail_btn = QPushButton("查看详情")
        self.detail_btn.setToolTip("在浏览器中打开该仓库的详情页（也可右键列表项）")
        self.detail_btn.clicked.connect(lambda: self.open_detail())
        self.copy_btn = QPushButton("复制克隆地址")
        self.copy_btn.clicked.connect(lambda: self.copy_selected_url())
        self.delete_btn = QPushButton("删除远程仓库")
        self.delete_btn.setProperty("variant", "danger")
        self.delete_btn.setToolTip("仅「我的仓库」中的仓库可删除；删除后不可恢复")
        self.delete_btn.clicked.connect(self.delete_remote)
        action_row.addWidget(self.detail_btn)
        action_row.addWidget(self.copy_btn)
        action_row.addWidget(self.delete_btn)
        action_row.addStretch(1)
        self.selection_label = QLabel("未选中仓库")
        self.selection_label.setObjectName("Faint")
        action_row.addWidget(self.selection_label)
        layout.addLayout(action_row)

        clone_bar = QHBoxLayout()
        clone_bar.setSpacing(8)
        clone_bar.addWidget(QLabel("克隆到"))
        self.dest_picker = PathPicker("选择克隆目标目录（仓库将作为子目录创建）")
        self.dest_picker.set_path(str(self.cfg.get_setting("clone_dir", "")))
        self.dest_picker.changed.connect(
            lambda value: self.cfg.set_setting("clone_dir", value.strip())
        )
        clone_bar.addWidget(self.dest_picker, 1)
        self.open_dest_btn = QPushButton("打开目录")
        self.open_dest_btn.setProperty("variant", "ghost")
        self.open_dest_btn.clicked.connect(
            lambda: open_in_explorer(self.dest_picker.path() or self.cfg.get_setting("clone_dir", ""))
        )
        clone_bar.addWidget(self.open_dest_btn)
        self.clone_btn = QPushButton("克隆选中仓库")
        self.clone_btn.setProperty("variant", "primary")
        self.clone_btn.clicked.connect(self.clone_selected)
        clone_bar.addWidget(self.clone_btn)
        self.stop_btn = QPushButton("停止克隆")
        self.stop_btn.setProperty("variant", "danger")
        self.stop_btn.setEnabled(False)
        self.stop_btn.setToolTip("中止正在进行的克隆，并清理未下载完成的目录")
        self.stop_btn.clicked.connect(self.stop_clone)
        clone_bar.addWidget(self.stop_btn)
        layout.addLayout(clone_bar)

        # 进度条 + 详情：克隆（下载）与删除远程仓库共用
        self.transfer = TransferPanel()
        layout.addWidget(self.transfer)
        self.progress = self.transfer.bar  # 兼容既有引用

        self.status_label = QLabel(
            "单击列表项选中仓库，右键可「查看详情」；选定目录后点击「克隆选中仓库」，"
            "下载过程中可随时「停止克隆」。"
        )
        self.status_label.setObjectName("Faint")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        # 初始状态下没有选中项，操作按钮应全部置灰
        self._reset_buttons()

    # ---------------- 交互 ---------------- #

    def _on_platform_changed(self) -> None:
        self._page = 1
        self._rows = []
        self.table.setRowCount(0)
        self._sync_action_state()
        self.auto_load()

    def _on_mode_changed(self) -> None:
        self._mode = self.mode_combo.currentData()
        is_search = self._mode == "search"
        self.search_edit.setEnabled(is_search)
        self.action_btn.setText("搜索" if is_search else "刷新列表")
        if not is_search:
            self.search_edit.clear()
        self.status_label.setText(
            "双击或右键列表项可「查看详情」；选中后点击「克隆选中仓库」开始下载。"
            if is_search
            else "左侧为你在当前平台下的仓库：选中后可「克隆」或「删除远程仓库」。"
        )
        self._sync_action_state()

    def _on_search(self) -> None:
        self._page = 1
        self.load_page(1)

    def auto_load(self) -> None:
        """进入页面时若已授权则自动加载一次。"""
        platform = self.platform_combo.currentData()
        if not self.cfg.is_connected(platform):
            self.status_label.setText(
                f"尚未完成 {platapi.PLATFORMS[platform].label} 授权，请先到「账号设置」配置令牌。"
            )
            return
        self.load_page(1)

    # ---------------- 数据加载 ---------------- #

    def load_page(self, page: int) -> None:
        if page < 1:
            return
        platform = self.platform_combo.currentData()
        mode = self.mode_combo.currentData()
        query = self.search_edit.text().strip()

        if mode == "search" and not query:
            self.status_label.setText("请输入搜索关键字。")
            return
        if not self.cfg.is_connected(platform):
            self.status_label.setText(
                f"尚未完成 {platapi.PLATFORMS[platform].label} 授权，请先到「账号设置」配置令牌。"
            )
            return

        try:
            client = platapi.make_client(self.cfg, platform)
        except platapi.ApiError as exc:
            show_error(self, "无法发起请求", str(exc))
            return

        self.action_btn.setEnabled(False)
        self.prev_btn.setEnabled(False)
        self.next_btn.setEnabled(False)
        self.status_label.setText(f"正在加载 {platapi.PLATFORMS[platform].label} 数据…")

        if mode == "mine":
            self.tasks.run(
                client.list_repos,
                page,
                PAGE_SIZE,
                on_success=lambda result: self._render(result[0], result[1], page, None),
                on_error=self._on_error,
                label=f"加载{platform}仓库列表",
            )
        else:
            self.tasks.run(
                client.search_repos,
                query,
                page,
                PAGE_SIZE,
                on_success=lambda result: self._render(result[0], len(result[0]) >= PAGE_SIZE, page, result[1]),
                on_error=self._on_error,
                label=f"搜索{platform}仓库",
            )

    def _on_error(self, message: str, detail: str) -> None:
        self._reset_buttons()
        self.status_label.setText(message)
        show_error(self, "请求失败", message, detail)

    def _reset_buttons(self) -> None:
        self.action_btn.setEnabled(not self._busy)
        self.prev_btn.setEnabled(not self._busy and self._page > 1)
        self.next_btn.setEnabled(not self._busy and self._has_next)
        self.mode_combo.setEnabled(not self._busy)
        self._sync_action_state()

    def _render(self, repos: list[dict], has_next: bool, page: int, total: int | None) -> None:
        self._rows = repos
        self._page = page
        self._has_next = has_next
        self.table.setRowCount(len(repos))

        for row, repo in enumerate(repos):
            values = [
                repo.get("full_name", ""),
                repo.get("description", "") or "—",
                repo.get("language", "") or "—",
                _stars(repo.get("stars", 0)),
                "私有" if repo.get("private") else "公开",
                repo.get("updated_at", "") or "—",
                repo.get("platform_label", ""),
            ]
            for col, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                if col == 0:
                    item.setToolTip(f"{repo.get('clone_url')}\n{repo.get('html_url')}")
                    item.setData(Qt.UserRole, row)
                elif col == 1:
                    item.setToolTip(repo.get("description", ""))
                elif col == 4:
                    item.setForeground(
                        Qt.GlobalColor.darkYellow if repo.get("private") else Qt.GlobalColor.darkGreen
                    )
                self.table.setItem(row, col, item)
        self.table.resizeRowsToContents()

        self.page_label.setText(f"第 {page} 页")
        if total is not None:
            self.count_label.setText(f"共约 {total} 个结果，本页 {len(repos)} 个")
        else:
            self.count_label.setText(f"本页 {len(repos)} 个仓库")
        self.status_label.setText(
            "单击选中仓库，双击或右键可「查看详情」；再点击「克隆选中仓库」开始下载。"
            if repos
            else "没有查询到仓库。"
        )
        self._reset_buttons()

    # ---------------- 选中 / 详情 ---------------- #

    def _on_selection_changed(self) -> None:
        repo = self._selected_repo()
        if not repo:
            self.selection_label.setText("未选中仓库")
        else:
            private = "私有" if repo.get("private") else "公开"
            self.selection_label.setText(
                f"已选中：{repo.get('full_name', '')} · {private} · "
                f"{_stars(repo.get('stars', 0))} 星标"
            )
        self._sync_action_state()

    def _sync_action_state(self) -> None:
        has_row = self._selected_repo() is not None
        busy = self._busy
        self.detail_btn.setEnabled(has_row and not busy)
        self.copy_btn.setEnabled(has_row and not busy)
        self.delete_btn.setEnabled(has_row and self.mode_combo.currentData() == "mine" and not busy)
        self.clone_btn.setEnabled(has_row and not busy)
        # 停止按钮只跟随忙碌态：克隆/删除任务启动瞬间即可响应
        self.stop_btn.setEnabled(busy)

    def _on_context_menu(self, pos) -> None:
        index = self.table.indexAt(pos)
        if not index.isValid():
            return
        self.table.selectRow(index.row())
        repo = self._selected_repo()
        if not repo:
            return
        self.status_label.setText(
            f"已选中 {repo.get('full_name', '')}；点击右键菜单中的「查看详情」可直接跳转到仓库页面。"
        )
        menu = self._build_context_menu(repo)
        self._menu = menu
        menu.exec(self.table.viewport().mapToGlobal(pos))

    def _build_context_menu(self, repo: dict) -> QMenu:
        """构造列表项的右键菜单（不依赖执行，便于测试）。"""
        menu = QMenu(self)
        menu.setObjectName("RepoContextMenu")
        detail = QAction("查看详情（在浏览器打开）", menu)
        detail.setObjectName("ActionDetail")
        detail.triggered.connect(lambda: self.open_detail(repo))
        menu.addAction(detail)

        copy_action = QAction("复制克隆地址", menu)
        copy_action.triggered.connect(lambda: self.copy_selected_url(repo))
        menu.addAction(copy_action)

        menu.addSeparator()
        clone_action = QAction("克隆到本地", menu)
        clone_action.triggered.connect(self.clone_selected)
        menu.addAction(clone_action)

        if self.mode_combo.currentData() == "mine":
            menu.addSeparator()
            delete_action = QAction("删除远程仓库…", menu)
            delete_action.setObjectName("ActionDelete")
            delete_action.triggered.connect(self.delete_remote)
            menu.addAction(delete_action)
        return menu

    def open_detail(self, repo: dict | None = None) -> None:
        """在浏览器中打开仓库详情页。"""
        repo = repo or self._selected_repo()
        if not repo:
            self.status_label.setText("请先在列表中选中一个仓库。")
            return
        url = repo.get("html_url") or repo.get("clone_url") or ""
        if not url:
            self.status_label.setText("该条目缺少仓库主页地址，无法打开详情。")
            return
        open_url(url)
        full_name = repo.get("full_name", url)
        self.status_label.setText(f"已在浏览器中打开 {full_name} 的详情页：{url}")
        log.info("打开仓库详情: %s", url)
        self.notify.emit(f"已打开 {full_name} 的详情页", "info")

    def copy_selected_url(self, repo: dict | None = None) -> None:
        repo = repo or self._selected_repo()
        if not repo:
            self.status_label.setText("请先在列表中选中一个仓库。")
            return
        url = repo.get("clone_url") or repo.get("html_url") or ""
        if not url:
            self.status_label.setText("该条目缺少克隆地址。")
            return
        clipboard = QGuiApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(url)
        self.status_label.setText(f"已复制克隆地址：{url}")
        self.notify.emit("克隆地址已复制到剪贴板", "success")

    # ---------------- 克隆 ---------------- #

    def _selected_repo(self) -> dict | None:
        row = self.table.currentRow()
        if row < 0 or row >= len(self._rows):
            return None
        return self._rows[row]

    def clone_selected(self) -> None:
        repo = self._selected_repo()
        if not repo:
            self.status_label.setText("请先在列表中选中一个仓库。")
            return
        if self._clone_task is not None and self._clone_task.isRunning():
            self.status_label.setText("已有克隆任务在进行中，可先点击「停止克隆」。")
            return

        base = self.dest_picker.path() or self.cfg.get_setting("clone_dir", "")
        if not base:
            self.status_label.setText("请先选择克隆目标目录。")
            return
        target = os.path.join(base, repo["name"])
        if os.path.isdir(target) and os.listdir(target):
            if not confirm(
                self,
                "目录已存在",
                f"目标目录已存在且非空：\n{target}\n\n继续将可能覆盖其中内容，是否继续？",
                danger=True,
            ):
                return

        platform = repo.get("platform", self.platform_combo.currentData())
        credential = Credential(
            username=self.cfg.username(platform), token=self.cfg.token(platform)
        )

        self._set_cloning(True)
        self.transfer.begin(f"正在克隆 {repo['full_name']} → {target}", kind="下载 · 克隆")
        self.status_label.setText(f"正在克隆 {repo['full_name']} → {target}")
        log.info("开始克隆 %s 到 %s", repo.get("clone_url"), target)

        self._clone_task = self.tasks.run(
            GitEngine.clone,
            repo["clone_url"],
            target,
            credential=credential,
            platform=platform,
            # 进度必须经由信号转发到界面线程，不能在后台线程直接操作控件
            on_progress=self._on_progress,
            on_detail=self._on_detail,
            on_success=lambda path: self._on_cloned(path, repo),
            on_error=lambda message, detail: self._on_clone_failed(message, detail, target),
            on_cancelled=lambda: self._on_clone_cancelled(target),
            label=f"克隆 {repo['name']}",
        )

    def stop_clone(self) -> None:
        """中止正在进行的克隆。

        取消请求会立刻置位；若此刻正处于「已发出请求、等待服务端响应」的网络等待
        阶段，实际中断会等到下一次数据往返才生效，届时目标目录会被自动清理。
        """
        task = self._clone_task
        if task is None or not task.isRunning():
            self.stop_btn.setEnabled(False)
            self.status_label.setText("当前没有正在进行的克隆任务。")
            return
        task.cancel()
        self.stop_btn.setEnabled(False)
        self.status_label.setText("已请求停止克隆，正在等待网络响应返回…")
        QTimer.singleShot(1500, self._confirm_stopping)
        log.info("用户请求停止克隆")

    def _confirm_stopping(self) -> None:
        """网络等待期较长时给出解释，避免用户误以为按钮失灵。"""
        if self._busy:
            self.status_label.setText(
                "已请求停止克隆：正在等待当前网络操作返回，中止后会自动清理未完成的目录。"
            )

    def _on_progress(self, text: str) -> None:
        self.status_label.setText(elide(text, 110))
        self.transfer.set_message(elide(text, 200))

    def _on_detail(self, payload: dict) -> None:
        self.transfer.update_detail(payload)

    def _on_cloned(self, path: str, repo: dict) -> None:
        self._clone_task = None
        self._set_cloning(False)
        self.transfer.finish(f"克隆完成：{os.path.basename(path)}", ok=True)
        self.store.add(
            path,
            platform=repo.get("platform", ""),
            full_name=repo.get("full_name", ""),
            remote_url=repo.get("clone_url", ""),
            source="clone",
        )
        self.status_label.setText(f"克隆完成：{path}")
        log.info("克隆完成: %s", path)
        self.repo_cloned.emit(path)

    def _on_clone_failed(self, message: str, detail: str, target: str = "") -> None:
        self._clone_task = None
        self._set_cloning(False)
        if message.startswith(CANCEL_MESSAGE):
            self._on_clone_cancelled(target)
            return
        self.transfer.finish("克隆失败", ok=False)
        self.status_label.setText(message)
        show_error(self, "克隆失败", message, detail)

    def _on_clone_cancelled(self, target: str = "") -> None:
        self._clone_task = None
        self._set_cloning(False)
        self.transfer.finish("克隆已取消", ok=False, cancelled=True)
        leftover = f"（已清理 {target}）" if target and not os.path.isdir(target) else ""
        self.status_label.setText(f"克隆已取消{leftover}，未下载完成的内容不会保留。")
        log.info("克隆已取消: %s", target)
        self.notify.emit("克隆已取消", "warning")

    def _set_cloning(self, busy: bool) -> None:
        self._busy = busy
        self.clone_btn.setText("克隆中…" if busy else "克隆选中仓库")
        self._reset_buttons()

    # ---------------- 删除远程仓库 ---------------- #

    def delete_remote(self) -> None:
        """删除「我的仓库」中的远程仓库（不可恢复）。"""
        repo = self._selected_repo()
        if not repo:
            self.status_label.setText("请先在列表中选中一个仓库。")
            return
        if self.mode_combo.currentData() != "mine":
            warn(
                self,
                "暂不支持删除",
                "只有「我的仓库」列表中的仓库才能在此删除。\n"
                "搜索结果中的第三方仓库请到对应平台网页端操作。",
            )
            return
        platform = repo.get("platform") or self.platform_combo.currentData()
        full_name = (repo.get("full_name") or "").strip()
        if not full_name:
            show_error(self, "无法删除", "该条目缺少仓库标识，无法删除。")
            return

        if not confirm(
            self,
            "删除远程仓库",
            f"即将永久删除远程仓库：\n\n{full_name}\n\n"
            "· 该操作不可撤销，仓库中的代码、Issue、PR 都会被删除；\n"
            "· 本地已克隆的副本不会被删除，但会失去远程地址。\n\n确定继续吗？",
            danger=True,
        ):
            return

        typed, ok = QInputDialog.getText(
            self, "二次确认", f"请输入仓库全名以确认删除：\n{full_name}"
        )
        if not ok:
            self.status_label.setText("已取消删除。")
            return
        if (typed or "").strip() != full_name:
            show_error(self, "确认失败", "输入的仓库全名不匹配，已取消删除操作。")
            return

        try:
            client = platapi.make_client(self.cfg, platform)
        except platapi.ApiError as exc:
            show_error(self, "无法发起请求", str(exc))
            return

        self._set_cloning(True)
        self.transfer.begin(f"正在删除远程仓库 {full_name} …", kind="删除远程")
        self.stop_btn.setEnabled(False)  # 删除任务不可中止
        self.status_label.setText(f"正在删除远程仓库 {full_name} …")
        log.warning("准备删除远程仓库: %s", full_name)
        self.tasks.run(
            client.delete_repo,
            full_name,
            on_success=lambda name: self._on_deleted(name),
            on_error=self._on_delete_failed,
            label=f"删除远程仓库 {full_name}",
        )

    def _on_deleted(self, full_name: str) -> None:
        self._set_cloning(False)
        self.transfer.finish(f"已删除远程仓库 {full_name}", ok=True)
        self._rows = [r for r in self._rows if r.get("full_name") != full_name]
        self._render(self._rows, self._has_next, self._page, None)
        self.status_label.setText(f"远程仓库 {full_name} 已删除（本地副本未受影响）。")
        log.warning("已删除远程仓库: %s", full_name)
        self.notify.emit(f"远程仓库 {full_name} 已删除", "success")

    def _on_delete_failed(self, message: str, detail: str) -> None:
        self._set_cloning(False)
        self.transfer.finish("删除失败", ok=False)
        self.status_label.setText(message)
        show_error(self, "删除失败", message, detail)

    def reload(self) -> None:
        self.dest_picker.set_path(str(self.cfg.get_setting("clone_dir", "")))
        self.auto_load()


def _stars(count) -> str:
    try:
        count = int(count)
    except (TypeError, ValueError):
        return "0"
    if count >= 1000:
        return f"{count / 1000:.1f}k"
    return str(count)
