"""本地仓库页：导入、状态总览、同步、分支与远程管理。

包含实时变更检测与冲突预警。
"""

from __future__ import annotations

import os

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...core import platapi
from ...core.config import RepoStore, _now
from ...core.gitengine import STAGED_LABELS, Credential, GitEngine, GitError, SyncState
from ...core.logger import get_logger
from ...core.publish import PublishPlan, publish_local
from ...core.tree import filter_tree, format_size, list_dir, summarize
from ..publish_dialog import PublishDialog
from ..widgets import (
    Badge,
    Card,
    DiffView,
    StatusDot,
    TransferPanel,
    confirm,
    elide,
    hline,
    info,
    mono_label,
    open_in_explorer,
    scrollable,
    show_error,
    warn,
)

log = get_logger("ui.repos")

GROUP_LABELS = {"staged": "已暂存的更改", "unstaged": "未暂存的更改", "untracked": "未跟踪的文件"}
GROUP_COLORS = {"staged": "#1a7f37", "unstaged": "#9a6700", "untracked": "#8b949e"}


class RepoDetailPanel(QWidget):
    """单个本地仓库的详细操作面板。"""

    changed = Signal()          # 仓库内容发生变化（用于列表刷新）
    notify = Signal(str, str)   # (消息, 级别)
    request_accounts = Signal()  # 需要用户前往账号设置页

    def __init__(self, cfg, store: RepoStore, tasks, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.cfg = cfg
        self.store = store
        self.tasks = tasks
        self.path = ""
        self._status: dict = {}
        self._sync = SyncState()
        self._signature = ""
        self._busy = False
        self._upload_task = None      # 正在运行的上传任务（推送 / 发布），供「停止」使用
        self._struct_loading = False
        self._struct_options: dict = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        # ---- 头部 ----
        head = QHBoxLayout()
        head.setSpacing(8)
        self.name_label = QLabel("未选择仓库")
        self.name_label.setObjectName("H2")
        head.addWidget(self.name_label)
        self.branch_badge = Badge("—", "#656d76")
        self.sync_badge = Badge("—", "#656d76")
        head.addWidget(self.branch_badge)
        head.addWidget(self.sync_badge)
        head.addStretch(1)
        self.open_btn = QPushButton("打开目录")
        self.open_btn.setProperty("variant", "ghost")
        self.open_btn.clicked.connect(lambda: open_in_explorer(self.path))
        head.addWidget(self.open_btn)
        self.refresh_btn = QPushButton("刷新")
        self.refresh_btn.clicked.connect(lambda: self.refresh_all(manual=True))
        head.addWidget(self.refresh_btn)
        layout.addLayout(head)

        self.path_label = QLabel("")
        self.path_label.setObjectName("Faint")
        self.path_label.setWordWrap(True)
        layout.addWidget(self.path_label)

        self.notice = QLabel("")
        self.notice.setWordWrap(True)
        self.notice.setVisible(False)
        layout.addWidget(self.notice)

        # ---- 标签页 ----
        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_overview(), "概览")
        self.tabs.addTab(self._build_changes(), "变更")
        self.tabs.addTab(self._build_commits(), "提交")
        self.tabs.addTab(self._build_branches(), "分支")
        self.tabs.addTab(self._build_remotes(), "远程")
        self.tabs.addTab(self._build_stash(), "暂存区")
        self.structure_page = self._build_structure()
        self.structure_index = self.tabs.addTab(self.structure_page, "项目结构")
        self.tabs.currentChanged.connect(self._on_tab_changed)
        layout.addWidget(self.tabs, 1)

        # ---- 传输面板（进度条 + 详情）：上传 / 下载共用 ----
        self.transfer = TransferPanel()
        self.progress = self.transfer.bar  # 兼容既有引用
        self.transfer.stop_requested.connect(self._on_stop_transfer)
        layout.addWidget(self.transfer)

        # ---- 实时监测 ----
        self.watch_timer = QTimer(self)
        self.watch_timer.timeout.connect(self._poll_local)
        self.remote_timer = QTimer(self)
        self.remote_timer.timeout.connect(self._poll_remote)
        self._remote_advanced = False

    # ===================================================================== #
    # 构建各标签页
    # ===================================================================== #

    def _build_overview(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(12)
        card = Card("仓库状态")
        grid = QGridLayout()
        grid.setHorizontalSpacing(26)
        grid.setVerticalSpacing(9)
        self.ov_labels: dict[str, QLabel] = {}
        fields = [
            ("current_branch", "当前分支"),
            ("upstream", "跟踪分支"),
            ("divergence", "领先 / 落后"),
            ("head", "最新提交"),
            ("author_time", "提交时间"),
            ("remotes", "远程地址"),
            ("dirty", "工作区"),
            ("file_count", "提交总数"),
        ]
        for idx, (key, label) in enumerate(fields):
            row, col = divmod(idx, 2)
            name = QLabel(label)
            name.setObjectName("Muted")
            value = QLabel("—")
            value.setWordWrap(True)
            value.setTextInteractionFlags(Qt.TextSelectableByMouse)
            self.ov_labels[key] = value
            grid.addWidget(name, row * 2, col)
            grid.addWidget(value, row * 2 + 1, col)
        card.add_layout(grid)
        layout.addWidget(card)

        actions = Card("同步操作")
        row1 = QHBoxLayout()
        row1.setSpacing(8)
        self.btn_fetch = QPushButton("仅获取远程更新")
        self.btn_fetch.clicked.connect(lambda: self.do_fetch())
        self.btn_pull = QPushButton("拉取并合并")
        self.btn_pull.setProperty("variant", "primary")
        self.btn_pull.clicked.connect(self.do_pull_flow)
        self.btn_push = QPushButton("推送本地提交")
        self.btn_push.setProperty("variant", "primary")
        self.btn_push.clicked.connect(self.do_push)
        row1.addWidget(self.btn_fetch)
        row1.addWidget(self.btn_pull)
        row1.addWidget(self.btn_push)
        row1.addStretch(1)
        actions.add_layout(row1)

        row2 = QHBoxLayout()
        row2.setSpacing(8)
        self.btn_publish = QPushButton("发布到远程仓库")
        self.btn_publish.setProperty("variant", "primary")
        self.btn_publish.clicked.connect(self.publish_to_remote)
        self.btn_force_pull = QPushButton("强制用远程覆盖本地")
        self.btn_force_pull.setProperty("variant", "danger")
        self.btn_force_pull.clicked.connect(self.do_force_pull)
        self.btn_stash = QPushButton("暂存当前更改")
        self.btn_stash.clicked.connect(self.do_stash_push)
        self.btn_structure = QPushButton("查看项目结构")
        self.btn_structure.setProperty("variant", "ghost")
        self.btn_structure.setToolTip("以目录树的形式浏览该项目的工作区文件结构")
        self.btn_structure.clicked.connect(
            lambda: self.tabs.setCurrentWidget(self.structure_page)
        )
        self.btn_add_remote = QPushButton("配置远程地址")
        self.btn_add_remote.setProperty("variant", "ghost")
        self.btn_add_remote.clicked.connect(lambda: self.tabs.setCurrentIndex(4))
        row2.addWidget(self.btn_publish)
        row2.addWidget(self.btn_force_pull)
        row2.addWidget(self.btn_stash)
        row2.addWidget(self.btn_structure)
        row2.addWidget(self.btn_add_remote)
        row2.addStretch(1)
        actions.add_layout(row2)

        self.ov_hint = QLabel(
            "「发布到远程仓库」可一键在 GitHub / Gitee 上创建仓库并推送本地内容；"
            "拉取前会自动检查工作区，存在未提交更改时会阻止操作，避免代码丢失。"
        )
        self.ov_hint.setObjectName("Faint")
        self.ov_hint.setWordWrap(True)
        actions.add(self.ov_hint)
        layout.addWidget(actions)
        layout.addStretch(1)
        # 套滚动容器：传输详情展开 / 窗口变矮时，空间不足改为滚动而不是控件重叠
        return scrollable(page)

    def _build_changes(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.btn_stage_all = QPushButton("全部暂存")
        self.btn_stage_all.clicked.connect(lambda: self._stage(mode="all"))
        self.btn_stage_sel = QPushButton("暂存选中")
        self.btn_stage_sel.clicked.connect(lambda: self._stage(mode="selected"))
        self.btn_unstage = QPushButton("取消暂存")
        self.btn_unstage.clicked.connect(self._unstage)
        self.btn_discard = QPushButton("放弃更改")
        self.btn_discard.setProperty("variant", "danger")
        self.btn_discard.clicked.connect(self._discard)
        bar.addWidget(self.btn_stage_all)
        bar.addWidget(self.btn_stage_sel)
        bar.addWidget(self.btn_unstage)
        bar.addWidget(self.btn_discard)
        bar.addStretch(1)
        self.change_summary = QLabel("—")
        self.change_summary.setObjectName("Muted")
        bar.addWidget(self.change_summary)
        layout.addLayout(bar)

        splitter = QSplitter(Qt.Horizontal)
        self.change_tree = QTreeWidget()
        self.change_tree.setHeaderLabels(["文件", "状态"])
        self.change_tree.setRootIsDecorated(True)
        self.change_tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.change_tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.change_tree.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.change_tree.itemSelectionChanged.connect(self._on_change_selected)
        splitter.addWidget(self.change_tree)

        self.diff_view = DiffView()
        splitter.addWidget(self.diff_view)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 5)
        layout.addWidget(splitter, 1)
        return page

    def _build_commits(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        card = Card("创建提交")
        self.commit_message = QPlainTextEdit()
        self.commit_message.setPlaceholderText("填写提交说明，例如：修复登录接口超时问题")
        self.commit_message.setFixedHeight(64)
        card.add(self.commit_message)
        row = QHBoxLayout()
        row.setSpacing(8)
        self.commit_stage_all = QCheckBox("提交前暂存全部更改")
        self.commit_stage_all.setChecked(True)
        self.commit_author = QLineEdit()
        self.commit_author.setPlaceholderText("作者")
        self.commit_author.setFixedWidth(130)
        self.commit_email = QLineEdit()
        self.commit_email.setPlaceholderText("邮箱")
        self.commit_email.setFixedWidth(190)
        self.btn_commit = QPushButton("提交到本地")
        self.btn_commit.setProperty("variant", "primary")
        self.btn_commit.clicked.connect(self.do_commit)
        row.addWidget(self.commit_stage_all)
        row.addWidget(self.commit_author)
        row.addWidget(self.commit_email)
        row.addStretch(1)
        row.addWidget(self.btn_commit)
        card.add_layout(row)
        layout.addWidget(card)

        splitter = QSplitter(Qt.Vertical)
        self.history_table = QTableWidget(0, 4)
        self.history_table.setHorizontalHeaderLabels(["提交", "作者", "时间", "说明"])
        self.history_table.verticalHeader().setVisible(False)
        self.history_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.history_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.history_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.history_table.setShowGrid(False)
        head = self.history_table.horizontalHeader()
        head.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        head.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        head.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        head.setSectionResizeMode(3, QHeaderView.Stretch)
        self.history_table.itemSelectionChanged.connect(self._on_commit_selected)
        splitter.addWidget(self.history_table)

        self.commit_diff = DiffView()
        self.commit_diff.setPlaceholderText("选中上方提交记录可查看该提交的完整改动")
        splitter.addWidget(self.commit_diff)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 4)
        layout.addWidget(splitter, 1)
        return page

    def _build_branches(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.btn_branch_switch = QPushButton("切换到选中分支")
        self.btn_branch_switch.clicked.connect(self._switch_branch)
        self.btn_branch_new = QPushButton("新建分支")
        self.btn_branch_new.clicked.connect(self._new_branch)
        self.btn_branch_del = QPushButton("删除分支")
        self.btn_branch_del.setProperty("variant", "danger")
        self.btn_branch_del.clicked.connect(self._delete_branch)
        self.btn_branch_pull = QPushButton("拉取远程分支")
        self.btn_branch_pull.setProperty("variant", "ghost")
        self.btn_branch_pull.clicked.connect(lambda: self.do_fetch())
        bar.addWidget(self.btn_branch_switch)
        bar.addWidget(self.btn_branch_new)
        bar.addWidget(self.btn_branch_del)
        bar.addWidget(self.btn_branch_pull)
        bar.addStretch(1)
        layout.addLayout(bar)

        splitter = QSplitter(Qt.Horizontal)
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.addWidget(_section_label("本地分支"))
        self.local_table = QTableWidget(0, 4)
        self.local_table.setHorizontalHeaderLabels(["分支", "上游", "状态", "提交"])
        self.local_table.verticalHeader().setVisible(False)
        self.local_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.local_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.local_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.local_table.setShowGrid(False)
        lh = self.local_table.horizontalHeader()
        lh.setSectionResizeMode(0, QHeaderView.Stretch)
        for i in (1, 2, 3):
            lh.setSectionResizeMode(i, QHeaderView.ResizeToContents)
        lv.addWidget(self.local_table)
        splitter.addWidget(left)

        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.addWidget(_section_label("远程分支"))
        self.remote_table = QTableWidget(0, 2)
        self.remote_table.setHorizontalHeaderLabels(["分支", "提交"])
        self.remote_table.verticalHeader().setVisible(False)
        self.remote_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.remote_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.remote_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.remote_table.setShowGrid(False)
        rh = self.remote_table.horizontalHeader()
        rh.setSectionResizeMode(0, QHeaderView.Stretch)
        rh.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        rv.addWidget(self.remote_table)
        splitter.addWidget(right)
        layout.addWidget(splitter, 1)
        return page

    def _build_remotes(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.btn_remote_publish = QPushButton("创建远程仓库并推送")
        self.btn_remote_publish.setProperty("variant", "primary")
        self.btn_remote_publish.clicked.connect(self.publish_to_remote)
        self.btn_remote_add = QPushButton("添加远程")
        self.btn_remote_add.clicked.connect(self._add_remote)
        self.btn_remote_edit = QPushButton("修改地址")
        self.btn_remote_edit.clicked.connect(self._edit_remote)
        self.btn_remote_del = QPushButton("删除远程")
        self.btn_remote_del.setProperty("variant", "danger")
        self.btn_remote_del.clicked.connect(self._remove_remote)
        bar.addWidget(self.btn_remote_publish)
        bar.addWidget(self.btn_remote_add)
        bar.addWidget(self.btn_remote_edit)
        bar.addWidget(self.btn_remote_del)
        bar.addStretch(1)
        layout.addLayout(bar)

        self.remote_list = QTableWidget(0, 2)
        self.remote_list.setHorizontalHeaderLabels(["名称", "地址"])
        self.remote_list.verticalHeader().setVisible(False)
        self.remote_list.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.remote_list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.remote_list.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.remote_list.setShowGrid(False)
        rh = self.remote_list.horizontalHeader()
        rh.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        rh.setSectionResizeMode(1, QHeaderView.Stretch)
        layout.addWidget(self.remote_list, 1)

        note = QLabel(
            "提示：远程地址请使用 HTTPS 形式（如 https://github.com/用户名/仓库.git）。"
            "GitDesk 会在同步时自动附加已配置的访问令牌，无需把令牌写进地址。"
        )
        note.setObjectName("Faint")
        note.setWordWrap(True)
        layout.addWidget(note)
        return page

    def _build_stash(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        bar = QHBoxLayout()
        bar.setSpacing(8)
        btn_push = QPushButton("暂存当前更改")
        btn_push.setProperty("variant", "primary")
        btn_push.clicked.connect(self.do_stash_push)
        btn_pop = QPushButton("恢复最近一次暂存")
        btn_pop.clicked.connect(self.do_stash_pop)
        btn_refresh = QPushButton("刷新")
        btn_refresh.clicked.connect(self.refresh_stash)
        bar.addWidget(btn_push)
        bar.addWidget(btn_pop)
        bar.addWidget(btn_refresh)
        bar.addStretch(1)
        layout.addLayout(bar)

        self.stash_list = QTableWidget(0, 2)
        self.stash_list.setHorizontalHeaderLabels(["序号", "说明"])
        self.stash_list.verticalHeader().setVisible(False)
        self.stash_list.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.stash_list.setShowGrid(False)
        sh = self.stash_list.horizontalHeader()
        sh.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        sh.setSectionResizeMode(1, QHeaderView.Stretch)
        layout.addWidget(self.stash_list, 1)

        tip = QLabel(
            "暂存区可临时保存未提交的改动，便于先同步远程再恢复。恢复时若与远程内容冲突，"
            "请手动处理冲突文件。"
        )
        tip.setObjectName("Faint")
        tip.setWordWrap(True)
        layout.addWidget(tip)
        return page

    # ---------------- 项目结构树 ----------------

    def _build_structure(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.struct_refresh_btn = QPushButton("刷新结构")
        self.struct_refresh_btn.clicked.connect(lambda: self.refresh_structure(force=True))
        self.struct_expand_btn = QPushButton("全部展开")
        self.struct_expand_btn.clicked.connect(lambda: self.struct_tree.expandAll())
        self.struct_collapse_btn = QPushButton("全部折叠")
        self.struct_collapse_btn.clicked.connect(lambda: self.struct_tree.collapseAll())
        bar.addWidget(self.struct_refresh_btn)
        bar.addWidget(self.struct_expand_btn)
        bar.addWidget(self.struct_collapse_btn)
        bar.addSpacing(4)

        self.struct_filter = QLineEdit()
        self.struct_filter.setPlaceholderText("按名称过滤…")
        self.struct_filter.setFixedWidth(170)
        self.struct_filter.returnPressed.connect(lambda: self.refresh_structure(force=True))
        bar.addWidget(self.struct_filter)
        self.struct_dirs_only = QCheckBox("仅显示目录")
        self.struct_dirs_only.toggled.connect(lambda _: self.refresh_structure(force=True))
        bar.addWidget(self.struct_dirs_only)
        bar.addStretch(1)
        self.struct_summary = QLabel("—")
        self.struct_summary.setObjectName("Faint")
        bar.addWidget(self.struct_summary)
        layout.addLayout(bar)

        self.struct_tree = QTreeWidget()
        self.struct_tree.setObjectName("StructureTree")
        self.struct_tree.setHeaderLabels(["名称", "大小", "类型"])
        self.struct_tree.setRootIsDecorated(True)
        self.struct_tree.setAlternatingRowColors(False)
        self.struct_tree.itemExpanded.connect(self._on_structure_expanded)
        self.struct_tree.itemDoubleClicked.connect(self._on_structure_activated)
        header = self.struct_tree.header()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        layout.addWidget(self.struct_tree, 1)

        tip = QLabel(
            "按需加载：展开目录时才读取该层内容，超大仓库也不会卡顿；"
            "双击文件可用系统默认程序打开。已自动隐藏 .git 元数据目录。"
        )
        tip.setObjectName("Faint")
        tip.setWordWrap(True)
        layout.addWidget(tip)
        return page

    def _structure_options(self) -> dict:
        return {
            "show_hidden": True,
            "dirs_only": bool(self.struct_dirs_only.isChecked()),
            "keyword": self.struct_filter.text().strip(),
        }

    def _on_tab_changed(self, index: int) -> None:
        if index == getattr(self, "structure_index", -1):
            self.refresh_structure()

    def refresh_structure(self, force: bool = False) -> None:
        """读取工作区目录结构（根层，或按关键字全局搜索）。"""
        if not self.path or not os.path.isdir(self.path):
            self.struct_tree.clear()
            self.struct_summary.setText("—")
            return
        if self._struct_loading and not force:
            return
        path = self.path
        options = self._structure_options()
        keyword = options.get("keyword") or ""
        self._struct_loading = True
        self.struct_summary.setText("正在读取项目结构…")

        def collect() -> dict:
            stats = summarize(path, max_entries=8000)
            if keyword:
                entries = filter_tree(
                    path,
                    keyword,
                    dirs_only=options["dirs_only"],
                    show_hidden=options["show_hidden"],
                )
                return {"entries": entries, "nested": True, "stats": stats, "keyword": keyword}
            return {
                "entries": list_dir(path, "", **options),
                "nested": False,
                "stats": stats,
                "keyword": "",
            }

        self.tasks.run(
            collect,
            on_success=lambda payload: self._render_structure_root(payload, options),
            on_error=self._on_structure_error,
            label="读取项目结构",
            silent=True,
        )

    def _on_structure_error(self, message: str, detail: str = "") -> None:
        self._struct_loading = False
        self.struct_summary.setText(f"读取失败：{message}")

    def _render_structure_root(self, payload: dict, options: dict) -> None:
        self._struct_loading = False
        entries = payload.get("entries") or []
        stats = payload.get("stats") or {}
        keyword = payload.get("keyword") or ""
        self._struct_options = dict(options)
        self.struct_tree.clear()
        for entry in entries:
            self.struct_tree.addTopLevelItem(
                self._make_structure_item(entry, options, nested=bool(payload.get("nested")))
            )
        if keyword:
            if entries:
                self.struct_tree.expandAll()
                self.struct_summary.setText(f"匹配「{keyword}」的条目已展开显示")
            else:
                self.struct_summary.setText(f"没有找到名称包含「{keyword}」的文件或目录。")
            return
        if not entries:
            self.struct_summary.setText("该目录下没有可展示的内容。")
            return
        text = (
            f"{stats.get('dirs', 0)} 个目录 · {stats.get('files', 0)} 个文件 · "
            f"{format_size(stats.get('size', 0))}"
        )
        if stats.get("truncated"):
            text += "（已达扫描上限，统计可能不完整）"
        self.struct_summary.setText(text)

    def _make_structure_item(
        self, entry: dict, options: dict, nested: bool = False
    ) -> QTreeWidgetItem:
        if entry.get("is_dir"):
            detail = f"{entry.get('child_count', 0)} 项"
            kind = "目录"
        else:
            detail = format_size(entry.get("size", 0))
            kind = _file_kind(entry.get("name", ""))
        item = QTreeWidgetItem([str(entry.get("name", "")), detail, kind])
        item.setData(0, Qt.UserRole, entry)
        item.setToolTip(0, entry.get("rel", ""))
        item.setForeground(0, QColor("#1f56b8" if entry.get("is_dir") else "#1f2328"))
        if entry.get("is_dir") and nested:
            # 搜索模式：子树已在内存里，直接铺开，无需再按需加载
            for child in entry.get("children") or []:
                item.addChild(self._make_structure_item(child, options, nested=True))
            item.setChildIndicatorPolicy(
                QTreeWidgetItem.ShowIndicator
                if item.childCount()
                else QTreeWidgetItem.DontShowIndicator
            )
            item.setData(0, Qt.UserRole + 1, True)
            return item
        if entry.get("is_dir") and entry.get("child_count", 0) > 0:
            # 占位子项：让展开箭头立刻可见，真正内容在展开时按需加载
            placeholder = QTreeWidgetItem(["加载中…", "", ""])
            placeholder.setData(0, Qt.UserRole, {"placeholder": True})
            item.addChild(placeholder)
            item.setChildIndicatorPolicy(QTreeWidgetItem.ShowIndicator)
        else:
            item.setChildIndicatorPolicy(QTreeWidgetItem.DontShowIndicator)
            item.setData(0, Qt.UserRole + 1, True)
        return item

    def _on_structure_expanded(self, item: QTreeWidgetItem) -> None:
        if item.data(0, Qt.UserRole + 1):
            return
        entry = item.data(0, Qt.UserRole) or {}
        if not isinstance(entry, dict) or not entry.get("is_dir"):
            return
        item.setData(0, Qt.UserRole + 1, True)
        path = self.path
        rel = str(entry.get("rel", ""))
        options = dict(getattr(self, "_struct_options", None) or self._structure_options())
        self.tasks.run(
            lambda: list_dir(path, rel, **options),
            on_success=lambda children: self._fill_structure(item, children, options),
            on_error=lambda message, detail: self._on_structure_branch_error(item, message),
            label=f"读取目录 {rel}",
            silent=True,
        )

    def _fill_structure(self, item: QTreeWidgetItem, children: list, options: dict) -> None:
        if item.treeWidget() is None:  # 结果回来前树已被重建
            return
        item.takeChildren()
        for entry in children:
            item.addChild(self._make_structure_item(entry, options))
        if not children:
            item.setChildIndicatorPolicy(QTreeWidgetItem.DontShowIndicator)

    def _on_structure_branch_error(self, item: QTreeWidgetItem, message: str) -> None:
        if item.treeWidget() is None:
            return
        item.takeChildren()
        failed = QTreeWidgetItem([f"（无法读取：{message}）", "", ""])
        item.addChild(failed)
        item.setChildIndicatorPolicy(QTreeWidgetItem.DontShowIndicator)

    def _on_structure_activated(self, item: QTreeWidgetItem, _column: int = 0) -> None:
        entry = item.data(0, Qt.UserRole) or {}
        if not isinstance(entry, dict) or entry.get("is_dir") or not self.path:
            return
        full = os.path.join(self.path, str(entry.get("rel", "")))
        if os.path.exists(full):
            open_in_explorer(full)
            self.notify.emit(f"已用系统默认程序打开：{entry.get('name', '')}", "info")

    # ===================================================================== #
    # 绑定仓库
    # ===================================================================== #

    def set_repo(self, path: str) -> None:
        self.stop_watch()
        self.path = path or ""
        if not self.path:
            self.name_label.setText("未选择仓库")
            self.path_label.setText("")
            self.notice.setVisible(False)
            self._status = {}
            self._sync = SyncState()
            self._signature = ""
            self._clear_views()
            return
        self.name_label.setText(os.path.basename(os.path.normpath(self.path)))
        self.path_label.setText(self.path)
        self.commit_author.setText(str(self.cfg.get_setting("git_username", "GitDesk")))
        self.commit_email.setText(str(self.cfg.get_setting("git_email", "gitdesk@localhost")))
        self.refresh_all(manual=True)
        self.start_watch()

    def _clear_views(self) -> None:
        self.change_tree.clear()
        self.history_table.setRowCount(0)
        self.local_table.setRowCount(0)
        self.remote_table.setRowCount(0)
        self.remote_list.setRowCount(0)
        self.stash_list.setRowCount(0)
        if hasattr(self, "struct_tree"):
            self.struct_tree.clear()
            self.struct_summary.setText("—")
            self._struct_loading = False
        self.diff_view.set_diff("")
        self.commit_diff.set_diff("")
        for label in self.ov_labels.values():
            label.setText("—")
        self.branch_badge.set_state("—", "#656d76")
        self.sync_badge.set_state("—", "#656d76")

    # ===================================================================== #
    # 监控
    # ===================================================================== #

    def start_watch(self) -> None:
        if not self.path or not self.cfg.get_setting("watch_enabled", True):
            return
        interval = int(self.cfg.get_setting("watch_interval_ms", 5000) or 5000)
        self.watch_timer.start(max(2000, interval))
        self.remote_timer.start(90000)
        log.debug("已开启变更监测: %s", self.path)

    def stop_watch(self) -> None:
        self.watch_timer.stop()
        self.remote_timer.stop()

    def _poll_local(self) -> None:
        if not self.path or self._busy or not self.isVisible():
            return
        path = self.path

        def job():
            status = GitEngine.status(path, untracked=True)
            sync = GitEngine.sync_state(path)
            return status, sync

        self.tasks.run(
            job,
            on_success=self._on_poll_result,
            on_error=lambda m, d: log.debug("变更检测失败: %s", m),
            label="变更检测",
            silent=True,
        )

    def _on_poll_result(self, result) -> None:
        status, sync = result
        if status.get("branch", "") == "" and not status.get("clean", True):
            return
        signature = _status_signature(status)
        changed = signature != self._signature
        first = self._signature == ""
        self._signature = signature
        self._status = status
        self._sync = sync
        self._render_overview()
        if changed and not first:
            count = len(status["staged"]) + len(status["unstaged"]) + len(status["untracked"])
            self._render_changes()
            self.notify.emit(
                f"检测到工作区变化：{count} 个文件有改动，请及时提交或同步。", "warning"
            )
            log.info("工作区发生变化，当前共 %d 个变更文件", count)

    def _poll_remote(self) -> None:
        if not self.path or self._busy:
            return
        path = self.path
        remote = self._primary_remote()
        if not remote:
            return
        branch = self._status.get("branch") or self._sync.upstream.rsplit("/", 1)[-1]
        if not branch:
            return
        platform = self._guess_platform(remote.get("url", ""))
        credential = Credential(self.cfg.username(platform), self.cfg.token(platform))

        def job():
            return GitEngine.remote_branch_sha(path, remote["name"], branch, credential, platform)

        self.tasks.run(
            job,
            on_success=lambda sha: self._on_remote_probe(sha),
            on_error=lambda m, d: log.debug("远程探测失败: %s", m),
            label="远程更新探测",
            silent=True,
        )

    def _on_remote_probe(self, sha: str | None) -> None:
        if not sha or not self.path:
            return
        local_tracking = self._sync.upstream
        if not local_tracking:
            return
        try:
            with __import__("dulwich").porcelain.open_repo_closing(self.path) as repo:
                local_sha = repo.refs.get(local_tracking.encode())
        except Exception:
            return
        if local_sha and local_sha.decode() != sha:
            if not self._remote_advanced:
                self._remote_advanced = True
                self.notify.emit("远程仓库有新提交，建议拉取以保持同步。", "info")
                log.info("检测到远程分支已更新: %s", sha[:8])
        elif local_sha:
            self._remote_advanced = False

    # ===================================================================== #
    # 刷新
    # ===================================================================== #

    def refresh_all(self, manual: bool = False) -> None:
        if not self.path:
            return
        path = self.path

        def job():
            status = GitEngine.status(path, untracked=True)
            sync = GitEngine.sync_state(path)
            info_data = GitEngine.repo_info(path, fetch_stats=False)
            return status, sync, info_data

        if manual:
            self._set_busy(True, "正在读取仓库状态…")
        self.tasks.run(
            job,
            on_success=lambda r: self._apply_refresh(*r),
            on_error=lambda m, d: (self._set_busy(False), show_error(self, "读取仓库失败", m, d)),
            label="刷新仓库状态",
        )
        self.refresh_history()
        self.refresh_branches()
        self.refresh_remotes()
        self.refresh_stash()
        if self.tabs.currentIndex() == getattr(self, "structure_index", -1):
            self.refresh_structure()

    def _apply_refresh(self, status: dict, sync: SyncState, info_data: dict) -> None:
        self._status = status
        self._sync = sync
        self._signature = _status_signature(status)
        self._remote_advanced = False
        self.name_label.setText(info_data.get("name") or os.path.basename(self.path))
        self.branch_badge.set_state(status.get("branch") or "无分支", "#2d6cdf")
        self._render_overview()
        self._render_changes()
        self._check_notice()
        self._set_busy(False)

    def _render_overview(self) -> None:
        status = self._status or {}
        sync = self._sync or SyncState()
        branch = status.get("branch") or "—"
        ahead, behind = sync.ahead, sync.behind

        self.ov_labels["current_branch"].setText(branch)
        self.ov_labels["upstream"].setText(sync.upstream or "未设置（推送后可自动建立）")
        if sync.has_upstream:
            self.ov_labels["divergence"].setText(f"领先 {ahead} 个提交 / 落后 {behind} 个提交")
        else:
            self.ov_labels["divergence"].setText("—")
        self.ov_labels["head"].setText(self._head_subject())
        self.ov_labels["author_time"].setText(self._head_time())
        self.ov_labels["remotes"].setText(self._remote_summary())
        staged = len(status.get("staged", {}))
        unstaged = len(status.get("unstaged", []))
        untracked = len(status.get("untracked", []))
        if status.get("clean", True):
            self.ov_labels["dirty"].setText("干净，无未提交更改")
            self.ov_labels["dirty"].setStyleSheet("color:#1a7f37;")
        else:
            self.ov_labels["dirty"].setText(
                f"已暂存 {staged} · 未暂存 {unstaged} · 未跟踪 {untracked}"
            )
            self.ov_labels["dirty"].setStyleSheet("color:#9a6700;")
        self.ov_labels["file_count"].setText(str(self._commit_count()))

        if sync.diverged:
            self.sync_badge.set_state("已分叉", "#b42318")
        elif sync.ahead and not sync.behind:
            self.sync_badge.set_state(f"领先 {sync.ahead}", "#9a6700")
        elif sync.behind and not sync.ahead:
            self.sync_badge.set_state(f"落后 {sync.behind}", "#2d6cdf")
        elif sync.has_upstream:
            self.sync_badge.set_state("与远程一致", "#1a7f37")
        else:
            self.sync_badge.set_state("无跟踪分支", "#656d76")

    def _check_notice(self) -> None:
        messages: list[tuple[str, str]] = []
        status = self._status or {}
        sync = self._sync or SyncState()
        if not status.get("clean", True) and sync.behind > 0:
            messages.append(
                (
                    "warning",
                    f"风险提示：本地有未提交的更改，同时远程领先 {sync.behind} 个提交。"
                    "此时直接拉取可能导致冲突，建议先提交或暂存本地改动。",
                )
            )
        if sync.diverged:
            messages.append(
                (
                    "error",
                    f"分支已分叉（本地领先 {sync.ahead}，远程领先 {sync.behind}）。"
                    "拉取时将尝试自动合并；若发生冲突会自动回滚，本地代码不会被覆盖。",
                )
            )
        if self._remote_advanced:
            messages.append(("info", "远程仓库出现了新的提交，建议点击「拉取并合并」同步。"))
        if not self._primary_remote():
            messages.append(
                (
                    "warning",
                    "尚未配置远程仓库地址。点击「发布到远程仓库」可直接在 GitHub / Gitee "
                    "创建仓库并推送本地内容，也可到「远程」标签页手动填写地址。",
                )
            )
        if not messages:
            self.notice.setVisible(False)
            return
        level, text = messages[0]
        colors = {
            "info": ("#1f56b8", "#eaf1fd"),
            "warning": ("#9a6700", "#fff8e1"),
            "error": ("#b42318", "#fdecea"),
        }
        fg, bg = colors[level]
        self.notice.setStyleSheet(
            f"background:{bg}; color:{fg}; border:1px solid {fg}33; border-radius:8px;"
            f" padding:9px 12px; font-weight:600;"
        )
        self.notice.setText("  ".join(t for _, t in messages))
        self.notice.setVisible(True)

    def _render_changes(self) -> None:
        status = self._status or {}
        self.change_tree.clear()
        staged_map = status.get("staged", {})
        groups = [
            ("staged", sorted(staged_map.keys()), staged_map),
            ("unstaged", sorted(status.get("unstaged", [])), {}),
            ("untracked", sorted(status.get("untracked", [])), {}),
        ]
        total = 0
        for key, files, extra in groups:
            if not files:
                continue
            total += len(files)
            root = QTreeWidgetItem([f"{GROUP_LABELS[key]}（{len(files)}）", ""])
            font = root.font(0)
            font.setBold(True)
            root.setFont(0, font)
            root.setForeground(0, QColor(GROUP_COLORS[key]))
            root.setExpanded(True)
            self.change_tree.addTopLevelItem(root)
            for rel in files:
                label = STAGED_LABELS.get(str(extra.get(rel, "")), "") if extra else ""
                if key == "unstaged":
                    label = "已修改"
                elif key == "untracked":
                    label = "新增"
                child = QTreeWidgetItem([rel, label])
                child.setData(0, Qt.UserRole, (key, rel))
                child.setForeground(0, QColor("#1f2328"))
                root.addChild(child)
        self.change_summary.setText(
            f"共 {total} 个变更文件" if total else "工作区干净，没有未提交的更改"
        )

    def refresh_history(self) -> None:
        if not self.path:
            return
        path = self.path
        self.tasks.run(
            GitEngine.history,
            path,
            200,
            on_success=self._render_history,
            on_error=lambda m, d: log.warning("读取提交历史失败: %s", m),
            label="读取提交历史",
        )

    def _render_history(self, commits: list[dict]) -> None:
        self.history_table.setRowCount(len(commits))
        for row, commit in enumerate(commits):
            values = [commit["short"], commit["author"], commit["date"], commit["subject"]]
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                if col == 0:
                    item.setForeground(QColor("#1f56b8"))
                    font = item.font()
                    font.setFamilies(["Cascadia Mono", "Consolas", "monospace"])
                    item.setFont(font)
                    item.setData(Qt.UserRole, commit["sha"])
                item.setToolTip(commit["message"])
                self.history_table.setItem(row, col, item)
        self.history_table.resizeRowsToContents()

    def refresh_branches(self) -> None:
        if not self.path:
            return
        path = self.path
        self.tasks.run(
            GitEngine.branches,
            path,
            on_success=self._render_branches,
            on_error=lambda m, d: log.warning("读取分支失败: %s", m),
            label="读取分支列表",
        )

    def _render_branches(self, data: dict) -> None:
        locals_ = data.get("local", [])
        remotes = data.get("remote", [])
        self.local_table.setRowCount(len(locals_))
        for row, branch in enumerate(locals_):
            name = ("★ " if branch["current"] else "") + branch["name"]
            state = "当前分支" if branch["current"] else (
                f"领先 {branch['ahead']} / 落后 {branch['behind']}"
                if branch["upstream"] and (branch["ahead"] or branch["behind"])
                else ("已同步" if branch["upstream"] else "无上游")
            )
            for col, value in enumerate([name, branch["upstream"] or "—", state, branch["short"]]):
                item = QTableWidgetItem(value)
                if branch["current"]:
                    item.setForeground(QColor("#1a7f37"))
                self.local_table.setItem(row, col, item)
        self.local_table.resizeRowsToContents()

        self.remote_table.setRowCount(len(remotes))
        for row, branch in enumerate(remotes):
            for col, value in enumerate([branch["name"], branch["short"]]):
                self.remote_table.setItem(row, col, QTableWidgetItem(value))
        self.remote_table.resizeRowsToContents()

    def refresh_remotes(self) -> None:
        if not self.path:
            return
        path = self.path
        self.tasks.run(
            GitEngine.remotes,
            path,
            on_success=self._render_remotes,
            on_error=lambda m, d: log.warning("读取远程列表失败: %s", m),
            label="读取远程列表",
        )

    def _render_remotes(self, remotes: list[dict]) -> None:
        self.remote_list.setRowCount(len(remotes))
        for row, remote in enumerate(remotes):
            self.remote_list.setItem(row, 0, QTableWidgetItem(remote["name"]))
            url_item = QTableWidgetItem(remote["url"])
            url_item.setToolTip(remote["url"])
            self.remote_list.setItem(row, 1, url_item)
        self.remote_list.resizeRowsToContents()
        self._render_overview()

    def refresh_stash(self) -> None:
        if not self.path:
            return
        path = self.path
        self.tasks.run(
            GitEngine.stash_list,
            path,
            on_success=self._render_stash,
            on_error=lambda m, d: log.debug("读取暂存列表失败: %s", m),
            label="读取暂存列表",
        )

    def _render_stash(self, items: list[dict]) -> None:
        self.stash_list.setRowCount(len(items))
        for row, item in enumerate(items):
            self.stash_list.setItem(row, 0, QTableWidgetItem(str(item["index"])))
            self.stash_list.setItem(row, 1, QTableWidgetItem(item["message"]))
        self.stash_list.resizeRowsToContents()

    # ===================================================================== #
    # 辅助
    # ===================================================================== #

    def _set_busy(
        self,
        busy: bool,
        message: str = "",
        indeterminate: bool = False,
        kind: str = "",
        cancellable: bool = False,
    ) -> None:
        self._busy = busy
        if busy:
            if not self.transfer.active:
                self.transfer.begin(
                    message or "正在执行…",
                    kind=kind or "处理中",
                    indeterminate=True,
                    cancellable=cancellable,
                )
            elif kind:
                self.transfer.kind_label.setText(kind)
                if cancellable and not self.transfer.stop_btn.isVisible():
                    self.transfer.enable_stop()
        elif self.transfer.active:
            self.transfer.finish("操作完成", ok=True)
        for btn in (
            self.btn_fetch, self.btn_pull, self.btn_push, self.btn_force_pull,
            self.btn_stash, self.btn_commit, self.btn_stage_all, self.btn_stage_sel,
            self.btn_unstage, self.btn_discard, self.btn_branch_switch,
            self.btn_branch_new, self.btn_branch_del,
            self.btn_publish, self.btn_remote_publish,
        ):
            btn.setEnabled(not busy)
        if message and busy:
            self.notify.emit(message, "info")

    def _on_detail(self, payload: dict) -> None:
        """接收引擎抛出的传输详情（阶段 / 字节 / 对象 / 速度 / 重试）。"""
        self.transfer.update_detail(payload)

    # ---------------- 停止上传（推送 / 发布） ----------------

    def _on_stop_transfer(self) -> None:
        """传输面板上的「停止」/「关闭」被点击。"""
        task = self._upload_task
        if task is not None and task.isRunning():
            log.info("用户请求停止上传")
            self.notify.emit("正在停止上传，请稍候…", "info")
            task.cancel()
            return
        # 任务已结束（成功 / 失败之后）：按钮此时是「关闭」，清掉面板即可
        self._upload_task = None
        self.transfer.reset()

    def _on_upload_stopped(self) -> None:
        """上传被用户停止后的收尾（任务线程已结束）。"""
        task = self._upload_task
        message = (getattr(task, "cancel_message", "") or "").strip() or "已停止上传。"
        self._upload_task = None
        self.transfer.finish(elide(message, 70), ok=False, cancelled=True)
        self._set_busy(False)
        self.notify.emit(elide(message, 120), "info")
        log.info("上传已停止：%s", message)
        self.refresh_all()
        self.changed.emit()

    def _progress_to_panel(self, text: str) -> None:
        """把引擎的进度文本同时投到通知区与传输详情日志。"""
        self.notify.emit(elide(text, 110), "info")
        self.transfer.set_message(elide(text, 220))

    def _primary_remote(self) -> dict | None:
        remotes = GitEngine.remotes(self.path) if self.path else []
        for remote in remotes:
            if remote["name"] == "origin":
                return remote
        return remotes[0] if remotes else None

    def _guess_platform(self, url: str) -> str:
        low = (url or "").lower()
        if "gitee.com" in low:
            return "gitee"
        if "github.com" in low:
            return "github"
        record = self.store.get(self.path) or {}
        return record.get("platform") or "github"

    def _credential(self) -> tuple[Credential, str, dict | None]:
        remote = self._primary_remote()
        if not remote:
            return Credential(), "github", None
        platform = self._guess_platform(remote["url"])
        return (
            Credential(self.cfg.username(platform), self.cfg.token(platform)),
            platform,
            remote,
        )

    def _head_subject(self) -> str:
        if not self.path:
            return "—"
        try:
            with __import__("dulwich").porcelain.open_repo_closing(self.path) as repo:
                head = repo.head()
                commit = repo[head]
                message = commit.message.decode("utf-8", "replace").strip()
                return elide(message.splitlines()[0] if message else "", 60)
        except Exception:
            return "（暂无提交）"

    def _head_time(self) -> str:
        if not self.path:
            return "—"
        try:
            import time as _time

            with __import__("dulwich").porcelain.open_repo_closing(self.path) as repo:
                commit = repo[repo.head()]
                return _time.strftime("%Y-%m-%d %H:%M:%S", _time.localtime(commit.commit_time))
        except Exception:
            return "—"

    def _commit_count(self) -> int:
        if not self.path:
            return 0
        try:
            return GitEngine.commit_count(self.path)
        except Exception:
            return 0

    def _remote_summary(self) -> str:
        remotes = GitEngine.remotes(self.path) if self.path else []
        if not remotes:
            return "未配置"
        return "；".join(f"{r['name']} → {r['url']}" for r in remotes)

    # ===================================================================== #
    # 变更页操作
    # ===================================================================== #

    def _selected_changes(self) -> list[tuple[str, str]]:
        result = []
        for item in self.change_tree.selectedItems():
            data = item.data(0, Qt.UserRole)
            if data:
                result.append(tuple(data))
        if not result:
            current = self.change_tree.currentItem()
            if current:
                data = current.data(0, Qt.UserRole)
                if data:
                    result.append(tuple(data))
        return result

    def _on_change_selected(self) -> None:
        selected = self._selected_changes()
        if not selected or not self.path:
            return
        kind, rel = selected[0]
        path = self.path
        if kind == "untracked":
            full = os.path.join(path, rel)
            try:
                if os.path.getsize(full) > 400_000:
                    self.diff_view.setPlainText("（未跟踪文件，内容过大，未预览）")
                    return
                with open(full, "r", encoding="utf-8", errors="replace") as fh:
                    self.diff_view.set_diff(
                        "diff --git a/{0} b/{0}\nnew file\n".format(rel)
                        + "".join("+" + line for line in fh.readlines())
                    )
            except Exception as exc:
                self.diff_view.setPlainText(f"（无法预览该文件：{exc}）")
            return

        staged = kind == "staged"
        self.tasks.run(
            GitEngine.diff_text,
            path,
            file_path=rel,
            staged=staged,
            on_success=self.diff_view.set_diff,
            on_error=lambda m, d: self.diff_view.setPlainText(f"（无法显示差异：{m}）"),
            label=f"生成差异 {rel}",
        )

    def _stage(self, mode: str = "all") -> None:
        if not self.path:
            return
        if mode == "all":
            paths = list(self._status.get("unstaged", [])) + list(self._status.get("untracked", []))
        else:
            paths = [rel for kind, rel in self._selected_changes() if kind in ("unstaged", "untracked")]
        if not paths:
            self.notify.emit("没有需要暂存的更改。", "info")
            return
        self._run_simple(
            lambda: GitEngine.stage(self.path, paths),
            f"已暂存 {len(paths)} 个文件",
            "暂存更改",
        )

    def _unstage(self) -> None:
        if not self.path:
            return
        paths = [rel for kind, rel in self._selected_changes() if kind == "staged"]
        if not paths:
            paths = list(self._status.get("staged", {}).keys())
        if not paths:
            self.notify.emit("没有需要取消暂存的文件。", "info")
            return
        self._run_simple(
            lambda: GitEngine.unstage(self.path, paths),
            f"已取消暂存 {len(paths)} 个文件",
            "取消暂存",
        )

    def _discard(self) -> None:
        if not self.path:
            return
        selected = self._selected_changes()
        tracked = [rel for kind, rel in selected if kind in ("staged", "unstaged")]
        untracked = [rel for kind, rel in selected if kind == "untracked"]
        if not tracked and not untracked:
            self.notify.emit("请先选择要放弃更改的文件。", "info")
            return
        preview = "\n".join(f"  • {p}" for p in (tracked + untracked)[:12])
        if not confirm(
            self,
            "放弃更改",
            "以下文件的改动将被永久丢弃，且无法恢复：\n"
            f"{preview}\n\n确定要继续吗？",
            danger=True,
        ):
            return
        self._run_simple(
            lambda: GitEngine.discard(self.path, tracked, untracked),
            "已放弃所选文件的更改",
            "放弃更改",
        )

    # ===================================================================== #
    # 提交
    # ===================================================================== #

    def do_commit(self) -> None:
        if not self.path:
            return
        message = self.commit_message.toPlainText().strip()
        if not message:
            self.notify.emit("请填写提交说明。", "warning")
            return
        author = self.commit_author.text().strip() or "GitDesk"
        email = self.commit_email.text().strip() or "gitdesk@localhost"
        path = self.path
        staging = self.commit_stage_all.isChecked()

        self.cfg.update_settings({"git_username": author, "git_email": email})

        def job():
            if staging:
                status = GitEngine.status(path, untracked=True)
                paths = list(status.get("unstaged", [])) + list(status.get("untracked", []))
                if not paths and not status.get("staged"):
                    raise GitError("工作区没有可提交的更改。")
                if paths:
                    GitEngine.stage(path, paths)
            elif not GitEngine.status(path, untracked=True).get("staged"):
                raise GitError("暂存区为空，请先暂存需要提交的文件。")
            return GitEngine.commit(path, message, author, email)

        self._set_busy(True, "正在创建提交…")
        self.tasks.run(
            job,
            on_success=self._on_commit_done,
            on_error=self._on_action_error,
            label="创建提交",
        )

    def _on_commit_done(self, sha: str) -> None:
        self._set_busy(False)
        self.commit_message.clear()
        self.notify.emit(f"提交成功：{sha[:8]}", "success")
        log.info("提交成功 %s", sha)
        self.refresh_all()
        self.changed.emit()

    # ===================================================================== #
    # 同步
    # ===================================================================== #

    def do_fetch(self) -> None:
        if not self.path:
            return
        credential, platform, remote = self._credential()
        if not remote:
            show_error(self, "缺少远程配置", "尚未配置远程仓库地址，请先到「远程」标签页添加。")
            return
        path = self.path
        self._set_busy(
            True, f"正在从 {remote['name']} 获取更新…", indeterminate=True, kind="下载 · 获取"
        )
        self.tasks.run(
            GitEngine.fetch,
            path,
            remote["name"],
            credential=credential,
            platform=platform,
            on_progress=self._progress_to_panel,
            on_detail=self._on_detail,
            on_success=self._on_fetch_done,
            on_error=self._on_action_error,
            label="获取远程更新",
        )

    def _on_fetch_done(self, result: dict) -> None:
        updated = len(result.get("updated_remote_refs", []))
        if result.get("updated_remote_refs"):
            self.transfer.finish(f"已获取 {updated} 个远程分支的更新", ok=True)
            self.notify.emit(f"已获取远程更新，{updated} 个远程分支发生变化。", "success")
            log.info("获取远程更新完成: %s", result.get("updated_remote_refs"))
        else:
            self.transfer.finish("远程无新内容，已是最新", ok=True)
            self.notify.emit("远程无新内容，已是最新状态。", "info")
        self._set_busy(False)
        self.refresh_all()
        self.changed.emit()

    def do_pull_flow(self) -> None:
        """预检 + 拉取：先取回远程引用，评估分歧，再决定合并方式。"""
        if not self.path:
            return
        credential, platform, remote = self._credential()
        if not remote:
            show_error(self, "缺少远程配置", "尚未配置远程仓库地址，请先到「远程」标签页添加。")
            return
        path = self.path

        def precheck(progress=None, detail=None):
            GitEngine.fetch(
                path,
                remote["name"],
                credential=credential,
                platform=platform,
                progress=progress,
                detail=detail,
            )
            return GitEngine.status(path, untracked=True), GitEngine.sync_state(path)

        self._set_busy(
            True, "正在预检查远程变更…", indeterminate=True, kind="下载 · 拉取"
        )
        self.tasks.run(
            precheck,
            on_progress=self._progress_to_panel,
            on_detail=self._on_detail,
            on_success=lambda r: self._after_precheck(r, credential, platform, remote),
            on_error=self._on_action_error,
            label="拉取预检",
        )

    def _after_precheck(self, result, credential, platform, remote) -> None:
        status, sync = result
        self._status = status
        self._sync = sync
        self._render_overview()
        self._check_notice()
        self.transfer.finish("预检查完成", ok=True)
        self._set_busy(False)

        if sync.behind == 0:
            if sync.diverged:
                pass
            self.notify.emit("本地已是最新，无需拉取。", "success")
            self.refresh_all()
            return

        dirty = not status.get("clean", True)
        stash_first = False

        if dirty:
            from PySide6.QtWidgets import QMessageBox as _MB

            box = _MB(self)
            box.setWindowTitle("存在未提交的更改")
            box.setIcon(_MB.Warning)
            box.setText(
                f"本地有 {len(status.get('unstaged', [])) + len(status.get('staged', {}))} 个已跟踪文件的改动，"
                "以及 "
                f"{len(status.get('untracked', []))} 个未跟踪文件。\n\n"
                "直接拉取可能覆盖这些改动。请选择处理方式："
            )
            stash_btn = box.addButton("先暂存更改再拉取", _MB.AcceptRole)
            cancel_btn = box.addButton("取消", _MB.RejectRole)
            box.setDefaultButton(stash_btn)
            box.exec()
            if box.clickedButton() is cancel_btn:
                self.notify.emit("已取消拉取操作。", "info")
                return
            stash_first = True

        if sync.diverged:
            if not confirm(
                self,
                "分支已分叉",
                f"本地 {sync.ahead} 个提交与远程 {sync.behind} 个提交已分叉。\n\n"
                "将尝试自动合并；若产生冲突会立即回滚，本地代码不会被覆盖。\n是否继续？",
            ):
                self.notify.emit("已取消拉取操作。", "info")
                return

        self._execute_pull(credential, platform, remote["name"], stash_first, sync.behind)

    def _execute_pull(self, credential, platform, remote_name, stash_first: bool, behind: int) -> None:
        path = self.path

        def job(progress=None, detail=None):
            if stash_first:
                GitEngine.stash_push(path)
            try:
                result = GitEngine.pull(
                    path,
                    remote_name,
                    credential=credential,
                    platform=platform,
                    progress=progress,
                    detail=detail,
                    strategy="auto",
                )
            except BaseException:
                if stash_first:
                    try:
                        GitEngine.stash_pop(path)
                    except Exception as exc:  # noqa: BLE001
                        log.warning("自动恢复暂存失败: %s", exc)
                        raise GitError(
                            "拉取失败，且自动恢复暂存内容时出现冲突。"
                            "请到「暂存区」标签页手动执行恢复。"
                        ) from exc
                raise
            if stash_first:
                try:
                    GitEngine.stash_pop(path)
                except Exception as exc:  # noqa: BLE001
                    log.warning("恢复暂存内容失败: %s", exc)
                    return {**result, "stash_restore_failed": str(exc)}
                result["stash_restored"] = True
            return result

        self._set_busy(
            True, "正在拉取并合并…", indeterminate=True, kind="下载 · 拉取"
        )
        self.tasks.run(
            job,
            on_progress=self._progress_to_panel,
            on_detail=self._on_detail,
            on_success=self._on_pull_done,
            on_error=self._on_action_error,
            label="拉取并合并",
        )

    def _on_pull_done(self, result: dict) -> None:
        kind = result.get("result")
        mapping = {
            "fast_forward": "已快进到远程最新版本。",
            "merged": "已完成合并，产生了一个合并提交。",
            "up_to_date": "本地已是最新，无需更新。",
            "no_upstream": "未找到对应的远程跟踪分支，请检查远程配置。",
            "forced": "已使用远程内容覆盖本地分支。",
        }
        message = mapping.get(kind, "同步完成。")
        if result.get("stash_restore_failed"):
            message += "（暂存的改动恢复时发生冲突，请到「暂存区」手动处理）"
        self.transfer.finish(message, ok=not result.get("stash_restore_failed"))
        self._set_busy(False)
        if result.get("stash_restore_failed"):
            self.notify.emit(message, "warning")
        else:
            self.notify.emit(message, "success")
        log.info("拉取完成: %s", result)
        self.refresh_all()
        self.changed.emit()

    def do_force_pull(self) -> None:
        if not self.path:
            return
        credential, platform, remote = self._credential()
        if not remote:
            show_error(self, "缺少远程配置", "尚未配置远程仓库地址。")
            return
        if not confirm(
            self,
            "强制覆盖本地",
            "该操作会使用远程分支内容强制覆盖本地分支，本地未推送的提交与未提交的更改都会丢失。\n\n"
            "确定继续吗？",
            danger=True,
        ):
            return
        path = self.path

        def job(progress=None, detail=None):
            return GitEngine.pull(
                path,
                remote["name"],
                credential=credential,
                platform=platform,
                progress=progress,
                detail=detail,
                strategy="force_local",
            )

        self._set_busy(
            True, "正在强制同步…", indeterminate=True, kind="下载 · 强制覆盖"
        )
        self.tasks.run(
            job,
            on_progress=self._progress_to_panel,
            on_detail=self._on_detail,
            on_success=self._on_pull_done,
            on_error=self._on_action_error,
            label="强制同步",
        )

    def do_push(self) -> None:
        if not self.path:
            return
        credential, platform, remote = self._credential()
        if not remote:
            show_error(self, "缺少远程配置", "尚未配置远程仓库地址，请先到「远程」标签页添加。")
            return
        if not credential.token:
            show_error(
                self,
                "缺少访问令牌",
                f"推送需要 {platapi.PLATFORMS.get(platform, platapi.PLATFORMS['github']).label} "
                "的访问令牌，请先到「账号设置」完成配置。",
            )
            return

        branch = self._status.get("branch") or ""
        if not branch:
            show_error(self, "无法推送", "当前仓库处于游离 HEAD 状态，请先切换到具体分支。")
            return
        path = self.path

        def job(progress=None, detail=None, cancel=None):
            GitEngine.push(
                path,
                remote["name"],
                branch,
                credential=credential,
                platform=platform,
                progress=progress,
                detail=detail,
                set_upstream=True,
                cancel=cancel,
            )
            return GitEngine.sync_state(path)

        self._set_busy(
            True, f"正在推送分支 {branch}…", indeterminate=True,
            kind="上传 · 推送", cancellable=True,
        )
        self._upload_task = self.tasks.run(
            job,
            on_progress=self._progress_to_panel,
            on_detail=self._on_detail,
            on_success=self._on_push_done,
            on_error=self._on_action_error,
            on_cancelled=self._on_upload_stopped,
            label="推送本地提交",
        )

    def _on_push_done(self, sync) -> None:
        self._upload_task = None
        self.transfer.finish("推送成功，本地与远程已同步", ok=True)
        self._set_busy(False)
        self.notify.emit("推送成功，本地与远程已同步。", "success")
        log.info("推送成功")
        self.refresh_all()
        self.changed.emit()

    # ===================================================================== #
    # 发布到远程仓库（GitHub / Gitee）
    # ===================================================================== #

    def publish_to_remote(self) -> None:
        """在所选平台新建远程仓库，并把本地仓库推送上去。"""
        if not self.path:
            self.notify.emit("请先选择或导入一个本地仓库。", "info")
            return
        if not (self.cfg.is_connected("github") or self.cfg.is_connected("gitee")):
            self.notify.emit("请先在「账号设置」中完成 GitHub 或 Gitee 的授权配置。", "warning")
            self.request_accounts.emit()
            return

        existing = self._primary_remote()
        if existing and not confirm(
            self,
            "已存在远程地址",
            f"当前仓库已配置远程 {existing['name']}：\n{existing['url']}\n\n"
            "继续发布会新建一个远程仓库，并把该远程的地址改为新仓库。是否继续？",
        ):
            return

        dialog = PublishDialog(self.cfg, self.path, self)
        if dialog.exec() != QDialog.Accepted:
            if dialog.want_accounts:
                self.request_accounts.emit()
            return

        params = dict(dialog.result_payload)
        platform = str(params.get("platform") or "github")
        meta = platapi.PLATFORMS[platform]
        credential = Credential(self.cfg.username(platform), self.cfg.token(platform))
        path = self.path
        branch = str((self._status or {}).get("branch") or "")
        author = str(self.cfg.get_setting("git_username", "GitDesk"))
        email = str(self.cfg.get_setting("git_email", "gitdesk@localhost"))

        def job(progress=None, detail=None, cancel=None):
            try:
                client = platapi.make_client(self.cfg, platform)
                if progress:
                    progress(f"正在 {meta.label} 创建远程仓库 {params['name']}…")
                repo = client.create_repo(
                    params["name"], params["description"], bool(params["private"])
                )
                url = repo.get("clone_url") or f"{meta.web_host}/{repo.get('full_name')}.git"
                plan = PublishPlan(
                    path=path,
                    url=url,
                    branch=branch,
                    commit_message=str(params.get("commit_message") or "Initial commit"),
                    author_name=author,
                    author_email=email,
                    create_readme=bool(params.get("create_readme")),
                    readme_title=str(params["name"]),
                    readme_description=str(params.get("description") or ""),
                )
                try:
                    outcome = publish_local(
                        plan, credential, platform, progress, detail, cancel=cancel
                    )
                except GitError as exc:
                    created = repo.get("full_name") or params["name"]
                    if getattr(exc, "cancelled", False):
                        raise GitError(
                            f"已停止发布。远程仓库 {created} 已创建成功，"
                            "本地内容尚未推送完成。\n"
                            "稍后可在「远程」标签页确认地址，并用「推送本地提交」重试。",
                            exc.detail,
                        ) from exc
                    raise GitError(
                        f"远程仓库 {created} 已创建成功，但本地推送失败：{exc.message}\n"
                        "稍后可在「远程」标签页确认地址，并用「推送本地提交」重试。",
                        exc.detail,
                    ) from exc
            except platapi.ApiError as exc:
                raise GitError(exc.message, exc.detail) from exc
            return {"repo": repo, "outcome": outcome, "platform": platform}

        self._set_busy(
            True, "正在创建远程仓库并推送…", indeterminate=True,
            kind="上传 · 发布", cancellable=True,
        )
        log.info("开始发布到 %s：%s -> %s", meta.label, path, params["name"])
        self._upload_task = self.tasks.run(
            job,
            on_progress=self._progress_to_panel,
            on_detail=self._on_detail,
            on_success=self._on_publish_done,
            on_error=self._on_action_error,
            on_cancelled=self._on_upload_stopped,
            label="发布到远程仓库",
        )

    def _on_publish_done(self, payload: dict) -> None:
        self._upload_task = None
        self.transfer.finish("已创建远程仓库并完成推送", ok=True)
        self._set_busy(False)
        repo = payload.get("repo") or {}
        outcome = payload.get("outcome")
        platform = str(payload.get("platform") or "github")
        full_name = repo.get("full_name") or ""

        try:
            self.store.update(
                self.path,
                platform=platform,
                full_name=full_name,
                remote=outcome.remote_name if outcome else "origin",
                remote_url=outcome.remote_url if outcome else "",
            )
        except Exception:  # noqa: BLE001 - 记录失败不影响发布结果
            log.debug("更新仓库记录失败", exc_info=True)

        lines = [f"平台：{platapi.PLATFORMS[platform].label}",
                 f"远程仓库：{full_name or '（已创建）'}"]
        if outcome:
            lines.append(f"推送分支：{outcome.branch}（已建立跟踪关系）")
            if outcome.created_readme:
                lines.append("已自动创建 README.md")
            if outcome.created_commit:
                lines.append(f"已创建首个提交：{outcome.commit_sha[:8]}")
            if outcome.staged_files:
                lines.append(f"纳入首个提交的文件：{outcome.staged_files} 个")
        visit = repo.get("html_url") or (outcome.remote_url if outcome else "")
        if visit:
            lines.append(f"访问地址：{visit}")
        text = "\n".join(lines)
        log.info("发布成功 | %s", text.replace("\n", " | "))
        info(self, "发布完成", "已在远程创建仓库并完成推送。\n\n" + text)
        label = full_name or repo.get("name") or "新仓库"
        self.notify.emit(f"已发布到 {platapi.PLATFORMS[platform].label}：{label}", "success")
        self.refresh_all()
        self.changed.emit()

    # ===================================================================== #
    # 分支 / 远程操作
    # ===================================================================== #

    def _switch_branch(self) -> None:
        row = self.local_table.currentRow()
        if row < 0:
            self.notify.emit("请先选择要切换的分支。", "info")
            return
        name_item = self.local_table.item(row, 0)
        if not name_item:
            return
        name = name_item.text().replace("★ ", "")
        if not self.path:
            return
        status = self._status or {}
        force = False
        if not status.get("clean", True):
            from PySide6.QtWidgets import QMessageBox

            box = QMessageBox(self)
            box.setWindowTitle("工作区存在更改")
            box.setIcon(QMessageBox.Warning)
            box.setText("切换分支可能覆盖当前未提交的更改。请选择处理方式：")
            force_btn = box.addButton("丢弃更改并切换", QMessageBox.DestructiveRole)
            stash_btn = box.addButton("先暂存更改", QMessageBox.AcceptRole)
            cancel_btn = box.addButton("取消", QMessageBox.RejectRole)
            box.exec()
            clicked = box.clickedButton()
            if clicked is cancel_btn:
                return
            if clicked is force_btn:
                force = True
            else:
                self.do_stash_push()
                QTimer.singleShot(1200, lambda: self._checkout(name, False))
                return
        self._checkout(name, force)

    def _checkout(self, name: str, force: bool) -> None:
        path = self.path
        self._run_simple(
            lambda: GitEngine.checkout(path, name, force=force),
            f"已切换到分支 {name}",
            "切换分支",
        )

    def _new_branch(self) -> None:
        if not self.path:
            return
        name, ok = QInputDialog.getText(self, "新建分支", "请输入新分支名称：")
        if not ok or not name.strip():
            return
        path = self.path
        self._run_simple(
            lambda: GitEngine.create_branch(path, name.strip()),
            f"已创建并切换到分支 {name.strip()}",
            "新建分支",
        )

    def _delete_branch(self) -> None:
        row = self.local_table.currentRow()
        if row < 0:
            self.notify.emit("请先选择要删除的分支。", "info")
            return
        item = self.local_table.item(row, 0)
        if not item:
            return
        name = item.text().replace("★ ", "")
        if not confirm(self, "删除分支", f"确定删除本地分支 {name} 吗？该分支未合并的提交将无法通过分支找回。", danger=True):
            return
        path = self.path
        self._run_simple(lambda: GitEngine.delete_branch(path, name), f"已删除分支 {name}", "删除分支")

    def _add_remote(self) -> None:
        if not self.path:
            return
        name, ok = QInputDialog.getText(self, "添加远程", "远程名称：", text="origin")
        if not ok or not name.strip():
            return
        url, ok = QInputDialog.getText(self, "添加远程", "远程地址（HTTPS）：")
        if not ok or not url.strip():
            return
        path = self.path
        self._run_simple(
            lambda: GitEngine.add_remote(path, name.strip(), url.strip()),
            f"已添加远程 {name.strip()}",
            "添加远程",
        )

    def _edit_remote(self) -> None:
        row = self.remote_list.currentRow()
        if row < 0 or not self.path:
            self.notify.emit("请先选择要修改的远程。", "info")
            return
        name = self.remote_list.item(row, 0).text()
        old_url = self.remote_list.item(row, 1).text()
        url, ok = QInputDialog.getText(self, "修改远程地址", f"{name} 的地址：", text=old_url)
        if not ok or not url.strip():
            return
        path = self.path
        self._run_simple(
            lambda: GitEngine.set_remote_url(path, name, url.strip()),
            f"已更新远程 {name} 地址",
            "修改远程地址",
        )

    def _remove_remote(self) -> None:
        row = self.remote_list.currentRow()
        if row < 0 or not self.path:
            self.notify.emit("请先选择要删除的远程。", "info")
            return
        name = self.remote_list.item(row, 0).text()
        if not confirm(self, "删除远程", f"确定删除远程 {name} 吗？", danger=True):
            return
        path = self.path
        self._run_simple(lambda: GitEngine.remove_remote(path, name), f"已删除远程 {name}", "删除远程")

    # ===================================================================== #
    # 暂存区
    # ===================================================================== #

    def do_stash_push(self) -> None:
        if not self.path:
            return
        if (self._status or {}).get("clean", True):
            self.notify.emit("工作区是干净的，无需暂存。", "info")
            return
        path = self.path
        self._run_simple(lambda: GitEngine.stash_push(path), "已暂存当前更改", "暂存更改")

    def do_stash_pop(self) -> None:
        if not self.path:
            return
        if not confirm(self, "恢复暂存", "确定恢复最近一次的暂存内容吗？可能与当前工作区产生冲突。", danger=True):
            return
        path = self.path
        self._run_simple(lambda: GitEngine.stash_pop(path), "已恢复暂存内容", "恢复暂存")

    # ===================================================================== #
    # 提交详情
    # ===================================================================== #

    def _on_commit_selected(self) -> None:
        row = self.history_table.currentRow()
        if row < 0 or not self.path:
            return
        item = self.history_table.item(row, 0)
        if not item:
            return
        sha = item.data(Qt.UserRole)
        pair = self.tasks.run(
            lambda: (GitEngine.commit_detail(self.path, sha), GitEngine.diff_text(self.path, commit=sha)),
            on_success=self._on_commit_detail,
            on_error=lambda m, d: self.commit_diff.setPlainText(f"（无法加载提交详情：{m}）"),
            label="加载提交详情",
        )
        self._last_detail_task = pair

    def _on_commit_detail(self, result) -> None:
        detail, diff = result
        header = (
            f"提交 {detail['sha']}\n"
            f"作者 {detail['author']} <{detail['email']}>\n"
            f"时间 {detail['date']}\n"
            f"父提交 {', '.join(p[:8] for p in detail['parents']) or '（根提交）'}\n"
            f"变更文件 {len(detail['files'])} 个\n"
            f"{'-' * 60}\n"
        )
        files = "\n".join(f"  [{f['status']}] {f['path']}" for f in detail["files"][:60])
        self.commit_diff.set_diff(header + files + "\n" + "=" * 60 + "\n" + diff)

    # ===================================================================== #
    # 公共执行包装
    # ===================================================================== #

    def _run_simple(self, fn, success_message: str, label: str) -> None:
        if not self.path:
            return
        self._set_busy(True, f"正在执行：{label}…", indeterminate=True)
        pair = self.tasks.run(
            fn,
            on_success=lambda _: self._on_simple_done(success_message),
            on_error=self._on_action_error,
            label=label,
        )
        self._last_task = pair

    def _on_simple_done(self, message: str) -> None:
        self._set_busy(False)
        self.notify.emit(message, "success")
        log.info(message)
        self.refresh_all()
        self.changed.emit()

    def _on_action_error(self, message: str, detail: str) -> None:
        self.transfer.finish(elide(message, 60), ok=False)
        self._set_busy(False)
        self.notify.emit(elide(message, 90), "error")
        show_error(self, "操作失败", message, detail)


def _section_label(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("H2")
    return label


#: 常见后缀 → 展示用的文件类型
_FILE_KINDS = {
    ".py": "Python",
    ".js": "JavaScript",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".jsx": "JavaScript",
    ".java": "Java",
    ".go": "Go",
    ".rs": "Rust",
    ".c": "C",
    ".h": "C/C++ 头文件",
    ".cpp": "C++",
    ".cs": "C#",
    ".rb": "Ruby",
    ".php": "PHP",
    ".swift": "Swift",
    ".kt": "Kotlin",
    ".md": "Markdown",
    ".txt": "文本",
    ".json": "JSON",
    ".yaml": "YAML",
    ".yml": "YAML",
    ".toml": "TOML",
    ".ini": "配置",
    ".cfg": "配置",
    ".html": "HTML",
    ".css": "样式表",
    ".scss": "样式表",
    ".sh": "Shell 脚本",
    ".bat": "批处理",
    ".ps1": "PowerShell",
    ".sql": "SQL",
    ".png": "图片",
    ".jpg": "图片",
    ".jpeg": "图片",
    ".gif": "图片",
    ".svg": "矢量图",
    ".ico": "图标",
    ".pdf": "PDF",
    ".zip": "压缩包",
    ".exe": "可执行文件",
    ".dll": "动态库",
}


def _file_kind(name: str) -> str:
    """根据文件名后缀推断展示类型。"""
    ext = os.path.splitext(name or "")[1].lower()
    if not ext:
        return "文件"
    return _FILE_KINDS.get(ext, ext.lstrip(".").upper() + " 文件")


def _status_signature(status: dict) -> str:
    return "|".join(
        [
            status.get("branch", ""),
            ",".join(sorted(status.get("staged", {}).keys())),
            ",".join(status.get("unstaged", [])),
            ",".join(status.get("untracked", [])),
        ]
    )


class ReposPage(QWidget):
    """本地仓库管理页。"""

    repo_added = Signal(str)
    request_accounts = Signal()  # 转发详情面板的“前往账号设置”请求

    def __init__(self, cfg, store: RepoStore, tasks, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.cfg = cfg
        self.store = store
        self.tasks = tasks

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ---- 左侧仓库列表 ----
        left = QWidget()
        left.setFixedWidth(272)
        lv = QVBoxLayout(left)
        lv.setContentsMargins(16, 18, 14, 16)
        lv.setSpacing(10)

        title = QLabel("本地仓库")
        title.setObjectName("H1")
        lv.addWidget(title)

        hint = QLabel("已纳入管理的仓库，点击即可查看状态并同步。")
        hint.setObjectName("Faint")
        hint.setWordWrap(True)
        lv.addWidget(hint)

        self.repo_list = QListWidget()
        self.repo_list.currentItemChanged.connect(self._on_repo_changed)
        lv.addWidget(self.repo_list, 1)

        row1 = QHBoxLayout()
        row1.setSpacing(6)
        self.import_btn = QPushButton("导入本地仓库")
        self.import_btn.setProperty("variant", "primary")
        self.import_btn.clicked.connect(self.import_repo)
        self.init_btn = QPushButton("初始化新仓库")
        self.init_btn.setProperty("variant", "ghost")
        self.init_btn.clicked.connect(self.init_repo)
        row1.addWidget(self.import_btn, 1)
        row1.addWidget(self.init_btn)
        lv.addLayout(row1)

        row2 = QHBoxLayout()
        row2.setSpacing(6)
        self.refresh_list_btn = QPushButton("刷新列表")
        self.refresh_list_btn.clicked.connect(self.reload)
        self.remove_btn = QPushButton("移出列表")
        self.remove_btn.setProperty("variant", "danger")
        self.remove_btn.clicked.connect(self.remove_repo)
        row2.addWidget(self.refresh_list_btn)
        row2.addWidget(self.remove_btn)
        lv.addLayout(row2)
        layout.addWidget(left)

        # ---- 右侧详情 ----
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(16, 18, 20, 16)
        rv.setSpacing(10)
        self.detail = RepoDetailPanel(cfg, store, tasks)
        self.detail.changed.connect(self._refresh_current_item)
        self.detail.notify.connect(self._on_notify)
        self.detail.request_accounts.connect(self.request_accounts)
        self.detail.setVisible(False)
        rv.addWidget(self.detail)

        self.empty = QLabel(
            "尚未选择仓库。\n\n"
            "· 点击左侧「初始化新仓库」新建并一键发布到 GitHub / Gitee\n"
            "· 「导入本地仓库」把已有的 Git 项目纳入管理\n"
            "· 或前往「远程仓库」页面克隆一个项目"
        )
        self.empty.setAlignment(Qt.AlignCenter)
        self.empty.setObjectName("Muted")
        rv.addWidget(self.empty, 1)
        layout.addWidget(right, 1)

        self._notice_handler = None
        self.reload()

    # ---------------- 通知桥接 ---------------- #

    def set_notifier(self, handler) -> None:
        self._notice_handler = handler

    def _on_notify(self, message: str, level: str) -> None:
        if self._notice_handler:
            self._notice_handler(message, level)

    # ---------------- 列表 ---------------- #

    def reload(self) -> None:
        current = self.current_path()
        self.repo_list.clear()
        items = self.store.all()
        valid = 0
        for record in items:
            path = record.get("path", "")
            exists = os.path.isdir(path)
            name = record.get("name") or os.path.basename(path)
            label = name if exists else f"{name}（路径不存在）"
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, path)
            if exists:
                remote = record.get("full_name") or record.get("remote_url") or ""
                if remote:
                    item.setToolTip(f"{path}\n{remote}")
                else:
                    item.setToolTip(path)
                valid += 1
            else:
                item.setForeground(QColor("#b42318"))
            self.repo_list.addItem(item)
        if current:
            self.select_path(current)
        elif valid:
            self.repo_list.setCurrentRow(0)
        else:
            self.detail.setVisible(False)
            self.empty.setVisible(True)

    def current_path(self) -> str:
        item = self.repo_list.currentItem()
        return item.data(Qt.UserRole) if item else ""

    def select_path(self, path: str) -> bool:
        for row in range(self.repo_list.count()):
            item = self.repo_list.item(row)
            if item.data(Qt.UserRole) == path:
                self.repo_list.setCurrentRow(row)
                return True
        return False

    def _on_repo_changed(self, current: QListWidgetItem | None, _previous=None) -> None:
        if not current:
            self.detail.setVisible(False)
            self.empty.setVisible(True)
            return
        path = current.data(Qt.UserRole)
        if not os.path.isdir(path):
            self.detail.setVisible(False)
            self.empty.setVisible(True)
            self.empty.setText("该仓库路径不存在，可能已被移动或删除。\n可从列表中移除后重新导入。")
            return
        self.empty.setVisible(False)
        self.detail.setVisible(True)
        self.detail.set_repo(path)

    def _refresh_current_item(self) -> None:
        item = self.repo_list.currentItem()
        if item and self.detail.path:
            self.store.update(self.detail.path, last_sync=_now())

    # ---------------- 导入 / 初始化 / 移除 ---------------- #

    def import_repo(self) -> None:
        start = self.cfg.get_setting("clone_dir", "") or os.path.expanduser("~")
        chosen = QFileDialog.getExistingDirectory(self, "选择已有的 Git 仓库目录", start)
        if not chosen:
            return
        self._register_path(chosen)

    def register_path(self, path: str) -> bool:
        """供外部（克隆完成后）登记的入口。"""
        return self._register_path(path)

    def _register_path(self, path: str) -> bool:
        if not GitEngine.is_git_repo(path):
            show_error(
                self,
                "不是 Git 仓库",
                f"目录 {path} 中未发现 Git 仓库信息。\n\n"
                "如果这是普通目录，可先在左侧点击「初始化新仓库」。",
            )
            return False
        remotes = GitEngine.remotes(path)
        platform = "github"
        remote_url = ""
        full_name = ""
        if remotes:
            remote_url = remotes[0]["url"]
            low = remote_url.lower()
            if "gitee.com" in low:
                platform = "gitee"
            full_name = _full_name_from_url(remote_url)
        record = self.store.add(
            path,
            platform=platform,
            remote_url=remote_url,
            full_name=full_name,
            source="import",
        )
        log.info("导入本地仓库: %s", path)
        self.reload()
        self.select_path(record["path"])
        if self._notice_handler:
            self._notice_handler(f"已导入仓库：{os.path.basename(path)}", "success")
        return True

    def init_repo(self) -> None:
        parent = QFileDialog.getExistingDirectory(
            self, "选择新仓库所在目录", self.cfg.get_setting("clone_dir", "") or os.path.expanduser("~")
        )
        if not parent:
            return
        name, ok = QInputDialog.getText(self, "初始化仓库", "仓库名称：")
        if not ok or not name.strip():
            return
        target = os.path.join(parent, name.strip())
        try:
            GitEngine.init(target)
        except Exception as exc:  # noqa: BLE001
            show_error(self, "初始化失败", str(exc))
            return
        if not self._register_path(target):
            return
        log.info("初始化新仓库: %s", target)
        notify = self._notice_handler or (lambda *_: None)
        if not (self.cfg.is_connected("github") or self.cfg.is_connected("gitee")):
            notify(
                f"仓库 {os.path.basename(target)} 已创建。完成账号授权后即可一键发布到 GitHub / Gitee。",
                "info",
            )
            return
        if confirm(
            self,
            "发布到远程仓库",
            f"仓库「{os.path.basename(target)}」已初始化完成。\n\n"
            "是否立即在 GitHub 或 Gitee 上创建远程仓库，并把本地内容推送上去？",
        ):
            self.detail.publish_to_remote()

    def remove_repo(self) -> None:
        path = self.current_path()
        if not path:
            return
        if not confirm(
            self,
            "移出列表",
            f"确定把以下仓库从 GitDesk 的管理列表中移除吗？\n{path}\n\n"
            "（仅移除管理记录，不会删除磁盘上的任何文件）",
            danger=True,
        ):
            return
        self.store.remove(path)
        self.detail.set_repo("")
        self.reload()

    def add_cloned(self, path: str, extra: dict | None = None) -> None:
        if extra:
            self.store.add(path, **extra)
        self.reload()
        self.select_path(os.path.abspath(path))

    def reload_settings(self) -> None:
        if self.detail.path:
            self.detail.stop_watch()
            self.detail.start_watch()

    def shutdown(self) -> None:
        self.detail.stop_watch()


def _full_name_from_url(url: str) -> str:
    text = (url or "").strip()
    for prefix in ("https://", "http://", "git@"):
        if text.startswith(prefix):
            text = text[len(prefix) :]
    if text.startswith("github.com") or text.startswith("gitee.com"):
        text = text.split("/", 1)[1] if "/" in text else ""
    elif ":" in text and "git@" in url:
        text = text.split(":", 1)[1]
    if text.endswith(".git"):
        text = text[:-4]
    return text.strip("/")
