"""界面主题与样式表（浅色主题）。"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette

PALETTE = {
    "bg": "#f4f6f9",
    "surface": "#ffffff",
    "surface_alt": "#fafbfc",
    "border": "#e2e6ec",
    "border_strong": "#d0d7de",
    "text": "#1f2328",
    "muted": "#656d76",
    "faint": "#8b949e",
    "primary": "#2d6cdf",
    "primary_dark": "#1f56b8",
    "primary_soft": "#eaf1fd",
    "success": "#1a7f37",
    "success_soft": "#e6f4ea",
    "warning": "#9a6700",
    "warning_soft": "#fff8e1",
    "danger": "#b42318",
    "danger_soft": "#fdecea",
    "github": "#24292f",
    "gitee": "#c71d23",
}

STYLESHEET = """
* { font-family: "Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI", sans-serif; }

QWidget { color: %(text)s; font-size: 13px; }

QMainWindow, QDialog { background: %(bg)s; }

/* 页面容器显式上色：避免系统深色主题下透明控件露出深色调色板 */
#AppBody, QStackedWidget#PageStack { background: %(bg)s; }

#Sidebar {
    background: %(surface)s;
    border-right: 1px solid %(border)s;
}

#AppTitle { font-size: 17px; font-weight: 700; color: %(text)s; }
#AppSubtitle { font-size: 11px; color: %(faint)s; }

QPushButton {
    background: %(surface)s;
    border: 1px solid %(border_strong)s;
    border-radius: 6px;
    padding: 5px 14px;
    min-height: 22px;
    color: %(text)s;
}
QPushButton:hover { background: #f3f4f6; border-color: #b9c0c8; }
QPushButton:pressed { background: #e9ecef; }
QPushButton:disabled { color: %(faint)s; background: %(surface_alt)s; border-color: %(border)s; }

QPushButton[variant="primary"] {
    background: %(primary)s; border: 1px solid %(primary)s; color: #ffffff; font-weight: 600;
}
QPushButton[variant="primary"]:hover { background: %(primary_dark)s; border-color: %(primary_dark)s; }
QPushButton[variant="primary"]:disabled { background: #a9c0ea; border-color: #a9c0ea; color: #f2f5fa; }

QPushButton[variant="danger"] {
    background: %(surface)s; border: 1px solid #e6b3ae; color: %(danger)s;
}
QPushButton[variant="danger"]:hover { background: %(danger_soft)s; }
QPushButton[variant="danger"]:disabled {
    background: %(surface_alt)s; border-color: %(border)s; color: %(faint)s;
}

QPushButton[variant="ghost"] {
    background: transparent; border: 1px solid transparent; color: %(muted)s;
}
QPushButton[variant="ghost"]:hover { background: #eef1f5; color: %(text)s; }

QPushButton#NavButton {
    text-align: left;
    padding: 9px 14px;
    border: none;
    border-radius: 8px;
    background: transparent;
    color: %(muted)s;
    font-size: 13.5px;
}
QPushButton#NavButton:hover { background: #f0f2f5; color: %(text)s; }
QPushButton#NavButton:checked {
    background: %(primary_soft)s; color: %(primary)s; font-weight: 600;
}

QLineEdit, QPlainTextEdit, QTextEdit, QComboBox, QSpinBox, QListWidget, QTableWidget, QTreeWidget {
    background: %(surface)s;
    border: 1px solid %(border_strong)s;
    border-radius: 6px;
    padding: 5px 8px;
    selection-background-color: %(primary_soft)s;
    selection-color: %(text)s;
}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QComboBox:focus, QSpinBox:focus {
    border: 1px solid %(primary)s;
}
QLineEdit:disabled { background: %(surface_alt)s; color: %(faint)s; }
QLineEdit[echoMode="2"] { letter-spacing: 2px; }

QComboBox::drop-down { border: none; width: 22px; }
QComboBox QAbstractItemView {
    background: %(surface)s; border: 1px solid %(border_strong)s;
    selection-background-color: %(primary_soft)s; selection-color: %(text)s; outline: none;
}

QTableWidget, QTreeWidget {
    gridline-color: %(border)s;
    border-radius: 8px;
    padding: 0px;
}
QTableWidget::item, QTreeWidget::item { padding: 6px 6px; border: none; }
QTableWidget::item:selected, QTreeWidget::item:selected {
    background: %(primary_soft)s; color: %(text)s;
}
QHeaderView::section {
    background: %(surface_alt)s;
    color: %(muted)s;
    padding: 7px 8px;
    border: none;
    border-bottom: 1px solid %(border)s;
    border-right: 1px solid %(border)s;
    font-weight: 600;
}
QTableCornerButton::section { background: %(surface_alt)s; border: none; }

QListWidget::item { padding: 8px 10px; border-radius: 6px; }
QListWidget::item:selected { background: %(primary_soft)s; color: %(text)s; }
QListWidget::item:hover { background: #f0f2f5; }

QTabWidget::pane { border: 1px solid %(border)s; border-radius: 8px; background: %(surface)s; top: -1px; }
QTabBar::tab {
    background: transparent; padding: 7px 16px; margin-right: 2px;
    border: 1px solid transparent; border-top-left-radius: 7px; border-top-right-radius: 7px;
    color: %(muted)s;
}
QTabBar::tab:selected {
    background: %(surface)s; border-color: %(border)s; border-bottom-color: %(surface)s;
    color: %(text)s; font-weight: 600;
}
QTabBar::tab:hover:!selected { color: %(text)s; }

QFrame#Card {
    background: %(surface)s;
    border: 1px solid %(border)s;
    border-radius: 10px;
}

/* ---- 瀑布流卡片 ----
   背景一律写死为浅色，避免深色系统主题下 QScrollArea 视口 / 画布
   用调色板色自绘出「黑底」。 */
QScrollArea#MasonryView {
    background: %(bg)s; border: none; border-radius: 10px;
}
QWidget#MasonryViewport { background: %(bg)s; }
QWidget#MasonryCanvas { background: %(bg)s; }
QFrame#RepoCard {
    background: %(surface)s;
    border: 1px solid %(border_strong)s;
    border-radius: 12px;
}
QFrame#RepoCard:hover { border-color: %(primary)s; background: #fdfefe; }
QLabel#RankBadge { border-radius: 13px; font-weight: 700; font-size: 12px; }
QLabel#RepoName { font-size: 13.5px; font-weight: 700; color: %(text)s; }
QLabel#RepoMeta { font-size: 11.5px; color: %(muted)s; }
QLabel#RepoDesc { font-size: 12.5px; color: #3d444d; line-height: 150%%; }
QLabel#Chip {
    background: %(primary_soft)s; color: %(primary_dark)s;
    border-radius: 8px; padding: 1px 7px; font-size: 11px;
}
QPushButton#MiniButton {
    padding: 3px 10px; min-height: 18px; font-size: 12px; border-radius: 5px;
}

