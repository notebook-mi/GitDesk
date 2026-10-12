"""瀑布流展示控件与仓库卡片。

Qt 没有内置 masonry 布局，这里用「最短列优先」算法手工排布卡片：
每张卡片按当前列宽重新计算高度后放进最矮的一列，卡片高度不齐也能紧密贴合并形成错落效果。
"""

from __future__ import annotations

from PySide6.QtCore import QRect, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .style import PALETTE

# 排名徽标配色：金银铜 + 常规
_RANK_STYLES = {
    1: ("#f6b93b", "#8a5a00", "#fff6e0"),
    2: ("#b9c0c8", "#4b5563", "#f2f4f7"),
    3: ("#d9a06b", "#8a4b1f", "#fbeee2"),
}
_RANK_FALLBACK = ("#dbe1e8", "#5b6470", "#f6f8fa")


def _rank_style(rank: int) -> str:
    border, color, bg = _RANK_STYLES.get(rank, _RANK_FALLBACK)
    return (
        "QLabel#RankBadge {"
        f" background: {bg}; color: {color}; border: 1px solid {border};"
        " border-radius: 13px; font-weight: 700; font-size: 12px;"
        "}"
    )


def _compact(number) -> str:
    try:
        number = int(number)
    except (TypeError, ValueError):
        return "0"
    if number >= 1_000_000:
        return f"{number / 1_000_000:.1f}M"
    if number >= 1000:
        return f"{number / 1000:.1f}k"
    return str(number)


def _monogram(text: str, size: int) -> QPixmap:
    """用首字母生成占位头像，保证头像缺失时排版依然整齐。"""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    letters = [ch for ch in (text or "?") if ch.isalnum()]
    char = (letters[0] if letters else "?").upper()
    hue = (sum(ord(c) for c in (text or "?")) * 47) % 360
    color = QColor.fromHsv(hue, 90, 190)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(Qt.NoPen)
    painter.setBrush(color)
    painter.drawEllipse(0, 0, size, size)
    font = QFont()
    font.setPixelSize(int(size * 0.46))
    font.setBold(True)
    painter.setFont(font)
    painter.setPen(QColor("#ffffff"))
    painter.drawText(QRect(0, 0, size, size), Qt.AlignCenter, char)
    painter.end()
    return pixmap


def _rounded(pixmap: QPixmap, size: int) -> QPixmap:
    """把头像裁成圆形并缩放。"""
    out = QPixmap(size, size)
    out.fill(Qt.transparent)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.Antialiasing)
    path = QPainterPath()
    path.addEllipse(0, 0, size, size)
    painter.setClipPath(path)
    scaled = pixmap.scaled(
        size, size, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation
    )
    painter.drawPixmap(0, 0, scaled)
    painter.end()
    return out


def columns_for_width(
    width: int,
    min_card_width: int = 318,
    spacing: int = 14,
    max_columns: int = 4,
) -> int:
    """按可用宽度推算瀑布流列数（纯函数，便于独立验证）。"""
    if width <= 0:
        return 1
    fits = (width + spacing) // (min_card_width + spacing)
    return max(1, min(max_columns, int(fits)))


def avatar_pixmap(repo: dict, size: int = 34) -> QPixmap:
    """优先使用抓取到的头像，失败时退回首字母徽标。"""
    data = repo.get("avatar_bytes")
    if data:
        pixmap = QPixmap()
        if pixmap.loadFromData(data) and not pixmap.isNull():
            return _rounded(pixmap, size)
    return _monogram(repo.get("owner") or repo.get("name") or "?", size)


