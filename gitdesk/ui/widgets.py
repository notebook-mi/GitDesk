"""通用界面组件与辅助函数。"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from typing import Iterable

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QDesktopServices, QFont, QTextCharFormat, QTextCursor
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .style import DIFF_COLORS, PALETTE


# --------------------------------------------------------------------------- #
# 基础组件
# --------------------------------------------------------------------------- #


class Card(QFrame):
    """带标题的白色卡片容器。"""

    def __init__(self, title: str = "", subtitle: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        self._outer = QVBoxLayout(self)
        self._outer.setContentsMargins(16, 14, 16, 16)
        self._outer.setSpacing(10)

        self.header = QHBoxLayout()
        self.header.setSpacing(8)
        self.title_label = QLabel(title)
        self.title_label.setObjectName("CardTitle")
        self.header.addWidget(self.title_label)
        self.header.addStretch(1)
        self._outer.addLayout(self.header)

        if subtitle:
            sub = QLabel(subtitle)
            sub.setObjectName("Faint")
            sub.setWordWrap(True)
            self._outer.addWidget(sub)

        self.body = QVBoxLayout()
        self.body.setSpacing(9)
        self._outer.addLayout(self.body)

    def add(self, widget: QWidget) -> QWidget:
        self.body.addWidget(widget)
        return widget

    def add_layout(self, layout) -> None:
        self.body.addLayout(layout)

    def add_header_widget(self, widget: QWidget) -> QWidget:
        self.header.addWidget(widget)
        return widget

    def set_title(self, text: str) -> None:
        self.title_label.setText(text)


class Badge(QLabel):
    """圆角状态标签。"""

    def __init__(self, text: str = "", color: str = PALETTE["muted"], bg: str | None = None) -> None:
        super().__init__(text)
        self.setObjectName("Badge")
        self.set_color(color, bg)
        self.setAlignment(Qt.AlignCenter)
        self.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)

    def set_color(self, color: str, bg: str | None = None) -> None:
        background = bg or _soft(color)
        self.setStyleSheet(
            f"QLabel#Badge {{ color: {color}; background: {background}; }}"
        )

    def set_state(self, text: str, color: str) -> None:
        self.setText(text)
        self.set_color(color)


def _soft(hex_color: str) -> str:
    """生成同一色相的浅色背景。"""
    hex_color = hex_color.lstrip("#")
    if len(hex_color) != 6:
        return "#eef1f5"
    r, g, b = (int(hex_color[i : i + 2], 16) for i in (0, 2, 4))
    mix = lambda c: int(c + (255 - c) * 0.88)  # noqa: E731
    return "#%02x%02x%02x" % (mix(r), mix(g), mix(b))


class StatusDot(QLabel):
    def __init__(self, color: str = PALETTE["faint"], size: int = 9) -> None:
        super().__init__()
        self._size = size
        self.setFixedSize(size, size)
        self.set_color(color)

    def set_color(self, color: str) -> None:
        self.setStyleSheet(
            f"background: {color}; border-radius: {self._size // 2}px;"
        )


class LabeledField(QWidget):
    """带标签的输入行。"""

    def __init__(
        self,
        label: str,
        placeholder: str = "",
        password: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        name = QLabel(label)
        name.setObjectName("Muted")
        layout.addWidget(name)

        row = QHBoxLayout()
        row.setSpacing(6)
        self.edit = QLineEdit()
        self.edit.setPlaceholderText(placeholder)
        if password:
            self.edit.setEchoMode(QLineEdit.Password)
        row.addWidget(self.edit, 1)
        layout.addLayout(row)

    def text(self) -> str:
        return self.edit.text().strip()

    def set_text(self, value: str) -> None:
        self.edit.setText(value or "")


class PathPicker(QWidget):
    """目录选择输入框。"""

    changed = Signal(str)

    def __init__(self, placeholder: str = "选择目录…", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self.edit = QLineEdit()
        self.edit.setPlaceholderText(placeholder)
        self.edit.textChanged.connect(self.changed)
        self.button = QPushButton("浏览…")
        self.button.clicked.connect(self._browse)
        layout.addWidget(self.edit, 1)
        layout.addWidget(self.button)

    def _browse(self) -> None:
        start = self.edit.text().strip() or os.path.expanduser("~")
        chosen = QFileDialog.getExistingDirectory(self, "选择目录", start)
        if chosen:
            self.edit.setText(chosen)

    def path(self) -> str:
        return self.edit.text().strip()

    def set_path(self, value: str) -> None:
        self.edit.setText(value or "")


class DiffView(QPlainTextEdit):
    """带语法着色的统一 diff 展示控件。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setReadOnly(True)
        self.setLineWrapMode(QPlainTextEdit.NoWrap)
        font = QFont("Cascadia Mono", 9)
        font.setStyleHint(QFont.Monospace)
        self.setFont(font)
        self.setPlaceholderText("选择文件后在此查看差异内容")

    def set_diff(self, text: str) -> None:
        self.clear()
        if not text.strip():
            return
        cursor = self.textCursor()
        for line in text.splitlines():
            stripped = line.rstrip("\n")
            fmt = QTextCharFormat()
            fmt.setFontFamilies(["Cascadia Mono", "Consolas", "monospace"])
            low = stripped.lower()
            if low.startswith(("diff --git", "index ", "--- ", "+++ ", "new file", "deleted file", "similarity", "rename ")):
                fmt.setForeground(QColor("#6b7280"))
                fmt.setBackground(QColor(DIFF_COLORS["meta"]))
            elif stripped.startswith("@@"):
                fmt.setForeground(QColor("#1f56b8"))
                fmt.setBackground(QColor(DIFF_COLORS["hunk"]))
            elif stripped.startswith("+"):
                fmt.setForeground(QColor("#116329"))
                fmt.setBackground(QColor(DIFF_COLORS["add"]))
            elif stripped.startswith("-"):
                fmt.setForeground(QColor("#a40e26"))
                fmt.setBackground(QColor(DIFF_COLORS["del"]))
            else:
                fmt.setForeground(QColor(PALETTE["text"]))
            cursor.insertText(stripped + "\n", fmt)
        self.moveCursor(QTextCursor.Start)


