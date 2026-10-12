"""后台任务封装：把耗时操作放到线程中执行，保持界面响应。"""

from __future__ import annotations

import inspect
import threading
import traceback
from collections import deque
from typing import Any, Callable

from PySide6.QtCore import QObject, QThread, Signal

from ..core.gitengine import CancelToken, GitError, OperationCancelled
from ..core.logger import get_logger

log = get_logger("task")

_active: set["Task"] = set()
_lock = threading.Lock()


class Task(QThread):
    """在工作线程中执行一个可调用对象。

    若目标函数声明了 progress 参数，会自动注入一个可安全跨线程发送的信号回调；
    声明了 detail 参数则注入结构化详情回调（阶段 / 字节 / 对象 / 速度）；
    声明了 cancel 参数则注入取消令牌，界面可随时调用 cancel() 中止任务。
    """

    succeeded = Signal(object)
    failed = Signal(str, str)
    progressed = Signal(str)
    detailed = Signal(object)
    cancelled = Signal()

    def __init__(self, fn: Callable[..., Any], *args, label: str = "", **kwargs) -> None:
        super().__init__()
        self._fn = fn
        self._args = args
        self._kwargs = kwargs
        self.label = label or getattr(fn, "__name__", "task")
        self.counted = True
        self.cancel_message = ""   # 取消原因，供界面在「已停止」提示里复用
        self._token = CancelToken()
        self._accepts_progress = self._wants(fn, "progress")
        self._accepts_detail = self._wants(fn, "detail")
        self._accepts_cancel = self._wants(fn, "cancel")
        self._result_cancelled = False

    @staticmethod
    def _wants(fn: Callable, name: str) -> bool:
        try:
            params = inspect.signature(fn).parameters
        except (TypeError, ValueError):
            return False
        return name in params

    # ---------------- 取消 ---------------- #

    @property
    def cancel_token(self) -> CancelToken:
        return self._token

    @property
    def was_cancelled(self) -> bool:
        return self._token.cancelled

    def cancel(self) -> None:
        """请求中止任务。可安全地从界面线程调用。"""
        if not self.isRunning():
            return
        log.info("请求中止任务: %s", self.label)
        self._token.cancel()
        self.requestInterruption()

    # ---------------- 执行 ---------------- #

    def run(self) -> None:  # noqa: D102
        log.debug("任务开始: %s", self.label)
        try:
            kwargs = dict(self._kwargs)
            if self._accepts_progress and "progress" not in kwargs:
                kwargs["progress"] = self._emit_progress
            if self._accepts_detail and "detail" not in kwargs:
                kwargs["detail"] = self._emit_detail
            if self._accepts_cancel and "cancel" not in kwargs:
                kwargs["cancel"] = self._token
            result = self._fn(*self._args, **kwargs)
        except GitError as exc:
            if getattr(exc, "cancelled", False) or self._token.cancelled:
                self._mark_cancelled(exc.message)
            else:
                log.error("任务失败[%s]: %s", self.label, exc.message)
                if exc.detail:
                    log.debug("任务失败详情[%s]: %s", self.label, exc.detail)
                self.failed.emit(exc.message, exc.detail)
        except Exception as exc:  # noqa: BLE001
            if self._token.cancelled:
                self._mark_cancelled(f"{self.label} 已取消。")
            else:
                detail = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
                log.error("任务异常[%s]: %s", self.label, exc)
                log.debug("任务异常堆栈[%s]:\n%s", self.label, detail)
                self.failed.emit(f"发生未预期的错误：{exc}", detail)
        else:
            if self._token.cancelled:
                self._mark_cancelled(f"{self.label} 已取消。")
            else:
                log.debug("任务完成: %s", self.label)
                self.succeeded.emit(result)

    def _mark_cancelled(self, message: str) -> None:
        self._result_cancelled = True
        self.cancel_message = message
        log.info("任务已取消[%s]: %s", self.label, message)
        self.cancelled.emit()

    def _emit_progress(self, message: str) -> None:
        if self._token.cancelled:
            raise OperationCancelled()
        try:
            self.progressed.emit(str(message))
        except RuntimeError:
            pass

    def _emit_detail(self, payload) -> None:
        """把结构化传输详情投递到界面线程。"""
        if self._token.cancelled:
            return
        try:
            self.detailed.emit(payload)
        except RuntimeError:
            pass


class TaskCenter(QObject):
    """统一管理后台任务的生命周期与结果分发。

    注意：QThread 对象必须在底层线程真正结束后仍保持引用，否则会被
    Python 垃圾回收，导致 Qt 直接中止进程。因此已完成的任务会先移入
    保留队列，稍后才释放。
    """

    busy_changed = Signal(bool, str)
    progressed = Signal(str)

    _GRAVEYARD_SIZE = 64

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._running = 0
        self._graveyard: deque[Task] = deque(maxlen=self._GRAVEYARD_SIZE)

    @property
    def busy(self) -> bool:
        return self._running > 0

    def run(
        self,
        fn: Callable[..., Any],
        *args,
        on_success: Callable[[Any], None] | None = None,
        on_error: Callable[[str, str], None] | None = None,
        on_progress: Callable[[str], None] | None = None,
        on_detail: Callable[[dict], None] | None = None,
        on_cancelled: Callable[[], None] | None = None,
        label: str = "",
        silent: bool = False,
        **kwargs,
    ) -> Task:
        """提交一个后台任务。

        silent=True 的任务（如周期性变更检测）不参与全局忙状态展示，
        避免状态栏被高频任务刷屏。
        """
        task = Task(fn, *args, label=label, **kwargs)
        task.counted = not silent
        if on_success:
            task.succeeded.connect(on_success)
        if on_error:
            task.failed.connect(on_error)
        if on_progress:
            task.progressed.connect(on_progress)
        if on_detail:
            task.detailed.connect(on_detail)
        if on_cancelled:
            task.cancelled.connect(on_cancelled)
        task.progressed.connect(self.progressed)
        task.finished.connect(lambda t=task: self._reap(t))
        with _lock:
            _active.add(task)
        if task.counted:
            self._running += 1
            self.busy_changed.emit(True, task.label)
        task.start()
        return task

    def _reap(self, task: Task) -> None:
        """线程结束后延迟释放引用，避免 Qt 在析构时中止进程。"""
        with _lock:
            _active.discard(task)
        if getattr(task, "counted", True):
            self._running = max(0, self._running - 1)
            if self._running == 0:
                self.busy_changed.emit(False, "")
        self._graveyard.append(task)


def shutdown_all(timeout_ms: int = 8000) -> None:
    """退出前尽量收敛仍在运行的任务：先请求取消，再限时等待。"""
    with _lock:
        tasks = list(_active)
    if not tasks:
        return
    for task in tasks:
        try:
            task.cancel()
        except RuntimeError:
            pass
    per_task = max(200, timeout_ms // len(tasks))
    for task in tasks:
        try:
            if task.isRunning():
                task.wait(per_task)
        except RuntimeError:
            pass
