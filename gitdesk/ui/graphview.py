"""提交演化图控件：以泳道图形式自绘提交历史。

只负责绘制、悬停与滚动，布局算法位于 :mod:`gitdesk.core.graph`，
两者解耦后可以单独对算法做单元测试。

绘制全部由 ``QPainter`` 完成（不使用 ``QGraphicsView``），背景显式填充白色，
因此在系统深色主题下也不会出现黑底。
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QPointF, QRect, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QAbstractScrollArea, QFrame, QToolTip, QWidget

from ..core.graph import GraphRow

#: 单行高度（像素）
ROW_HEIGHT = 26
#: 单条泳道宽度（像素）
LANE_WIDTH = 18
#: 泳道区左侧留白
LANE_PADDING = 8
#: 泳道数量下限（保证窄历史也留有呼吸感）
MIN_LANES = 3
#: 泳道色板（8 色循环）
LANE_COLORS: tuple[str, ...] = (
    "#2d6cdf",
    "#1a7f37",
    "#9a6700",
    "#b42318",
    "#8250df",
    "#0969da",
    "#bf3989",
    "#57606a",
)

COLOR_BG = "#ffffff"
COLOR_HOVER = "#f4f6f9"
COLOR_TEXT = "#1f2328"
COLOR_MUTED = "#8b949e"
COLOR_HEAD = "#2d6cdf"
COLOR_BADGE_BG = "#eaf1fd"
COLOR_BADGE_FG = "#1f56b8"

#: 徽标最多直接显示的个数（其余以「+N」形式提示）
MAX_BADGES = 4


class CommitGraphView(QAbstractScrollArea):
    """提交演化图（泳道图）自绘控件。

    使用方式::

        view = CommitGraphView()
        view.set_commits(layout_graph(GitEngine.graph(path, 80)))

    控件内部按像素滚动（行高 26px），滚轮每次滚动 3 行。
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._rows: list[GraphRow] = []
        self._error = ""
        self._hover = -1
        self._lane_count = 1

        self.setObjectName("CommitGraphView")
        self.setFrameShape(QFrame.NoFrame)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setFocusPolicy(Qt.NoFocus)

        viewport = self.viewport()
        viewport.setObjectName("CommitGraphViewport")
        viewport.setMouseTracking(True)
        viewport.setAutoFillBackground(False)
        # 每次 paintEvent 都会整块填白，启用不透明绘制可避免深色主题透底
        viewport.setAttribute(Qt.WA_OpaquePaintEvent, True)

        bar = self.verticalScrollBar()
        bar.setSingleStep(ROW_HEIGHT)
        bar.valueChanged.connect(self._on_scrolled)

        self._mono_font = QFont(self.font())
        self._mono_font.setFamilies(["Cascadia Mono", "Consolas", "Courier New", "monospace"])
        self._mono_font.setPixelSize(12)

        self._badge_font = QFont(self.font())
        self._badge_font.setPixelSize(11)

        self._meta_font = QFont(self.font())
        self._meta_font.setPixelSize(12)

        self._subject_font = QFont(self.font())
        self._subject_font.setPixelSize(13)

    # ------------------------------------------------------------------ #
    # 对外接口
    # ------------------------------------------------------------------ #

    def set_commits(self, rows: list[GraphRow], error: str = "") -> None:
        """设置要绘制的行（由 :func:`gitdesk.core.graph.layout_graph` 生成）。

        Args:
            rows: 布局后的行列表；允许传 ``None`` 或空列表表示无数据。
            error: 读取失败时的提示文案（无数据时居中显示）。
        """
        coerced = [_coerce_row(row) for row in (rows or [])]
        self._rows = [row for row in coerced if row is not None]
        self._error = (error or "").strip()
        self._hover = -1
        self._lane_count = self._measure_lane_count()
        self.verticalScrollBar().setValue(0)
        self._update_scroll_range()
        self.viewport().update()

    def row_count(self) -> int:
        """当前行数。"""
        return len(self._rows)

    def clear(self) -> None:
        """清空图形（切换仓库时避免残留旧数据）。"""
        self._rows = []
        self._error = ""
        self._hover = -1
        self._lane_count = 1
        self.verticalScrollBar().setRange(0, 0)
        self.viewport().update()

    # ------------------------------------------------------------------ #
    # 尺寸 / 滚动
    # ------------------------------------------------------------------ #

    def resizeEvent(self, event) -> None:  # noqa: D102
        super().resizeEvent(event)
        self._update_scroll_range()

    def wheelEvent(self, event) -> None:  # noqa: D102
        delta = event.angleDelta().y()
        if not delta:
            super().wheelEvent(event)
            return
        steps = int(delta / 120) or (1 if delta > 0 else -1)
        bar = self.verticalScrollBar()
        bar.setValue(bar.value() - steps * ROW_HEIGHT * 3)
        event.accept()

    def _on_scrolled(self, _value: int) -> None:
        self.viewport().update()

    def _update_scroll_range(self) -> None:
        total = len(self._rows) * ROW_HEIGHT
        # 控件尚未显示时 viewport 尺寸还是旧值，退回用控件自身高度估算
        page = self.viewport().height() if self.isVisible() else max(0, self.height())
        bar = self.verticalScrollBar()
        bar.setPageStep(max(ROW_HEIGHT, page))
        bar.setRange(0, max(0, total - page))

    # ------------------------------------------------------------------ #
    # 交互
    # ------------------------------------------------------------------ #

    def viewportEvent(self, event) -> bool:  # noqa: D102
        kind = event.type()
        if kind == QEvent.Type.ToolTip:
            index = self._row_at(event.pos().y())
            if 0 <= index < len(self._rows):
                QToolTip.showText(event.globalPos(), self._tooltip_for(self._rows[index]), self)
                return True
            QToolTip.hideText()
            return True
        if kind == QEvent.Type.MouseMove:
            index = self._row_at(event.pos().y())
            if index != self._hover:
                self._hover = index
                self.viewport().update()
            return True
        if kind == QEvent.Type.Leave:
            if self._hover != -1:
                self._hover = -1
                self.viewport().update()
            return True
        return super().viewportEvent(event)

    def _row_at(self, y: int) -> int:
        """视口纵坐标 → 行下标（超出范围返回 -1）。"""
        if not self._rows:
            return -1
        offset = self.verticalScrollBar().value()
        index = int((y + offset) // ROW_HEIGHT)
        if index < 0 or index >= len(self._rows):
            return -1
        return index

    @staticmethod
    def _tooltip_for(row: GraphRow) -> str:
        refs = " ".join(row.refs)
        parts = [f"提交 {row.sha}"]
        if refs:
            parts.append(f"引用：{refs}")
        parts.append(f"作者：{row.author}  时间：{row.date}")
        parts.append("")
        parts.append(row.subject or "（无提交说明）")
        return "\n".join(parts)

    # ------------------------------------------------------------------ #
    # 绘制
    # ------------------------------------------------------------------ #

    def paintEvent(self, event) -> None:  # noqa: D102
        painter = QPainter(self.viewport())
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.TextAntialiasing, True)
        rect = self.viewport().rect()
        # 显式填白：深色系统主题下也不会露出黑底
        painter.fillRect(rect, QColor(COLOR_BG))

        if not self._rows:
            font = QFont(self.font())
            font.setPixelSize(13)
            painter.setFont(font)
            painter.setPen(QColor(COLOR_MUTED))
            painter.drawText(rect, Qt.AlignCenter, self._error or "暂无提交记录")
            painter.end()
            return

        offset = self.verticalScrollBar().value()
        lane_area = self._lane_area_width()
        first = max(0, offset // ROW_HEIGHT)
        last = min(len(self._rows), (offset + rect.height()) // ROW_HEIGHT + 2)
        for index in range(first, last):
            y_top = index * ROW_HEIGHT - offset
            self._paint_row(
                painter,
                row=self._rows[index],
                index=index,
                y_top=y_top,
                width=rect.width(),
                lane_area=lane_area,
            )
        painter.end()

    def _paint_row(
        self,
        painter: QPainter,
        *,
        row: GraphRow,
        index: int,
        y_top: int,
        width: int,
        lane_area: int,
    ) -> None:
        """绘制单行：悬停底色 → 连线 → 圆点 → 文本。"""
        if index == self._hover:
            painter.fillRect(QRect(0, y_top, width, ROW_HEIGHT), QColor(COLOR_HOVER))

        y_mid = y_top + ROW_HEIGHT / 2.0
        self._paint_connectors(painter, row, y_mid)
        self._paint_dot(painter, row, y_mid)
        self._paint_text(painter, row, y_top, width, lane_area)

    def _paint_connectors(self, painter: QPainter, row: GraphRow, y_mid: float) -> None:
        """绘制「本行圆点 → 下一行圆点」之间的连线。"""
        for connector in row.connectors:
            try:
                lane_from = int(connector.get("from", 0))
                lane_to = int(connector.get("to", 0))
            except (TypeError, ValueError):
                continue
            kind = str(connector.get("kind") or "straight")
            # 颜色跟随「这条线代表的分支」：分叉线用目标泳道色，汇入线用来源泳道色
            color_lane = lane_to if kind == "branch" else lane_from
            color = QColor(self._lane_color(color_lane))
            pen = QPen(color, 1.6)
            if kind == "merge":
                pen.setStyle(Qt.DashLine)
                pen.setDashPattern([4.0, 3.0])
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)

            x_from = self._lane_center(lane_from)
            x_to = self._lane_center(lane_to)
            y_to = y_mid + ROW_HEIGHT
            if x_from == x_to:
                painter.drawLine(QPointF(x_from, y_mid), QPointF(x_to, y_to))
                continue
            path = QPainterPath(QPointF(x_from, y_mid))
            path.cubicTo(
                QPointF(x_from, y_mid + ROW_HEIGHT * 0.45),
                QPointF(x_to, y_mid + ROW_HEIGHT * 0.55),
                QPointF(x_to, y_to),
            )
            painter.drawPath(path)

    def _paint_dot(self, painter: QPainter, row: GraphRow, y_mid: float) -> None:
        """绘制本行圆点：HEAD 实心 + 光晕，普通提交空心。"""
        color = QColor(self._lane_color(row.lane))
        center = QPointF(self._lane_center(row.lane), y_mid)
        if row.is_head:
            halo = QColor(color)
            halo.setAlpha(46)
            painter.setPen(Qt.NoPen)
            painter.setBrush(halo)
            painter.drawEllipse(center, 8.4, 8.4)
            painter.setPen(QPen(QColor(COLOR_BG), 1.4))
            painter.setBrush(QColor(COLOR_HEAD))
            painter.drawEllipse(center, 5.0, 5.0)
            return
        painter.setPen(QPen(color, 1.8))
        painter.setBrush(QColor(COLOR_BG))
        painter.drawEllipse(center, 4.2, 4.2)

    def _paint_text(
        self,
        painter: QPainter,
        row: GraphRow,
        y_top: int,
        width: int,
        lane_area: int,
    ) -> None:
        """绘制右侧信息列：短 hash → 引用徽标 → 提交说明 → 作者 · 日期。"""
        right = width - 14
        if right <= lane_area + 40:
            return  # 太窄时只保留泳道图

        x = lane_area + 6
        # 1) 短 hash（等宽字体）
        mono_metrics = QFontMetrics(self._mono_font)
        painter.setFont(self._mono_font)
        painter.setPen(QColor(COLOR_MUTED))
        short_text = row.short or row.sha[:8]
        short_width = mono_metrics.horizontalAdvance(short_text)
        painter.drawText(
            QRect(x, y_top, short_width + 2, ROW_HEIGHT),
            int(Qt.AlignLeft | Qt.AlignVCenter),
            short_text,
        )
        x += short_width + 10

        # 2) 引用徽标
        x = self._paint_badges(painter, row, x, y_top, right)

        # 3) 作者 · 日期（右对齐，先算宽度再给说明留空间）
        meta_metrics = QFontMetrics(self._meta_font)
        meta_text = self._meta_text(row)
        meta_limit = max(70, int((right - x) * 0.40))
        meta_text = meta_metrics.elidedText(meta_text, Qt.ElideRight, meta_limit)
        meta_width = meta_metrics.horizontalAdvance(meta_text)
        painter.setFont(self._meta_font)
        painter.setPen(QColor(COLOR_MUTED))
        painter.drawText(
            QRect(right - meta_width, y_top, meta_width + 2, ROW_HEIGHT),
            int(Qt.AlignRight | Qt.AlignVCenter),
            meta_text,
        )

        # 4) 提交说明（占据剩余宽度，超出省略）
        subject_width = right - x - meta_width - 12
        if subject_width <= 24:
            return
        subject_metrics = QFontMetrics(self._subject_font)
        subject = subject_metrics.elidedText(row.subject or "（无提交说明）", Qt.ElideRight, subject_width)
        painter.setFont(self._subject_font)
        painter.setPen(QColor(COLOR_TEXT))
        painter.drawText(
            QRect(x, y_top, subject_width, ROW_HEIGHT),
            int(Qt.AlignLeft | Qt.AlignVCenter),
            subject,
        )

    def _paint_badges(
        self, painter: QPainter, row: GraphRow, x: int, y_top: int, right: int
    ) -> int:
        """绘制引用徽标，返回徽标区之后的横坐标。"""
        if not row.refs:
            return x
        metrics = QFontMetrics(self._badge_font)
        painter.setFont(self._badge_font)
        budget = max(60, int((right - x) * 0.45))
        used = 0
        shown = 0
        badge_y = y_top + (ROW_HEIGHT - 16) / 2.0
        for label in row.refs[:MAX_BADGES]:
            text_width = metrics.horizontalAdvance(label) + 12
            if used and used + text_width > budget:
                break
            fill, ink = self._badge_colors(row, label)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(fill))
            painter.drawRoundedRect(QRectF(x + used, badge_y, text_width, 16.0), 4.0, 4.0)
            painter.setPen(QColor(ink))
            painter.drawText(QRectF(x + used, badge_y, text_width, 16.0), int(Qt.AlignCenter), label)
            used += text_width + 4
            shown += 1
        # 放不下的引用用「+N」提示，避免信息被静默丢弃
        extra = len(row.refs) - shown
        if extra > 0:
            text = f"+{extra}"
            text_width = metrics.horizontalAdvance(text) + 10
            if used + text_width <= budget + 24:
                painter.setPen(Qt.NoPen)
                painter.setBrush(QColor(COLOR_BADGE_BG))
                painter.drawRoundedRect(QRectF(x + used, badge_y, text_width, 16.0), 4.0, 4.0)
                painter.setPen(QColor(COLOR_MUTED))
                painter.drawText(
                    QRectF(x + used, badge_y, text_width, 16.0), int(Qt.AlignCenter), text
                )
                used += text_width + 4
        return x + used + 4 if used else x

    def _badge_colors(self, row: GraphRow, label: str) -> tuple[str, str]:
        """徽标配色：HEAD 与当前分支用实底主色，其它引用用中性浅蓝。"""
        if label == "HEAD":
            return COLOR_HEAD, "#ffffff"
        # 当前分支：HEAD 所在提交上的本地分支（远程引用含 "/"，标签不参与高亮）
        if row.is_head and "/" not in label:
            return COLOR_HEAD, "#ffffff"
        return COLOR_BADGE_BG, COLOR_BADGE_FG

    @staticmethod
    def _meta_text(row: GraphRow) -> str:
        author = (row.author or "").strip()
        date = (row.date or "").strip()
        if len(date) > 16:  # 去掉秒，保持行内紧凑
            date = date[:16]
        if author and date:
            return f"{author} · {date}"
        return author or date

    # ------------------------------------------------------------------ #
    # 几何辅助
    # ------------------------------------------------------------------ #

    def _measure_lane_count(self) -> int:
        """统计图中实际用到的泳道数量（含连线经过的泳道）。"""
        lanes = 1
        for row in self._rows:
            lanes = max(lanes, row.lane + 1)
            for connector in row.connectors:
                for key in ("from", "to"):
                    try:
                        lanes = max(lanes, int(connector.get(key, 0)) + 1)
                    except (TypeError, ValueError):
                        continue
        return max(1, lanes)

    def _lane_area_width(self) -> int:
        """左侧泳道区宽度：``max(3, 泳道数 + 1) * 18 + 8``。"""
        lanes = max(MIN_LANES, self._lane_count + 1)
        return lanes * LANE_WIDTH + LANE_PADDING

    @staticmethod
    def _lane_center(lane: int) -> float:
        """泳道中心横坐标。"""
        index = max(0, lane)
        return LANE_PADDING + index * LANE_WIDTH + LANE_WIDTH / 2.0

    @staticmethod
    def _lane_color(lane: int) -> str:
        """泳道颜色（8 色循环）。"""
        return LANE_COLORS[max(0, lane) % len(LANE_COLORS)]


def _coerce_row(row) -> GraphRow | None:
    """把外部传入的行对象归一化为 :class:`GraphRow`（容错，便于测试）。"""
    if isinstance(row, GraphRow):
        return row
    if isinstance(row, dict):
        return GraphRow(
            sha=str(row.get("sha", "")),
            short=str(row.get("short", "")),
            subject=str(row.get("subject", "")),
            author=str(row.get("author", "")),
            date=str(row.get("date", "")),
            lane=int(row.get("lane", 0) or 0),
            parents=list(row.get("parents") or []),
            refs=list(row.get("refs") or []),
            connectors=list(row.get("connectors") or []),
            is_merge=bool(row.get("is_merge", False)),
            is_head=bool(row.get("is_head", False)),
        )
    return None
