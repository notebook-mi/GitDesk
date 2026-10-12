"""「发布到远程仓库」对话框：选择平台、创建远程仓库并推送本地内容。"""

from __future__ import annotations

import os
import re

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..core import platapi
from ..core.logger import get_logger
from ..core.publish import repo_state
from .widgets import show_error

log = get_logger("ui.publish")

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")


class PublishDialog(QDialog):
    """收集发布参数；实际执行由调用方放进后台任务。"""

    def __init__(self, cfg, repo_path: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.cfg = cfg
        self.repo_path = os.path.abspath(repo_path)
        self.want_accounts = False
        self.result_payload: dict = {}

        self.setWindowTitle("发布到远程仓库")
        self.setMinimumWidth(560)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(12)

        title = QLabel("创建远程仓库并推送")
        title.setObjectName("H2")
        layout.addWidget(title)

        desc = QLabel(
            "GitDesk 将在所选平台上新建一个仓库（不生成初始提交，避免历史冲突），"
            "把本地目录设为其 origin，然后推送当前分支上已提交的内容。"
        )
        desc.setObjectName("Muted")
        desc.setWordWrap(True)
        layout.addWidget(desc)

        path_label = QLabel(f"本地目录：{self.repo_path}")
        path_label.setObjectName("Faint")
        path_label.setWordWrap(True)
        layout.addWidget(path_label)

        # ---- 平台 ----
        self.ready_platforms = [
            key for key in ("github", "gitee") if cfg.is_connected(key)
        ]
        self._no_commit, self._empty = self._detect(repo_state)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(10)

        self.platform_combo = QComboBox()
        for key in ("github", "gitee"):
            meta = platapi.PLATFORMS[key]
            if cfg.is_connected(key):
                owner = cfg.username(key) or cfg.account(key).get("display") or ""
                suffix = f"（{owner}）" if owner else ""
                self.platform_combo.addItem(f"{meta.label}{suffix}", key)
            else:
                self.platform_combo.addItem(f"{meta.label} · 未配置令牌", key)
        self.platform_combo.currentIndexChanged.connect(self._on_platform_changed)
        form.addRow("提交平台", self.platform_combo)

        self.name_edit = QLineEdit(os.path.basename(os.path.normpath(self.repo_path)))
        self.name_edit.setPlaceholderText("远程仓库名称")
        self.name_edit.textChanged.connect(self._update_preview)
        form.addRow("仓库名称", self.name_edit)

        self.desc_edit = QLineEdit()
        self.desc_edit.setPlaceholderText("仓库简介（可选）")
        form.addRow("仓库描述", self.desc_edit)

        self.visibility_combo = QComboBox()
        self.visibility_combo.addItem("公开（任何人可见）", False)
        self.visibility_combo.addItem("私有（仅自己可见）", True)
        form.addRow("可见性", self.visibility_combo)

        layout.addLayout(form)

        self.preview = QLabel("")
        self.preview.setObjectName("Faint")
        self.preview.setWordWrap(True)
        self.preview.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.preview)

        # ---- 空仓库相关选项 ----
        self.empty_box = QWidget()
        empty_layout = QVBoxLayout(self.empty_box)
        empty_layout.setContentsMargins(0, 0, 0, 0)
        empty_layout.setSpacing(8)

        empty_hint = QLabel(
            "当前仓库尚无任何提交，GitDesk 需要至少一个提交才能推送："
            "现有文件会被整理为首个提交，若目录为空则可自动生成 README.md。"
        )
        empty_hint.setObjectName("Faint")
        empty_hint.setWordWrap(True)
        empty_layout.addWidget(empty_hint)

        self.readme_check = QCheckBox("自动创建 README.md 作为首个提交（目录为空时可用）")
        self.readme_check.setChecked(self._empty)
        self.readme_check.setEnabled(self._empty)
        empty_layout.addWidget(self.readme_check)

        msg_row = QHBoxLayout()
        msg_row.setSpacing(8)
        msg_row.addWidget(QLabel("首次提交说明"))
        self.message_edit = QLineEdit("Initial commit")
        msg_row.addWidget(self.message_edit, 1)
        empty_layout.addLayout(msg_row)

        layout.addWidget(self.empty_box)
        self.empty_box.setVisible(self._no_commit)

        self.hint = QLabel("")
        self.hint.setWordWrap(True)
        layout.addWidget(self.hint)

        # ---- 按钮 ----
        buttons = QDialogButtonBox()
        self.go_accounts_btn = QPushButton("前往账号设置")
        self.go_accounts_btn.setProperty("variant", "ghost")
        self.go_accounts_btn.clicked.connect(self._go_accounts)
        self.create_btn = QPushButton("创建并推送")
        self.create_btn.setProperty("variant", "primary")
        self.create_btn.setDefault(True)
        self.cancel_btn = QPushButton("取消")
        buttons.addButton(self.go_accounts_btn, QDialogButtonBox.ActionRole)
        buttons.addButton(self.cancel_btn, QDialogButtonBox.RejectRole)
        buttons.addButton(self.create_btn, QDialogButtonBox.AcceptRole)
        self.cancel_btn.clicked.connect(self.reject)
        self.create_btn.clicked.connect(self._confirm)
        layout.addWidget(buttons)

        self._on_platform_changed()

    # ------------------------------------------------------------------ #

    def _detect(self, state_fn) -> tuple[bool, bool]:
        """返回 (是否无提交, 是否仓库完全为空)，读取失败时按非空处理。"""
        try:
            no_commit, empty = state_fn(self.repo_path)
            return bool(no_commit), bool(empty)
        except Exception:  # noqa: BLE001 - 交由推送阶段给出准确错误
            log.debug("检测仓库状态失败", exc_info=True)
            return False, False

    def current_platform(self) -> str:
        return self.platform_combo.currentData() or "github"

    def _platform_ready(self) -> bool:
        return self.current_platform() in self.ready_platforms

    def _on_platform_changed(self) -> None:
        ready = self._platform_ready()
        self.go_accounts_btn.setVisible(not ready)
        self.create_btn.setEnabled(ready)
        if ready:
            key = self.current_platform()
            self.hint.setText(
                "推送时会自动使用已保存的访问令牌；请确认令牌具备创建仓库权限"
                "（GitHub 需 repo，Gitee 需 projects）。"
            )
            self.hint.setStyleSheet("color:#8b949e;")
        else:
            meta = platapi.PLATFORMS[self.current_platform()]
            self.hint.setText(
                f"尚未配置 {meta.label} 的访问令牌，无法创建远程仓库。请先到「账号设置」完成授权。"
            )
            self.hint.setStyleSheet("color:#9a6700; font-weight:600;")
        self._update_preview()

    def _go_accounts(self) -> None:
        self.want_accounts = True
        self.reject()

    def _update_preview(self) -> None:
        key = self.current_platform()
        name = self.name_edit.text().strip()
        owner = self.cfg.username(key) or self.cfg.account(key).get("display") or "<账号>"
        host = platapi.PLATFORMS[key].web_host
        self.preview.setText(f"将创建的远程地址：{host}/{owner}/{name or '<仓库名>'}.git")

    def _confirm(self) -> None:
        name = self.name_edit.text().strip()
        if not NAME_RE.match(name):
            show_error(
                self,
                "仓库名称不合法",
                "仓库名称需以字母或数字开头，只能包含字母、数字、点、下划线或连字符，长度不超过 100。",
            )
            return
        if self._no_commit and self._empty and self.readme_check.isChecked() \
                and not self.message_edit.text().strip():
            show_error(self, "缺少提交说明", "请填写首次提交说明，或取消自动创建 README.md。")
            return
        self.result_payload = {
            "platform": self.current_platform(),
            "name": name,
            "description": self.desc_edit.text().strip(),
            "private": bool(self.visibility_combo.currentData()),
            "create_readme": bool(self._empty and self.readme_check.isChecked()),
            "commit_message": self.message_edit.text().strip() or "Initial commit",
        }
        self.accept()
