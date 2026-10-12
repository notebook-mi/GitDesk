"""操作日志页：实时展示运行日志，便于排查同步异常。"""

from __future__ import annotations

import os
import time

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QFont, QTextCursor
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...core.logger import LEVEL_COLORS, bus_handler, current_log_file, get_logger
from ...core.paths import log_dir
from ..widgets import confirm, open_in_explorer

log = get_logger("ui.logs")

LEVEL_ORDER = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40, "CRITICAL": 50}
MAX_LINES = 3000


class LogBridge(QObject):
    """把日志线程的回调转换为 Qt 信号，保证跨线程安全。"""

    message = Signal(int, str, str)


class LogsPage(QWidget):
    """日志查看器。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._bridge = LogBridge()
        self._bridge.message.connect(self._append)
        self._min_level = 10
        self._keyword = ""
        self._line_count = 0

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 20)
        layout.setSpacing(12)

        title = QLabel("操作日志")
        title.setObjectName("H1")
        layout.addWidget(title)

        info_label = QLabel(
            "所有同步、克隆、认证等操作都会记录在此，并按天写入本地日志文件，便于排查异常。"
        )
        info_label.setObjectName("Muted")
        info_label.setWordWrap(True)
        layout.addWidget(info_label)

        bar = QHBoxLayout()
        bar.setSpacing(8)
        bar.addWidget(QLabel("级别"))
        self.level_combo = QComboBox()
        for text, value in (
            ("全部", 10),
            ("INFO 及以上", 20),
            ("WARNING 及以上", 30),
            ("ERROR 及以上", 40),
        ):
            self.level_combo.addItem(text, value)
        self.level_combo.setCurrentIndex(1)
        self.level_combo.setFixedWidth(140)
        self.level_combo.currentIndexChanged.connect(self._on_level_changed)
        bar.addWidget(self.level_combo)

        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("按关键字过滤日志…")
        self.filter_edit.textChanged.connect(self._on_filter_changed)
        bar.addWidget(self.filter_edit, 1)

        self.autoscroll = QCheckBox("自动滚动")
        self.autoscroll.setChecked(True)
        bar.addWidget(self.autoscroll)
        layout.addLayout(bar)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.clear_btn = QPushButton("清空显示")
        self.clear_btn.setProperty("variant", "ghost")
        self.clear_btn.clicked.connect(self.clear_view)
        self.open_btn = QPushButton("打开日志目录")
        self.open_btn.clicked.connect(lambda: open_in_explorer(str(log_dir())))
        self.view_file_btn = QPushButton("查看当日日志文件")
        self.view_file_btn.setProperty("variant", "ghost")
        self.view_file_btn.clicked.connect(lambda: open_in_explorer(current_log_file()))
        actions.addWidget(self.clear_btn)
        actions.addWidget(self.open_btn)
        actions.addWidget(self.view_file_btn)
        actions.addStretch(1)
        self.path_label = QLabel(f"当前日志：{current_log_file()}")
        self.path_label.setObjectName("Faint")
        actions.addWidget(self.path_label)
        layout.addLayout(actions)

        self.view = QPlainTextEdit()
        self.view.setReadOnly(True)
        self.view.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.view.setMaximumBlockCount(MAX_LINES)
        font = QFont("Cascadia Mono", 9)
        font.setStyleHint(QFont.Monospace)
        self.view.setFont(font)
        self.view.setPlaceholderText("暂无日志。")
        layout.addWidget(self.view, 1)

        bus_handler.add_listener(self._on_log_record)
        self._append(20, "INFO", f"{_ts()}  INFO     日志视图已就绪，开始记录。")

    # ---------------- 日志接入 ---------------- #

    def _on_log_record(self, levelno: int, levelname: str, message: str) -> None:
        try:
            self._bridge.message.emit(levelno, levelname, message)
        except RuntimeError:
            pass

    def _append(self, levelno: int, levelname: str, message: str) -> None:
        if levelno < self._min_level:
            return
        if self._keyword and self._keyword not in message.lower():
            return
        color = LEVEL_COLORS.get(levelname, "#1f2328")
        escaped = (
            message.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        )
        cursor = self.view.textCursor()
        cursor.movePosition(QTextCursor.End)
        fmt = cursor.charFormat()
        from PySide6.QtGui import QTextCharFormat, QColor

        char_fmt = QTextCharFormat()
        char_fmt.setForeground(QColor(color))
        cursor.insertText(escaped + "\n", char_fmt)
        if self.autoscroll.isChecked():
            self.view.moveCursor(QTextCursor.End)
            self.view.ensureCursorVisible()

    # ---------------- 过滤 ---------------- #

    def _on_level_changed(self) -> None:
        self._min_level = int(self.level_combo.currentData() or 10)
        self._reload_hint()

    def _on_filter_changed(self, text: str) -> None:
        self._keyword = text.strip().lower()
        self._reload_hint()

    def _reload_hint(self) -> None:
        self.view.setPlaceholderText(
            "已调整过滤条件，仅显示此后新产生的日志。点击「清空显示」可重新开始。"
        )

    def clear_view(self) -> None:
        if not confirm(self, "清空显示", "仅清空界面上的日志，不会删除本地日志文件。确定继续？"):
            return
        self.view.clear()

    def shutdown(self) -> None:
        bus_handler.remove_listener(self._on_log_record)


def _ts() -> str:
    return time.strftime("%H:%M:%S")