QLabel#CardTitle { font-size: 14px; font-weight: 700; }
QLabel#H1 { font-size: 18px; font-weight: 700; }
QLabel#H2 { font-size: 15px; font-weight: 700; }
QLabel#Muted { color: %(muted)s; }
QLabel#Faint { color: %(faint)s; font-size: 12px; }
QLabel#Mono { font-family: "Cascadia Mono", Consolas, monospace; }

QLabel#Badge {
    border-radius: 9px; padding: 2px 9px; font-size: 11.5px; font-weight: 600;
}

QProgressBar {
    border: none; background: #e9edf2; border-radius: 4px; height: 6px; text-align: center;
}
QProgressBar::chunk { background: %(primary)s; border-radius: 4px; }
/* 失败 / 已停止：换色，避免「明明失败却显示满格蓝色」造成误解 */
QProgressBar[state="error"]::chunk { background: %(danger)s; }
QProgressBar[state="cancel"]::chunk { background: %(border_strong)s; }

/* ---- 页签内的滚动容器 ----
   空间不足（窗口变矮 / 传输详情展开）时改为滚动，而不是把控件压到重叠。
   背景透明：让 QTabWidget::pane 的底色透上来，与其它页签保持一致。 */
QScrollArea#PageScroll { background: transparent; border: none; }
QWidget#PageScrollViewport { background: transparent; }

