"""账号设置页：分别配置并校验 GitHub / Gitee 授权信息。"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ...core import platapi
from ...core.logger import get_logger
from ..widgets import Badge, Card, StatusDot, confirm, open_url, show_error

log = get_logger("ui.accounts")


class AccountCard(Card):
    """单个平台的授权配置卡片。"""

    changed = Signal()

    def __init__(self, platform: str, cfg, tasks, parent: QWidget | None = None) -> None:
        meta = platapi.PLATFORMS[platform]
        super().__init__(parent=parent)
        self.platform = platform
        self.meta = meta
        self.cfg = cfg
        self.tasks = tasks

        self.setMinimumWidth(430)

        self.status_dot = StatusDot()
        self.status_badge = Badge("未配置", "#8b949e")
        header_box = QWidget()
        hb = QHBoxLayout(header_box)
        hb.setContentsMargins(0, 0, 0, 0)
        hb.setSpacing(6)
        hb.addWidget(self.status_dot)
        hb.addWidget(self.status_badge)
        self.add_header_widget(header_box)

        platform_label = QLabel(meta.label)
        platform_label.setObjectName("H2")
        platform_label.setStyleSheet(f"color: {meta.accent};")
        self.add(platform_label)

        hint = QLabel(meta.token_hint)
        hint.setObjectName("Faint")
        hint.setWordWrap(True)
        self.add(hint)

        token_label = QLabel(f"{meta.token_label}")
        token_label.setObjectName("Muted")
        self.add(token_label)

        token_row = QHBoxLayout()
        token_row.setSpacing(6)
        self.token_edit = QLineEdit()
        self.token_edit.setEchoMode(QLineEdit.Password)
        self.token_edit.setPlaceholderText("粘贴访问令牌")
        self.toggle_btn = QPushButton("显示")
        self.toggle_btn.setProperty("variant", "ghost")
        self.toggle_btn.setCheckable(True)
        self.toggle_btn.setFixedWidth(56)
        self.toggle_btn.toggled.connect(self._toggle_token)
        token_row.addWidget(self.token_edit, 1)
        token_row.addWidget(self.toggle_btn)
        self.add_layout(token_row)

        user_label = QLabel("账号名（可选，用于克隆认证提示）")
        user_label.setObjectName("Muted")
        self.add(user_label)
        self.user_edit = QLineEdit()
        self.user_edit.setPlaceholderText("默认自动识别，可留空")
        self.add(self.user_edit)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.test_btn = QPushButton("保存并测试连接")
        self.test_btn.setProperty("variant", "primary")
        self.test_btn.clicked.connect(self.save_and_test)
        self.get_token_btn = QPushButton("获取令牌")
        self.get_token_btn.setProperty("variant", "ghost")
        self.get_token_btn.clicked.connect(lambda: open_url(self.meta.token_url))
        self.clear_btn = QPushButton("清除")
        self.clear_btn.setProperty("variant", "danger")
        self.clear_btn.clicked.connect(self.clear_account)
        actions.addWidget(self.test_btn)
        actions.addWidget(self.get_token_btn)
        actions.addStretch(1)
        actions.addWidget(self.clear_btn)
        self.add_layout(actions)

        self.detail = QLabel("尚未验证，请填写令牌后点击「保存并测试连接」。")
        self.detail.setObjectName("Faint")
        self.detail.setWordWrap(True)
        self.detail.setMinimumHeight(38)
        self.detail.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.add(self.detail)

        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        self.reload()

    # ---------------- 数据同步 ---------------- #

    def reload(self) -> None:
        acc = self.cfg.account(self.platform)
        self.token_edit.setText(self.cfg.token(self.platform))
        self.user_edit.setText(acc.get("username", ""))
        if self.cfg.is_connected(self.platform):
            display = acc.get("display") or acc.get("username") or "已连接"
            self.status_dot.set_color("#1a7f37")
            self.status_badge.set_state("已连接", "#1a7f37")
            self.detail.setText(f"当前身份：{display}")
        else:
            self.status_dot.set_color("#8b949e")
            self.status_badge.set_state("未配置", "#8b949e")

    def _toggle_token(self, checked: bool) -> None:
        self.token_edit.setEchoMode(QLineEdit.Normal if checked else QLineEdit.Password)
        self.toggle_btn.setText("隐藏" if checked else "显示")

    # ---------------- 操作 ---------------- #

    def save_and_test(self) -> None:
        token = self.token_edit.text().strip()
        username = self.user_edit.text().strip()
        if not token:
            self.status_badge.set_state("未配置", "#8b949e")
            self.status_dot.set_color("#8b949e")
            self.detail.setText("请先填写访问令牌。")
            return

        self.test_btn.setEnabled(False)
        self.test_btn.setText("连接中…")
        self.status_badge.set_state("连接中", "#9a6700")
        self.status_dot.set_color("#9a6700")
        self.detail.setText(f"正在向 {self.meta.label} 校验令牌…")

        client = platapi.RemoteClient(
            self.platform,
            token,
            username=username,
            proxy=self.cfg.get_setting("proxy", "") or "",
        )
        self.tasks.run(
            client.verify,
            on_success=self._on_verified,
            on_error=self._on_verify_failed,
            label=f"校验{self.meta.label}令牌",
        )

    def _on_verified(self, info: dict) -> None:
        self.test_btn.setEnabled(True)
        self.test_btn.setText("保存并测试连接")
        self.cfg.save_account(
            self.platform,
            token=self.token_edit.text().strip(),
            username=self.user_edit.text().strip() or info.get("login", ""),
            verified=True,
            display=info.get("name") or info.get("login") or "",
        )
        self.status_dot.set_color("#1a7f37")
        self.status_badge.set_state("已连接", "#1a7f37")
        self.detail.setText(
            f"连接成功 · 账号 {info.get('login')}（{info.get('name')}）"
            f" · 公开仓库 {info.get('repos')} 个"
        )
        log.info("%s 账号校验成功：%s", self.meta.label, info.get("login"))
        self.changed.emit()

    def _on_verify_failed(self, message: str, detail: str) -> None:
        self.test_btn.setEnabled(True)
        self.test_btn.setText("保存并测试连接")
        self.status_dot.set_color("#b42318")
        self.status_badge.set_state("验证失败", "#b42318")
        self.detail.setText(message)
        self.cfg.save_account(
            self.platform,
            token=self.token_edit.text().strip(),
            username=self.user_edit.text().strip(),
            verified=False,
        )
        self.changed.emit()
        show_error(self, f"{self.meta.label} 连接失败", message, detail)

    def clear_account(self) -> None:
        if not confirm(self, "清除配置", f"确定要清除 {self.meta.label} 的令牌配置吗？"):
            return
        self.cfg.clear_account(self.platform)
        self.token_edit.clear()
        self.user_edit.clear()
        self.status_dot.set_color("#8b949e")
        self.status_badge.set_state("未配置", "#8b949e")
        self.detail.setText("配置已清除。")
        log.info("已清除 %s 账号配置", self.meta.label)
        self.changed.emit()


class AccountsPage(QWidget):
    """账号设置页面。"""

    accounts_changed = Signal()

    def __init__(self, cfg, tasks, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.cfg = cfg
        self.tasks = tasks

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 20)
        layout.setSpacing(14)

        title = QLabel("账号设置")
        title.setObjectName("H1")
        layout.addWidget(title)

        desc = QLabel(
            "为 GitHub 与 Gitee 分别配置访问令牌。令牌将使用 Windows 数据保护接口（DPAPI）"
            "加密后保存在本机，不会上传到任何第三方服务。"
        )
        desc.setObjectName("Muted")
        desc.setWordWrap(True)
        layout.addWidget(desc)

        grid = QGridLayout()
        grid.setSpacing(16)
        grid.setContentsMargins(0, 4, 0, 0)

        self.github_card = AccountCard("github", cfg, tasks)
        self.gitee_card = AccountCard("gitee", cfg, tasks)
        for card in (self.github_card, self.gitee_card):
            card.changed.connect(self.accounts_changed)
            card.changed.connect(self._refresh_hint)

        grid.addWidget(self.github_card, 0, 0)
        grid.addWidget(self.gitee_card, 0, 1)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        layout.addLayout(grid)

        self.summary = QLabel()
        self.summary.setObjectName("Faint")
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        layout.addStretch(1)

        self._refresh_hint()

    def _refresh_hint(self) -> None:
        ready = [
            platapi.PLATFORMS[p].label
            for p in ("github", "gitee")
            if self.cfg.is_connected(p)
        ]
        if ready:
            self.summary.setText(
                "已完成授权：" + "、".join(ready) + "。可前往「远程仓库」浏览与克隆项目。"
            )
        else:
            self.summary.setText("提示：至少完成一个平台的授权后，即可浏览远程仓库并克隆到本地。")

    def reload(self) -> None:
        self.github_card.reload()
        self.gitee_card.reload()
        self._refresh_hint()