class Toast(QLabel):
    """浮层提示条。"""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("Toast")
        self.setVisible(False)
        self.setWordWrap(True)
        self.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)
        self.setContentsMargins(14, 10, 14, 10)

    def show_message(self, text: str, level: str = "info", timeout_ms: int = 4200) -> None:
        colors = {
            "info": (PALETTE["primary"], PALETTE["primary_soft"]),
            "success": (PALETTE["success"], PALETTE["success_soft"]),
            "warning": (PALETTE["warning"], PALETTE["warning_soft"]),
            "error": (PALETTE["danger"], PALETTE["danger_soft"]),
        }
        fg, bg = colors.get(level, colors["info"])
        self.setStyleSheet(
            f"QLabel#Toast {{ background: {bg}; color: {fg}; border: 1px solid {fg}33;"
            f" border-radius: 8px; font-weight: 600; }}"
        )
        self.setText(text)
        self.adjustSize()
        self._reposition()
        self.setVisible(True)
        self.raise_()
        from PySide6.QtCore import QTimer

        QTimer.singleShot(timeout_ms, self.hide)

    def _reposition(self) -> None:
        parent = self.parentWidget()
        if not parent:
            return
        width = min(max(self.sizeHint().width() + 20, 260), max(320, parent.width() - 80))
        self.setFixedWidth(width)
        self.adjustSize()
        x = (parent.width() - width) // 2
        self.move(x, 18)
        self.setFixedWidth(width)