/* ---- 传输面板：进度条 + 详情 ---- */
QFrame#TransferPanel {
    background: %(surface_alt)s;
    border: 1px solid %(border)s;
    border-radius: 8px;
}
QLabel#TransferKind {
    background: %(primary_soft)s; color: %(primary_dark)s;
    border-radius: 8px; padding: 1px 8px; font-size: 11.5px; font-weight: 600;
}
QLabel#TransferPhase { font-weight: 600; }
QLabel#TransferSummary { color: %(muted)s; font-size: 12px; }
QLabel#TransferValue { color: %(text)s; font-size: 12px; }
QPlainTextEdit#TransferLog {
    background: %(surface)s;
    border: 1px solid %(border)s;
    border-radius: 6px;
    font-family: "Cascadia Mono", Consolas, monospace;
    font-size: 11.5px;
    padding: 4px 6px;
}

QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }
QScrollBar::handle:vertical { background: #cdd4dc; border-radius: 5px; min-height: 28px; }
QScrollBar::handle:vertical:hover { background: #b3bcc7; }
QScrollBar:horizontal { background: transparent; height: 10px; margin: 2px; }
QScrollBar::handle:horizontal { background: #cdd4dc; border-radius: 5px; min-width: 28px; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0px; width: 0px; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }

QStatusBar { background: %(surface)s; border-top: 1px solid %(border)s; color: %(muted)s; }
QStatusBar::item { border: none; }

QSplitter::handle { background: %(border)s; }
QSplitter::handle:horizontal { width: 1px; }
QSplitter::handle:vertical { height: 1px; }

QMenu { background: %(surface)s; border: 1px solid %(border_strong)s; border-radius: 6px; padding: 4px; }
QMenu::item { padding: 6px 22px 6px 12px; border-radius: 5px; }
QMenu::item:selected { background: %(primary_soft)s; }

QToolTip {
    background: #24292f; color: #ffffff; border: none; padding: 5px 8px; border-radius: 4px;
}
""" % PALETTE

DIFF_COLORS = {
    "add": "#e6f4ea",
    "del": "#fdecea",
    "hunk": "#eaf1fd",
    "meta": "#f6f8fa",
}

# 调色板角色 → 颜色（键使用字符串，便于在不导入 Qt 的场景下也安全）
_PALETTE_ROLES = {
    "Window": PALETTE["bg"],
    "WindowText": PALETTE["text"],
    "Base": PALETTE["surface"],
    "AlternateBase": PALETTE["surface_alt"],
    "Text": PALETTE["text"],
    "Button": PALETTE["surface"],
    "ButtonText": PALETTE["text"],
    "ToolTipBase": "#24292f",
    "ToolTipText": "#ffffff",
    "Highlight": PALETTE["primary_soft"],
    "HighlightedText": PALETTE["text"],
    "PlaceholderText": PALETTE["faint"],
    "BrightText": "#ffffff",
    "Link": PALETTE["primary"],
    "Mid": PALETTE["border"],
    "Dark": PALETTE["border_strong"],
    "Shadow": "#c9d1d9",
}


def light_palette() -> QPalette:
    """构造与本项目浅色主题一致的调色板。"""
    palette = QPalette()
    for role_name, color in _PALETTE_ROLES.items():
        role = getattr(QPalette, role_name, None)
        if role is not None:
            palette.setColor(role, QColor(color))
    for role_name in ("WindowText", "Text", "ButtonText"):
        role = getattr(QPalette, role_name, None)
        if role is not None:
            palette.setColor(QPalette.Disabled, role, QColor(PALETTE["faint"]))
    return palette


def apply_light_theme(app=None) -> None:
    """把应用锁定为浅色主题。

    Windows 处于深色模式时，Qt 会跟随系统给出一套深色调色板；此时凡是
    「按调色板自绘背景」的控件（``QScrollArea`` 视口、``setAutoFillBackground``
    的子控件等）都会变成深色甚至纯黑。本项目的视觉设计是浅色主题，
    因此这里显式声明浅色配色，保证在任何系统主题下外观一致。
    """
    if app is None:
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance()
    if app is None:
        return
    try:
        hints = app.styleHints()
        setter = getattr(hints, "setColorScheme", None)
        if setter is not None:
            setter(Qt.ColorScheme.Light)
    except Exception:  # noqa: BLE001 - 老版本 Qt 无此接口
        pass
    try:
        app.setPalette(light_palette())
    except Exception:  # noqa: BLE001
        pass

