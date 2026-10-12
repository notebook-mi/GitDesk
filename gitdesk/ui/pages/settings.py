"""设置页：克隆目录、变更监测、网络代理与提交身份。"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ...core.logger import get_logger
from ...core.paths import config_file, data_dir
from ..widgets import Card, PathPicker, confirm, open_in_explorer

log = get_logger("ui.settings")


class SettingsPage(QWidget):
    saved = Signal()

    def __init__(self, cfg, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.cfg = cfg

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 20)
        layout.setSpacing(14)

        title = QLabel("设置")
        title.setObjectName("H1")
        layout.addWidget(title)

        desc = QLabel("调整克隆目录、变更检测频率与网络参数。所有配置保存在本机。")
        desc.setObjectName("Muted")
        desc.setWordWrap(True)
        layout.addWidget(desc)

        # ---- 通用 ----
        general = Card("通用")
        general.add(_field_label("默认克隆目录"))
        self.clone_picker = PathPicker("克隆的仓库将保存在此目录下的同名子目录中")
        general.add(self.clone_picker)

        row = QHBoxLayout()
        row.setSpacing(12)
        self.watch_check = QCheckBox("启用本地仓库变更实时检测")
        self.watch_check.setToolTip(
            "开启后会定期扫描当前仓库的工作区，发现未提交改动时给出提示，避免同步冲突。"
        )
        row.addWidget(self.watch_check)
        row.addWidget(QLabel("检测间隔"))
        self.interval_spin = QSpinBox()
        self.interval_spin.setRange(2, 120)
        self.interval_spin.setSuffix(" 秒")
        self.interval_spin.setFixedWidth(110)
        row.addWidget(self.interval_spin)
        row.addStretch(1)
        general.add_layout(row)
        layout.addWidget(general)

        # ---- 网络 ----
        network = Card("网络")
        network.add(_field_label("HTTP / HTTPS 代理（留空表示直连）"))
        self.proxy_edit = QLineEdit()
        self.proxy_edit.setPlaceholderText("例如 http://127.0.0.1:7890")
        network.add(self.proxy_edit)
        layout.addWidget(network)

        # ---- 提交身份 ----
        identity = Card("Git 提交身份")
        ident_grid = QGridLayout()
        ident_grid.setHorizontalSpacing(14)
        ident_grid.setVerticalSpacing(6)
        ident_grid.addWidget(_field_label("用户名"), 0, 0)
        ident_grid.addWidget(_field_label("邮箱"), 0, 1)
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("例如 zhangsan")
        self.email_edit = QLineEdit()
        self.email_edit.setPlaceholderText("例如 zhangsan@example.com")
        ident_grid.addWidget(self.name_edit, 1, 0)
        ident_grid.addWidget(self.email_edit, 1, 1)
        identity.add_layout(ident_grid)
        layout.addWidget(identity)

        # ---- 数据 ----
        storage = Card("数据存储")
        self.data_label = QLabel()
        self.data_label.setObjectName("Faint")
        self.data_label.setWordWrap(True)
        self.data_label.setTextInteractionFlags(self.data_label.textInteractionFlags())
        storage.add(self.data_label)
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        open_data = QPushButton("打开数据目录")
        open_data.clicked.connect(lambda: open_in_explorer(str(data_dir())))
        config_btn = QPushButton("查看配置文件")
        config_btn.setProperty("variant", "ghost")
        config_btn.clicked.connect(lambda: open_in_explorer(str(config_file())))
        btn_row.addWidget(open_data)
        btn_row.addWidget(config_btn)
        btn_row.addStretch(1)
        storage.add_layout(btn_row)
        layout.addWidget(storage)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.save_btn = QPushButton("保存设置")
        self.save_btn.setProperty("variant", "primary")
        self.save_btn.clicked.connect(self.save)
        self.reset_btn = QPushButton("恢复默认")
        self.reset_btn.setProperty("variant", "danger")
        self.reset_btn.clicked.connect(self.reset)
        actions.addStretch(1)
        actions.addWidget(self.reset_btn)
        actions.addWidget(self.save_btn)
        layout.addLayout(actions)
        layout.addStretch(1)

        self.load_values()

    # ---------------- 读写 ---------------- #

    def load_values(self) -> None:
        self.clone_picker.set_path(str(self.cfg.get_setting("clone_dir", "")))
        self.watch_check.setChecked(bool(self.cfg.get_setting("watch_enabled", True)))
        self.interval_spin.setValue(int(int(self.cfg.get_setting("watch_interval_ms", 5000)) / 1000))
        self.proxy_edit.setText(str(self.cfg.get_setting("proxy", "")))
        self.name_edit.setText(str(self.cfg.get_setting("git_username", "GitDesk")))
        self.email_edit.setText(str(self.cfg.get_setting("git_email", "gitdesk@localhost")))
        self.data_label.setText(
            f"数据目录：{data_dir()}\n配置文件：{config_file()}\n"
            "日志按天写入数据目录下的 logs 文件夹，保留最近若干份。"
        )

    def save(self) -> None:
        values = {
            "clone_dir": self.clone_picker.path(),
            "watch_enabled": self.watch_check.isChecked(),
            "watch_interval_ms": max(2, self.interval_spin.value()) * 1000,
            "proxy": self.proxy_edit.text().strip(),
            "git_username": self.name_edit.text().strip() or "GitDesk",
            "git_email": self.email_edit.text().strip() or "gitdesk@localhost",
        }
        if not values["clone_dir"]:
            values["clone_dir"] = str(data_dir() / "repos")
        self.cfg.update_settings(values)
        log.info("设置已保存")
        self.saved.emit()

    def reset(self) -> None:
        if not confirm(self, "恢复默认", "确定恢复为默认设置吗？克隆目录与代理等将重置。", danger=True):
            return
        self.cfg.update_settings(
            {
                "watch_enabled": True,
                "watch_interval_ms": 5000,
                "proxy": "",
                "git_username": "GitDesk",
                "git_email": "gitdesk@localhost",
            }
        )
        self.load_values()
        self.saved.emit()


def _field_label(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("Muted")
    return label