class TransferPanel(QFrame):
    """「进度条 + 详情」传输面板。

    下载（克隆 / 获取 / 拉取）与上传（推送 / 发布）共用：
    顶部显示当前阶段与耗时，中间是进度条，下方一行摘要，
    展开后给出字节数、对象数、平均速度、重试次数与最近事件日志。

    可取消的操作（上传）会多出一个「停止」按钮：任务运行期间（含自动重试的
    等待期）点击即请求中止；任务结束后按钮保留为「关闭」，因此**失败之后
    依然可以点它清掉面板**，不会留下一个卡住的失败态。
    """

    MAX_LOG_LINES = 200

    #: 用户点击「停止」（任务仍在运行时）或「关闭」（任务已结束时）
    stop_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("TransferPanel")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setVisible(False)
        self._started_at = 0.0
        self._active = False
        self._cancellable = False
        self._last_payload: dict = {}

        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 10, 12, 10)
        outer.setSpacing(7)

        head = QHBoxLayout()
        head.setSpacing(8)
        self.kind_label = QLabel("传输")
        self.kind_label.setObjectName("TransferKind")
        self.phase_label = QLabel("准备中")
        self.phase_label.setObjectName("TransferPhase")
        self.phase_label.setWordWrap(True)
        head.addWidget(self.kind_label)
        head.addWidget(self.phase_label, 1)
        self.speed_label = QLabel("")
        self.speed_label.setObjectName("Faint")
        self.elapsed_label = QLabel("用时 0.0 秒")
        self.elapsed_label.setObjectName("Faint")
        head.addWidget(self.speed_label)
        head.addWidget(self.elapsed_label)
        outer.addLayout(head)

        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        self.bar.setRange(0, 0)
        outer.addWidget(self.bar)

        foot = QHBoxLayout()
        foot.setSpacing(8)
        self.summary_label = QLabel("等待开始…")
        self.summary_label.setObjectName("TransferSummary")
        self.summary_label.setWordWrap(True)
        foot.addWidget(self.summary_label, 1)
        self.stop_btn = QPushButton("停止")
        self.stop_btn.setObjectName("MiniButton")
        self.stop_btn.setProperty("variant", "danger")
        self.stop_btn.setCursor(Qt.PointingHandCursor)
        self.stop_btn.setToolTip("中止当前上传（自动重试的等待期内也可以随时停止）")
        self.stop_btn.clicked.connect(self._on_stop_clicked)
        self.stop_btn.setVisible(False)
        foot.addWidget(self.stop_btn)
        self.toggle_btn = QPushButton("详情 ▾")
        self.toggle_btn.setObjectName("MiniButton")
        self.toggle_btn.setProperty("variant", "ghost")
        self.toggle_btn.setCheckable(True)
        self.toggle_btn.setCursor(Qt.PointingHandCursor)
        self.toggle_btn.toggled.connect(self._on_toggle)
        foot.addWidget(self.toggle_btn)
        outer.addLayout(foot)

        self.detail_box = QWidget()
        detail_layout = QVBoxLayout(self.detail_box)
        detail_layout.setContentsMargins(0, 2, 0, 0)
        detail_layout.setSpacing(6)
        self.fields: dict[str, QLabel] = {}
        grid = QGridLayout()
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(4)
        specs = [
            ("phase", "阶段"),
            ("objects", "对象"),
            ("bytes_in", "已接收"),
            ("bytes_out", "已发送"),
            ("speed_avg", "平均速度"),
            ("elapsed", "用时"),
            ("retries", "自动重试"),
            ("message", "最近事件"),
        ]
        for idx, (key, text) in enumerate(specs):
            row, col = divmod(idx, 2)
            name = QLabel(text)
            name.setObjectName("Muted")
            value = QLabel("—")
            value.setObjectName("TransferValue")
            value.setWordWrap(True)
            value.setTextInteractionFlags(Qt.TextSelectableByMouse)
            self.fields[key] = value
            grid.addWidget(name, row, col * 2)
            grid.addWidget(value, row, col * 2 + 1)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)
        detail_layout.addLayout(grid)

        self.log = QPlainTextEdit()
        self.log.setObjectName("TransferLog")
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(80)
        self.log.setMinimumHeight(54)
        self.log.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.log.setPlaceholderText("传输过程日志")
        detail_layout.addWidget(self.log)
        self.detail_box.setVisible(False)
        outer.addWidget(self.detail_box)

        self._clock = QTimer(self)
        self._clock.setInterval(500)
        self._clock.timeout.connect(self._tick)

    # ---------------- 生命周期 ----------------

    @property
    def active(self) -> bool:
        return self._active

    def begin(
        self,
        label: str = "",
        kind: str = "",
        indeterminate: bool = True,
        cancellable: bool = False,
    ) -> None:
        """开始一次传输（或一次需要展示进度的耗时操作）。

        cancellable=True 时显示「停止」按钮（上传类操作）。
        """
        self._active = True
        self._cancellable = bool(cancellable)
        self._started_at = time.time()
        self._last_payload = {}
        self.kind_label.setText(kind or "处理中")
        self.phase_label.setText(label or "准备中…")
        self.summary_label.setText("已开始，等待远程响应…")
        self.speed_label.setText("")
        self.elapsed_label.setText("用时 0.0 秒")
        self.log.clear()
        for value in self.fields.values():
            value.setText("—")
        self.fields["phase"].setText(label or "准备中")
        self._set_bar_state("")
        if indeterminate:
            self.bar.setRange(0, 0)
            self.bar.setValue(0)
        else:
            self.bar.setRange(0, 100)
            self.bar.setValue(0)
        if self._cancellable:
            self.enable_stop()
        else:
            self.stop_btn.setVisible(False)
        self.setVisible(True)
        self._clock.start()

    def update_detail(self, payload: dict) -> None:
        """接收引擎抛出的结构调整（阶段 / 字节 / 对象 / 速度 / 重试）。"""
        if not isinstance(payload, dict):
            return
        self._last_payload = dict(payload)
        kind = payload.get("label") or payload.get("kind") or ""
        if kind:
            self.kind_label.setText(str(kind))
        phase = str(payload.get("phase") or "")
        if phase:
            self.phase_label.setText(phase)
            self.fields["phase"].setText(phase)
        self.fields["objects"].setText(str(payload.get("objects_text") or "—"))
        self.fields["bytes_in"].setText(str(payload.get("bytes_in_text") or "—"))
        self.fields["bytes_out"].setText(str(payload.get("bytes_out_text") or "—"))
        self.fields["speed_avg"].setText(str(payload.get("speed_avg") or "—"))
        elapsed = payload.get("elapsed")
        if isinstance(elapsed, (int, float)):
            self.fields["elapsed"].setText(f"{float(elapsed):.1f} 秒")
            self.elapsed_label.setText(f"用时 {float(elapsed):.1f} 秒")
        retries = int(payload.get("retries") or 0)
        self.fields["retries"].setText(f"{retries} 次" if retries else "未发生")
        message = str(payload.get("message") or "")
        if message:
            self.fields["message"].setText(message)
        speed = str(payload.get("speed") or "")
        if speed:
            self.speed_label.setText(speed)
        summary = str(payload.get("summary") or "")
        if summary:
            self.summary_label.setText(summary)
        percent = payload.get("percent")
        if isinstance(percent, (int, float)) and 0 <= float(percent) <= 100:
            self.bar.setRange(0, 100)
            self.bar.setValue(int(float(percent)))

    def set_message(self, text: str, level: str = "info") -> None:
        """更新阶段提示，并把文本追加到详情日志。"""
        if text:
            self.phase_label.setText(text)
            self.fields["phase"].setText(text)
            self.fields["message"].setText(text)
        self.append_log(text)

    def append_log(self, text: str) -> None:
        if not text:
            return
        self.log.appendPlainText(str(text))
        document = self.log.document()
        if document is not None and document.blockCount() > self.MAX_LOG_LINES:
            cursor = self.log.textCursor()
            cursor.movePosition(QTextCursor.Start)
            cursor.select(QTextCursor.BlockUnderCursor)
            cursor.removeSelectedText()
            cursor.deleteChar()

    def finish(self, message: str = "", ok: bool = True, cancelled: bool = False) -> None:
        self._active = False
        self._clock.stop()
        elapsed = time.time() - self._started_at if self._started_at else 0.0
        self.elapsed_label.setText(f"用时 {elapsed:.1f} 秒")
        self.fields["elapsed"].setText(f"{elapsed:.1f} 秒")
        # 进度条在失败 / 取消时改用红 / 灰满格，避免「明明失败却显示满格蓝色」
        self.bar.setRange(0, 100)
        self.bar.setValue(100)
        if cancelled:
            self._set_bar_state("cancel")
            self.phase_label.setText(message or "已停止")
        elif ok:
            self._set_bar_state("")
            self.phase_label.setText(message or "已完成")
        else:
            self._set_bar_state("error")
            self.phase_label.setText(message or "已中止")
        if message:
            self.fields["phase"].setText(message)
            self.append_log(message)
        if self._cancellable:
            # 任务已结束：按钮退化为「关闭」，失败后依然可点，用于清掉失败态
            self.stop_btn.setText("关闭")
            self.stop_btn.setEnabled(True)
            self.stop_btn.setVisible(True)
        self.setVisible(True)

    def reset(self) -> None:
        self._active = False
        self._cancellable = False
        self._clock.stop()
        self._started_at = 0.0
        self._set_bar_state("")
        self.stop_btn.setText("停止")
        self.stop_btn.setEnabled(True)
        self.stop_btn.setVisible(False)
        self.setVisible(False)
        self.bar.setRange(0, 0)
        self.bar.setValue(0)

    # ---------------- 内部 ----------------

    def enable_stop(self, text: str = "停止") -> None:
        """让「停止」按钮可用（同一面板被连续复用时调用）。"""
        self._cancellable = True
        self.stop_btn.setText(text)
        self.stop_btn.setEnabled(True)
        self.stop_btn.setVisible(True)

    def _on_stop_clicked(self) -> None:
        """运行中 → 请求中止；已结束（失败/成功）→ 关闭面板。"""
        if self._active:
            self.stop_btn.setEnabled(False)
            self.stop_btn.setText("正在停止…")
            self.summary_label.setText("正在中断传输，请稍候…")
            self.append_log("已请求停止，正在中断当前传输…")
            self.stop_requested.emit()
        else:
            self.reset()

    def _set_bar_state(self, state: str) -> None:
        """切换进度条配色（'' 正常 / 'error' 失败 / 'cancel' 已停止）。"""
        if self.bar.property("state") == state:
            return
        self.bar.setProperty("state", state)
        style = self.bar.style()
        if style is not None:
            style.unpolish(self.bar)
            style.polish(self.bar)
        self.bar.update()

    def _tick(self) -> None:
        if not self._active or not self._started_at:
            return
        elapsed = time.time() - self._started_at
        self.elapsed_label.setText(f"用时 {elapsed:.1f} 秒")
        self.fields["elapsed"].setText(f"{elapsed:.1f} 秒")

    def _on_toggle(self, checked: bool) -> None:
        self.detail_box.setVisible(checked)
        self.toggle_btn.setText("详情 ▴" if checked else "详情 ▾")


