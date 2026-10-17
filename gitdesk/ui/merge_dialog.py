"""合并冲突解决窗口：逐个编辑冲突文件、标记已解决、完成或中止合并。"""

from __future__ import annotations

import os

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..core.gitengine import GitEngine, GitError
from ..core.logger import get_logger
from .widgets import confirm, show_error

log = get_logger("ui.merge")

#: 冲突标记前缀，用于提示用户「这些行必须删掉才算解决」
MARKERS = ("<<<<<<<", "=======", ">>>>>>>", "|||||||")


class MergeConflictDialog(QDialog):
    """手动解决合并冲突。

    打开时从 ``GitEngine.merge_state`` 读取现场，逐个文件编辑；
    保存即 ``git add``（标记已解决），全部解决后可提交合并，也可中止回滚。
    """

    finished_with_result = Signal(str)   # "merged" / "aborted" / "closed"

    def __init__(self, parent: QWidget | None, path: str, author: str = "", email: str = "") -> None:
        super().__init__(parent)
        self.path = path
        self.author = author or "GitDesk"
        self.email = email or "gitdesk@localhost"
        self._state: dict = {}
        self._current: str = ""
        self._resolved: set[str] = set()
        self._loading = False
        self._all_files: list[str] = []

        self.setWindowTitle("解决合并冲突")
        self.resize(980, 660)
        self.setMinimumSize(760, 520)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 14)
        layout.setSpacing(10)

        self.title = QLabel("解决合并冲突")
        self.title.setObjectName("H2")
        layout.addWidget(self.title)

        self.desc = QLabel("")
        self.desc.setObjectName("Muted")
        self.desc.setWordWrap(True)
        layout.addWidget(self.desc)

        splitter = QSplitter(Qt.Horizontal)

        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.setSpacing(6)
        lv.addWidget(QLabel("冲突文件"))
        self.file_list = QListWidget()
        self.file_list.currentRowChanged.connect(self._on_file_selected)
        lv.addWidget(self.file_list, 1)
        splitter.addWidget(left)

        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(6)
        self.file_label = QLabel("—")
        self.file_label.setObjectName("Faint")
        rv.addWidget(self.file_label)

        tools = QHBoxLayout()
        tools.setSpacing(8)
        self.btn_ours = QPushButton("使用我的版本")
        self.btn_ours.setProperty("variant", "ghost")
        self.btn_ours.clicked.connect(lambda: self._load_side("ours"))
        self.btn_theirs = QPushButton("使用对方版本")
        self.btn_theirs.setProperty("variant", "ghost")
        self.btn_theirs.clicked.connect(lambda: self._load_side("theirs"))
        self.btn_reload = QPushButton("还原冲突标记")
        self.btn_reload.setProperty("variant", "ghost")
        self.btn_reload.clicked.connect(lambda: self._load_file(self._current))
        tools.addWidget(self.btn_ours)
        tools.addWidget(self.btn_theirs)
        tools.addWidget(self.btn_reload)
        tools.addStretch(1)
        rv.addLayout(tools)

        self.editor = QPlainTextEdit()
        font = QFont("Consolas")
        font.setStyleHint(QFont.Monospace)
        font.setPointSize(10)
        self.editor.setFont(font)
        self.editor.setTabChangesFocus(False)
        self.editor.setLineWrapMode(QPlainTextEdit.NoWrap)
        rv.addWidget(self.editor, 1)

        # 冲突块着色：我方段浅蓝、对方段浅绿、标记行黄底
        self._fmt_ours = QTextCharFormat()
        self._fmt_ours.setBackground(QColor("#eaf1fd"))
        self._fmt_theirs = QTextCharFormat()
        self._fmt_theirs.setBackground(QColor("#e6f4ea"))
        self._fmt_marker = QTextCharFormat()
        self._fmt_marker.setBackground(QColor("#fff3cd"))
        self._fmt_marker.setForeground(QColor("#9a6700"))
        self._fmt_marker.setFontWeight(QFont.Bold)
        self._hl_timer = QTimer(self)
        self._hl_timer.setSingleShot(True)
        self._hl_timer.setInterval(120)
        self._hl_timer.timeout.connect(self._refresh_highlights)
        self.editor.textChanged.connect(self._hl_timer.start)

        self.hint = QLabel("编辑后点「保存并标记已解决」；冲突标记（<<<<<<< / ======= / >>>>>>>）需要全部删除。")
        self.hint.setObjectName("Faint")
        self.hint.setWordWrap(True)
        rv.addWidget(self.hint)
        splitter.addWidget(right)
        splitter.setSizes([240, 720])
        layout.addWidget(splitter, 1)

        bottom = QHBoxLayout()
        bottom.setSpacing(8)
        self.status = QLabel("")
        self.status.setObjectName("Faint")
        bottom.addWidget(self.status)
        bottom.addStretch(1)
        self.btn_abort = QPushButton("中止合并")
        self.btn_abort.setProperty("variant", "danger")
        self.btn_abort.clicked.connect(self._abort)
        self.btn_save = QPushButton("保存并标记已解决")
        self.btn_save.clicked.connect(self._save_current)
        self.btn_finish = QPushButton("完成合并")
        self.btn_finish.setProperty("variant", "primary")
        self.btn_finish.clicked.connect(self._finish)
        bottom.addWidget(self.btn_abort)
        bottom.addWidget(self.btn_save)
        bottom.addWidget(self.btn_finish)
        layout.addLayout(bottom)

        self.reload()

    # ------------------------------------------------------------------ #
    # 数据加载
    # ------------------------------------------------------------------ #

    def reload(self) -> None:
        try:
            self._state = GitEngine.merge_state(self.path)
        except Exception as exc:  # noqa: BLE001
            show_error(self, "读取合并状态失败", str(exc))
            self._state = {"active": False, "conflicts": []}
        conflicts = self._state.get("conflicts") or []
        self._all_files = [c["path"] for c in conflicts]
        for name in list(self._resolved):
            if name not in self._all_files:
                self._all_files.append(name)

        source = str(self._state.get("source") or "").replace("refs/heads/", "").replace(
            "refs/remotes/", ""
        )
        branch = self._state.get("branch") or ""
        if self._state.get("active"):
            self.desc.setText(
                f"正在合并「{source or '目标分支'}」到「{branch or '当前分支'}」。"
                f"共 {len(conflicts)} 个文件存在冲突，需要逐个编辑并标记已解决；"
                "全部解决后点「完成合并」。"
            )
        else:
            self.desc.setText("当前没有进行中的合并。")

        self._loading = True
        self.file_list.clear()
        for name in self._all_files:
            resolved = name in self._resolved
            item = QListWidgetItem(("✓  " if resolved else "●  ") + name)
            item.setToolTip(name)
            item.setData(Qt.UserRole, name)
            if resolved:
                item.setForeground(QColor("#1a7f37"))
            self.file_list.addItem(item)
        self._loading = False

        if self._all_files:
            self.file_list.setCurrentRow(0)
            self._load_file(self._all_files[0])
        else:
            self._current = ""
            self.editor.clear()
            self.editor.setReadOnly(True)
            self.file_label.setText("—")
        self._sync_status()

    def _sync_status(self) -> None:
        total = len(self._all_files)
        done = len(self._resolved)
        self.status.setText(f"已解决 {done} / {total}" if total else "无冲突文件")
        self.btn_finish.setEnabled(total > 0 and done == total)
        self.btn_save.setEnabled(bool(self._current) and self._current in self._all_files)
        editable = bool(self._current) and not self._is_binary(self._current)
        self.editor.setReadOnly(not editable)
        self.btn_ours.setEnabled(bool(self._current))
        self.btn_theirs.setEnabled(bool(self._current))
        self.btn_reload.setEnabled(bool(self._current))

    def _is_binary(self, name: str) -> bool:
        for item in self._state.get("conflicts") or []:
            if item.get("path") == name:
                return bool(item.get("binary"))
        return False

    def _refresh_highlights(self) -> None:
        """按冲突标记给段落上色，让「要删哪一块」一眼可见。"""
        if self.editor.isReadOnly():
            self.editor.setExtraSelections([])
            return
        selections: list[QTextEdit.ExtraSelection] = []
        block = self.editor.document().begin()
        state = ""
        scanned = 0
        while block.isValid() and scanned < 4000:
            text = block.text()
            fmt = None
            if text.startswith("<<<<<<<"):
                state = "ours"
                fmt = self._fmt_marker
            elif text.startswith("=======") and state == "ours":
                state = "theirs"
                fmt = self._fmt_marker
            elif text.startswith(">>>>>>>") and state == "theirs":
                state = ""
                fmt = self._fmt_marker
            elif text.startswith("|||||||") and state == "ours":
                fmt = self._fmt_marker
            elif state == "ours":
                fmt = self._fmt_ours
            elif state == "theirs":
                fmt = self._fmt_theirs
            if fmt is not None:
                sel = QTextEdit.ExtraSelection()
                cursor = QTextCursor(block)
                # 必须选中整行：零宽光标不会渲染背景色
                cursor.select(QTextCursor.LineUnderCursor)
                sel.cursor = cursor
                sel.format = fmt
                selections.append(sel)
            block = block.next()
            scanned += 1
        self.editor.setExtraSelections(selections)

    def _on_file_selected(self, row: int) -> None:
        if self._loading or row < 0:
            return
        item = self.file_list.item(row)
        if item is None:
            return
        self._load_file(str(item.data(Qt.UserRole) or ""))

    def _load_file(self, name: str) -> None:
        if not name:
            return
        self._current = name
        try:
            data = GitEngine.conflict_text(self.path, name)
        except Exception as exc:  # noqa: BLE001
            show_error(self, "读取冲突文件失败", str(exc))
            return
        self.editor.setPlainText(str(data.get("current") or ""))
        mark = "已解决" if name in self._resolved else "未解决"
        size = len(str(data.get("current") or ""))
        self.file_label.setText(f"{name}　·　{mark}　·　{size} 字符")
        if data.get("binary"):
            self.hint.setText(
                "这是二进制文件，无法在编辑器里手动合并。请用「使用我的版本」或「使用对方版本」二选一。"
            )
            self.editor.setReadOnly(True)
        else:
            self.hint.setText(
                "编辑后点「保存并标记已解决」；冲突标记（<<<<<<< / ======= / >>>>>>>）需要全部删除。"
            )
            self.editor.setReadOnly(False)
        self._refresh_highlights()
        self._sync_status()

    def _load_side(self, side: str) -> None:
        if not self._current:
            return
        try:
            data = GitEngine.conflict_text(self.path, self._current)
        except Exception as exc:  # noqa: BLE001
            show_error(self, "读取冲突内容失败", str(exc))
            return
        text = str(data.get(side) or "")
        if not text and side == "theirs":
            self.hint.setText("该文件在对方分支里不存在（对方删除或只在我方新增）。")
        self.editor.setPlainText(text)
        self.editor.setReadOnly(False)
        self._refresh_highlights()
        # 立即标记，省一步点击
        self._save_current()

    def _save_current(self) -> None:
        name = self._current
        if not name:
            return
        content = self.editor.toPlainText()
        leftovers = [
            line for line in content.splitlines()[:4000] if line.startswith(MARKERS)
        ]
        if leftovers and not self._is_binary(name):
            if not confirm(
                self,
                "仍有冲突标记",
                f"「{name}」里还有 {len(leftovers)} 行冲突标记（如 {leftovers[0][:20]}…）。\n"
                "带标记的内容会被提交进仓库，确定要标记为已解决吗？",
                danger=True,
            ):
                return
        try:
            GitEngine.resolve_conflict(self.path, name, content)
        except (GitError, OSError) as exc:
            show_error(self, "保存失败", str(exc))
            return
        self._resolved.add(name)
        row = self.file_list.currentRow()
        item = self.file_list.item(row) if row >= 0 else None
        if item is not None and str(item.data(Qt.UserRole) or "") == name:
            item.setText("✓  " + name)
            item.setForeground(QColor("#1a7f37"))
        self.file_label.setText(f"{name}　·　已解决")
        self._sync_status()
        self._select_next_pending()

    def _select_next_pending(self) -> None:
        for row in range(self.file_list.count()):
            name = str(self.file_list.item(row).data(Qt.UserRole) or "")
            if name not in self._resolved:
                self.file_list.setCurrentRow(row)
                return

    # ------------------------------------------------------------------ #
    # 收尾
    # ------------------------------------------------------------------ #

    def _pending_names(self) -> list[str]:
        return [n for n in self._all_files if n not in self._resolved]

    def _finish(self) -> None:
        pending = self._pending_names()
        if pending:
            self.notify_pending(pending)
            return
        default = str(self._state.get("message") or "")
        source = str(self._state.get("source") or "").replace("refs/heads/", "").replace(
            "refs/remotes/", ""
        )
        branch = self._state.get("branch") or ""
        if not default:
            default = f"合并分支 {source} 到 {branch}".strip()
        message, ok = QInputDialog.getMultiLineText(self, "完成合并", "合并提交说明：", default)
        if not ok:
            return
        try:
            result = GitEngine.finish_merge(
                self.path, message.strip() or default, author=self.author, email=self.email
            )
        except (GitError, OSError) as exc:
            show_error(self, "完成合并失败", str(exc))
            return
        log.info("冲突已全部解决，合并提交 %s", str(result.get("commit", ""))[:8])
        self.finished_with_result.emit("merged")
        self.accept()

    def notify_pending(self, pending: list[str]) -> None:
        preview = "\n".join(f"  · {n}" for n in pending[:8])
        more = f"\n  … 另有 {len(pending) - 8} 个" if len(pending) > 8 else ""
        show_error(
            self,
            "还有未解决的冲突",
            f"以下 {len(pending)} 个文件还没标记为已解决：\n{preview}{more}\n\n"
            "逐个编辑并点「保存并标记已解决」后才能完成合并。",
        )

    def _abort(self) -> None:
        if not confirm(
            self,
            "中止合并",
            "将放弃本次合并，工作区与索引恢复到合并前的状态（已做的冲突编辑会丢失）。\n\n确定中止吗？",
            danger=True,
        ):
            return
        try:
            GitEngine.abort_merge(self.path)
        except (GitError, OSError) as exc:
            show_error(self, "中止合并失败", str(exc))
            return
        self.finished_with_result.emit("aborted")
        self.reject()

    def closeEvent(self, event) -> None:  # noqa: N802
        """直接关窗时保留合并现场，下次可继续。"""
        if self._state.get("active") and self._pending_names():
            if not confirm(
                self,
                "合并尚未完成",
                "关闭窗口不会丢失合并现场：已保存的解决结果会保留，"
                "下次点「合并到当前分支」可以继续处理。\n\n确定关闭吗？",
            ):
                event.ignore()
                return
        self.finished_with_result.emit("closed")
        super().closeEvent(event)