class RepoCard(QFrame):
    """单个仓库的瀑布流卡片。"""

    open_requested = Signal(str)     # 仓库主页地址
    clone_requested = Signal(dict)   # 仓库数据
    copy_requested = Signal(str)     # 克隆地址

    DESC_LINES_MAX = 8
    H_PADDING = 28  # 左右内边距之和

    def __init__(self, repo: dict, rank: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("RepoCard")
        self.repo = dict(repo)
        self.rank = rank

        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 12, 14, 12)
        outer.setSpacing(8)

        # ---- 头部：排名 + 头像 + 仓库名 ----
        head = QHBoxLayout()
        head.setSpacing(9)
        self.rank_label = QLabel(str(rank))
        self.rank_label.setObjectName("RankBadge")
        self.rank_label.setFixedSize(26, 26)
        self.rank_label.setAlignment(Qt.AlignCenter)
        self.rank_label.setStyleSheet(_rank_style(rank))
        self.rank_label.setToolTip(f"榜单第 {rank} 名")
        head.addWidget(self.rank_label, 0, Qt.AlignTop)

        self.avatar = QLabel()
        self.avatar.setFixedSize(34, 34)
        self.avatar.setPixmap(avatar_pixmap(self.repo, 34))
        head.addWidget(self.avatar, 0, Qt.AlignTop)

        title_box = QVBoxLayout()
        title_box.setSpacing(2)
        self.title = QLabel(repo.get("full_name", ""))
        self.title.setObjectName("RepoName")
        self.title.setToolTip(repo.get("html_url", ""))
        self.title.setWordWrap(True)
        self.title.setCursor(Qt.PointingHandCursor)
        self.subtitle = QLabel(self._meta_html())
        self.subtitle.setObjectName("RepoMeta")
        self.subtitle.setTextFormat(Qt.RichText)
        self.subtitle.setToolTip("语言 / 星标 / 复刻数")
        title_box.addWidget(self.title)
        title_box.addWidget(self.subtitle)
        head.addLayout(title_box, 1)
        outer.addLayout(head)

        # ---- 简介（中文）----
        self.desc = QLabel(self._description())
        self.desc.setObjectName("RepoDesc")
        self.desc.setWordWrap(True)
        self.desc.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.desc.setTextInteractionFlags(Qt.TextSelectableByMouse)
        original = repo.get("description", "")
        if original and original != self._description():
            self.desc.setToolTip(f"原文：{original}")
        outer.addWidget(self.desc)

        # ---- 主题标签 ----
        self.topic_row = QHBoxLayout()
        self.topic_row.setSpacing(5)
        self.topic_chips: list[QLabel] = []
        for topic in (repo.get("topics") or [])[:3]:
            chip = QLabel(str(topic)[:18])
            chip.setObjectName("Chip")
            chip.setToolTip(str(topic))
            self.topic_row.addWidget(chip)
            self.topic_chips.append(chip)
        if self.topic_chips:
            self.topic_row.addStretch(1)
            outer.addLayout(self.topic_row)

        # ---- 操作 ----
        actions = QHBoxLayout()
        actions.setSpacing(6)
        self.detail_btn = QPushButton("查看详情")
        self.detail_btn.setObjectName("MiniButton")
        self.detail_btn.setCursor(Qt.PointingHandCursor)
        self.detail_btn.setToolTip("在浏览器中打开该仓库主页")
        self.detail_btn.clicked.connect(lambda: self.open_requested.emit(self.repo.get("html_url", "")))
        self.clone_btn = QPushButton("克隆到本地")
        self.clone_btn.setObjectName("MiniButton")
        self.clone_btn.setProperty("variant", "primary")
        self.clone_btn.setCursor(Qt.PointingHandCursor)
        self.clone_btn.setToolTip("克隆到「远程仓库」页中设置的目录")
        self.clone_btn.clicked.connect(lambda: self.clone_requested.emit(dict(self.repo)))
        self.copy_btn = QPushButton("复制地址")
        self.copy_btn.setObjectName("MiniButton")
        self.copy_btn.setProperty("variant", "ghost")
        self.copy_btn.setCursor(Qt.PointingHandCursor)
        self.copy_btn.clicked.connect(lambda: self.copy_requested.emit(self.repo.get("clone_url", "")))
        actions.addWidget(self.detail_btn)
        actions.addWidget(self.clone_btn)
        actions.addStretch(1)
        actions.addWidget(self.copy_btn)
        outer.addLayout(actions)

    # ---------------- 内容 ---------------- #

    def _description(self) -> str:
        text = (self.repo.get("description_zh") or self.repo.get("description") or "").strip()
        return text or "该项目未填写简介。"

    def _meta_html(self) -> str:
        repo = self.repo
        parts = []
        language = (repo.get("language") or "").strip()
        if language:
            color = (repo.get("language_color") or PALETTE["faint"]).strip()
            parts.append(f"<span style='color:{color}'>●</span> {language}")
        parts.append(f"★ {_compact(repo.get('stars', 0))}")
        if repo.get("forks"):
            parts.append(f"⑂ {_compact(repo['forks'])}")
        period = repo.get("stars_period") or 0
        if period:
            label = repo.get("period_label") or "本期"
            parts.append(f"<span style='color:{PALETTE['success']}'>{label} +{_compact(period)}</span>")
        return " &nbsp;·&nbsp; ".join(parts)

    # ---------------- 布局 ---------------- #

    def prepare_width(self, width: int) -> None:
        """按给定卡片宽度重算简介高度，保证瀑布流高度可精确预测。"""
        self.setFixedWidth(width)
        inner = max(100, width - self.H_PADDING - 2)
        metrics = QFontMetrics(self.desc.font())
        line = metrics.lineSpacing()
        rect = metrics.boundingRect(
            QRect(0, 0, inner, line * self.DESC_LINES_MAX),
            Qt.TextWordWrap | Qt.AlignTop,
            self.desc.text(),
        )
        lines = max(1, min(self.DESC_LINES_MAX, round(rect.height() / max(1, line))))
        self.desc.setFixedHeight(line * lines)