# --------------------------------------------------------------------------- #
# 工具函数
# --------------------------------------------------------------------------- #


def scrollable(widget: QWidget, object_name: str = "PageScroll") -> QScrollArea:
    """把页面内容包进滚动容器。

    必要性：标签页里的内容（卡片 + 按钮组）在窗口变矮或下方「传输详情面板」
    展开时会被压缩到低于最小尺寸，Qt 此时只能让控件互相重叠（表现为文字、
    按钮叠在一起）。套上滚动容器后，空间不足时改为出现滚动条，界面不再错乱。
    """
    area = QScrollArea()
    area.setObjectName(object_name)
    area.setWidgetResizable(True)
    area.setFrameShape(QFrame.NoFrame)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    area.setWidget(widget)
    # QScrollArea.setWidget() 会隐式打开子控件的 autoFillBackground，
    # 深色系统主题下会按 Window 角色色自绘成「黑底」，这里显式关掉。
    widget.setAutoFillBackground(False)
    viewport = area.viewport()
    if viewport is not None:
        viewport.setObjectName(f"{object_name}Viewport")
        viewport.setAttribute(Qt.WA_StyledBackground, True)
    return area


def open_in_explorer(path: str) -> None:
    """在系统文件管理器中打开目录/文件。"""
    if not path:
        return
    path = os.path.normpath(path)
    try:
        if sys.platform.startswith("win"):
            if os.path.isfile(path):
                subprocess.Popen(["explorer", "/select,", path])
            else:
                os.startfile(path)  # noqa: S606
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path if os.path.isdir(path) else os.path.dirname(path)])
        else:
            subprocess.Popen(["xdg-open", path if os.path.isdir(path) else os.path.dirname(path)])
    except Exception:
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))