class MasonryCanvas(QWidget):
    """按最短列优先排布卡片的容器。

    注意：``QScrollArea.setWidget()`` 会把子控件设为 ``autoFillBackground``，
    在深色系统主题下该控件会用深色的 Window 色填充，表现为「黑底」。
    因此这里显式指定 objectName 并配合样式表锁定浅色背景，不受系统主题影响。
    """

    def __init__(
        self,
        spacing: int = 14,
        min_card_width: int = 318,
        max_columns: int = 4,
        padding: int = 2,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("MasonryCanvas")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self._spacing = spacing
        self._min_card_width = min_card_width
        self._max_columns = max_columns
        self._padding = max(0, int(padding))
        self._cards: list[QWidget] = []
        self._content_height = 0
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

    # ---------------- 卡片管理 ---------------- #

    @property
    def cards(self) -> list[QWidget]:
        return list(self._cards)

    def add_card(self, card: QWidget) -> QWidget:
        card.setParent(self)
        card.show()
        self._cards.append(card)
        return card

    def clear_cards(self) -> None:
        for card in self._cards:
            card.setParent(None)
            card.deleteLater()
        self._cards.clear()
        self._content_height = 0
        self.setFixedHeight(0)

    # ---------------- 布局 ---------------- #

    def column_count(self, width: int | None = None) -> int:
        width = self.width() if width is None else width
        return columns_for_width(
            width, self._min_card_width, self._spacing, self._max_columns
        )

    def relayout(self, width: int) -> int:
        """按给定宽度重新排布，返回内容总高度。"""
        width = max(1, int(width))
        pad = self._padding
        inner = max(1, width - pad * 2)
        count = self.column_count(inner)
        gap = self._spacing
        card_width = max(180, (inner - gap * (count - 1)) // count)
        positions_x = [pad + i * (card_width + gap) for i in range(count)]
        heights = [pad] * count

        for card in self._cards:
            if hasattr(card, "prepare_width"):
                card.prepare_width(card_width)
            else:
                card.setFixedWidth(card_width)
            height = card.sizeHint().height()
            column = heights.index(min(heights))
            y = heights[column] + (gap if heights[column] > pad else 0)
            card.move(positions_x[column], y)
            heights[column] = y + height

        self._content_height = (max(heights) + pad) if heights else 0
        self.setFixedSize(width, self._content_height)
        return self._content_height

    def sizeHint(self):  # noqa: N802
        from PySide6.QtCore import QSize

        return QSize(max(1, self.width()), self._content_height)


class MasonryView(QScrollArea):
    """带滚动条的瀑布流视图。"""

    def __init__(
        self,
        spacing: int = 14,
        min_card_width: int = 318,
        max_columns: int = 4,
        padding: int = 2,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("MasonryView")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFrameShape(QFrame.NoFrame)
        self.setWidgetResizable(False)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        # 视口同样锁定浅色背景：滚动区视口默认使用 Base 调色板色，深色主题下会发黑
        viewport = self.viewport()
        viewport.setObjectName("MasonryViewport")
        viewport.setAttribute(Qt.WA_StyledBackground, True)
        self.canvas = MasonryCanvas(spacing, min_card_width, max_columns, padding)
        self.setWidget(self.canvas)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(30)
        self._timer.timeout.connect(self.refresh_layout)

    # ---------------- 对外接口 ---------------- #

    def add_card(self, card: QWidget) -> QWidget:
        self.canvas.add_card(card)
        self._schedule()
        return card

    def clear_cards(self) -> None:
        self.canvas.clear_cards()
        self._schedule()

    @property
    def cards(self) -> list[QWidget]:
        return self.canvas.cards

    def _schedule(self) -> None:
        self._timer.start()

    def refresh_layout(self) -> int:
        width = self.viewport().width()
        if width <= 0:
            width = max(1, self.width())
        return self.canvas.relayout(width)

    # ---------------- 事件 ---------------- #

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._schedule()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._schedule()