def open_url(url: str) -> None:
    if url:
        QDesktopServices.openUrl(QUrl(url))


def confirm(parent: QWidget, title: str, text: str, danger: bool = False) -> bool:
    box = QMessageBox(parent)
    box.setWindowTitle(title)
    box.setText(text)
    box.setIcon(QMessageBox.Warning if danger else QMessageBox.Question)
    yes = box.addButton("确定", QMessageBox.AcceptRole)
    box.addButton("取消", QMessageBox.RejectRole)
    box.setDefaultButton(yes)
    box.exec()
    return box.clickedButton() is yes


def info(parent: QWidget, title: str, text: str) -> None:
    box = QMessageBox(parent)
    box.setWindowTitle(title)
    box.setText(text)
    box.setIcon(QMessageBox.Information)
    box.addButton("知道了", QMessageBox.AcceptRole)
    box.exec()


def warn(parent: QWidget, title: str, text: str) -> None:
    box = QMessageBox(parent)
    box.setWindowTitle(title)
    box.setText(text)
    box.setIcon(QMessageBox.Warning)
    box.addButton("知道了", QMessageBox.AcceptRole)
    box.exec()


def show_error(parent: QWidget, title: str, message: str, detail: str = "") -> None:
    box = QMessageBox(parent)
    box.setWindowTitle(title)
    box.setText(message)
    box.setIcon(QMessageBox.Critical)
    if detail:
        box.setDetailedText(detail)
    box.addButton("关闭", QMessageBox.AcceptRole)
    box.exec()


def hline() -> QFrame:
    line = QFrame()
    line.setFrameShape(QFrame.HLine)
    line.setStyleSheet(f"color: {PALETTE['border']}; background: {PALETTE['border']};")
    line.setFixedHeight(1)
    return line


def mono_label(text: str = "") -> QLabel:
    label = QLabel(text)
    label.setObjectName("Mono")
    label.setTextInteractionFlags(Qt.TextSelectableByMouse)
    return label


def kv_row(key: str, value_widget: QWidget) -> QHBoxLayout:
    row = QHBoxLayout()
    row.setSpacing(8)
    key_label = QLabel(key)
    key_label.setObjectName("Muted")
    key_label.setFixedWidth(88)
    row.addWidget(key_label)
    row.addWidget(value_widget, 1)
    return row


def human_size(kb: int | float) -> str:
    try:
        kb = float(kb)
    except (TypeError, ValueError):
        return "-"
    if kb <= 0:
        return "-"
    for unit in ("KB", "MB", "GB"):
        if kb < 1024:
            return f"{kb:.1f} {unit}"
        kb /= 1024
    return f"{kb:.1f} TB"


def clear_layout(layout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.setParent(None)
            widget.deleteLater()
        elif item.layout() is not None:
            clear_layout(item.layout())


def elide(text: str, limit: int = 80) -> str:
    text = (text or "").replace("\n", " ").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def iter_widgets(layout) -> Iterable[QWidget]:
    for i in range(layout.count()):
        item = layout.itemAt(i)
        if item and item.widget():
            yield item.widget()
