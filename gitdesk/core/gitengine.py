"""基于 dulwich 的纯 Python Git 引擎封装。

不依赖用户本机安装 Git，所有操作通过 dulwich 实现。
所有方法均以「仓库路径」为入口，内部按需打开仓库，避免跨线程共享状态。
"""

from __future__ import annotations

import io
import os
import re
import shutil
import socket
import ssl
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Callable, Iterable, Sequence

from dulwich import porcelain
from dulwich.errors import (
    GitProtocolError,
    NotGitRepository,
    SendPackError,
    UnexpectedCommandError,
    WorkingTreeModifiedError,
)
from dulwich.index import IndexEntry
from dulwich.objects import Commit, Tag
from dulwich.repo import Repo

from .logger import get_logger

log = get_logger("git")

ProgressCb = Callable[[str], None]


class GitError(Exception):
    """对用户可见的 Git 操作错误。"""

    def __init__(self, message: str, detail: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail

    def __str__(self) -> str:  # pragma: no cover
        return f"{self.message}\n{self.detail}" if self.detail else self.message


CANCEL_MESSAGE = "克隆已取消"


class CancelledError(GitError):
    """操作被用户主动取消。"""

    def __init__(self, message: str = CANCEL_MESSAGE, detail: str = "") -> None:
        super().__init__(message, detail)
        self.cancelled = True


class CancelToken:
    """线程安全的取消信号，可由界面线程发出、工作线程读取。"""

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def wait(self, timeout: float) -> bool:
        """等待取消信号，返回 True 表示等待期间被取消。

        用于让「重试退避」这类等待也能被立刻打断，而不是干等满整段时间。
        """
        return self._event.wait(timeout)

    def raise_if_cancelled(self) -> None:
        if self._event.is_set():
            raise _Cancelled()


class _Cancelled(Exception):
    """内部中断信号：从传输层回调中抛出以打断阻塞中的克隆。"""


# 供界面层（如进度回调）复用的公开别名
OperationCancelled = _Cancelled


def _describe_bytes(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024:
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TB"


# --------------------------------------------------------------------------- #
# 传输详情（进度条下方的「详情」数据源）
# --------------------------------------------------------------------------- #

#: 传输类型 → 中文名
TRANSFER_KINDS = {"clone": "克隆", "fetch": "获取", "pull": "拉取", "push": "推送"}

#: 服务端进度文本 → 阶段名（按顺序匹配，命中即止）
_PHASE_RULES: tuple[tuple[str, str], ...] = (
    ("cloning into", "初始化仓库"),
    ("counting objects", "统计对象"),
    ("enumerating objects", "枚举对象"),
    ("compressing objects", "压缩对象"),
    ("receiving objects", "接收对象"),
    ("resolving deltas", "解析增量"),
    ("writing objects", "发送对象"),
    ("updating references", "更新引用"),
    ("updating refs", "更新引用"),
    ("checking out", "检出文件"),
    ("remote:", "远程消息"),
    ("everything up-to-date", "无需传输"),
    ("already up-to-date", "无需传输"),
)

_PROGRESS_RE = re.compile(r"\((\d+)\s*/\s*(\d+)\)")
_PERCENT_RE = re.compile(r"(\d{1,3})\s*%")
_SPEED_RE = re.compile(r"\|\s*([\d.]+\s*[KMG]i?B/s)")
_SIZE_RE = re.compile(r"([\d.]+\s*[KMG]i?B)")


def parse_progress_line(text: str) -> dict:
    """从服务端进度文本中解析阶段 / 对象计数 / 百分比（纯函数，便于测试）。

    例：``Receiving objects:  45% (9/20), 1.2 MiB | 300 KiB/s``
    → ``{"phase": "接收对象", "objects": 9, "total": 20, "percent": 45.0,
         "transferred": "1.2 MiB", "speed": "300 KiB/s"}``
    """
    raw = (text or "").strip()
    low = raw.lower().replace("\r", " ").replace("\n", " ")
    result: dict = {"phase": "", "objects": 0, "total": 0, "percent": None,
                    "transferred": "", "speed": ""}
    for keyword, phase in _PHASE_RULES:
        if keyword in low:
            result["phase"] = phase
            break
    match = _PROGRESS_RE.search(raw)
    if match:
        result["objects"] = int(match.group(1))
        result["total"] = int(match.group(2))
    match = _PERCENT_RE.search(raw)
    if match:
        try:
            result["percent"] = float(match.group(1))
        except ValueError:  # pragma: no cover - 正则已保证是数字
            result["percent"] = None
    match = _SPEED_RE.search(raw)
    if match:
        result["speed"] = match.group(1).strip()
    if not result["transferred"]:
        match = _SIZE_RE.search(raw)
        if match:
            result["transferred"] = match.group(1).strip()
    return result


@dataclass
class TransferStats:
    """一次网络传输的可展示统计。"""

    kind: str = ""
    label: str = ""
    phase: str = "准备中"
    message: str = ""
    bytes_in: int = 0
    bytes_out: int = 0
    objects_done: int = 0
    objects_total: int = 0
    percent: float | None = None
    speed: str = ""
    retries: int = 0
    attempts: int = 1
    started_at: float = field(default_factory=time.time)
    finished_at: float = 0.0
    ok: bool | None = None

    @property
    def transferred(self) -> int:
        return self.bytes_in + self.bytes_out

    @property
    def elapsed(self) -> float:
        end = self.finished_at or time.time()
        return max(0.0, end - self.started_at)

    def summary(self) -> str:
        parts: list[str] = []
        if self.bytes_in:
            parts.append(f"已接收 {_describe_bytes(self.bytes_in)}")
        if self.bytes_out:
            parts.append(f"已发送 {_describe_bytes(self.bytes_out)}")
        if self.objects_total:
            parts.append(f"对象 {self.objects_done}/{self.objects_total}")
        elif self.objects_done:
            parts.append(f"对象 {self.objects_done}")
        if self.retries:
            parts.append(f"重试 {self.retries} 次")
        return " · ".join(parts)

    def as_dict(self) -> dict:
        """供界面直接渲染的展开数据。"""
        return {
            "kind": self.kind,
            "label": self.label or TRANSFER_KINDS.get(self.kind, "传输"),
            "phase": self.phase,
            "message": self.message,
            "bytes_in": self.bytes_in,
            "bytes_in_text": _describe_bytes(self.bytes_in) if self.bytes_in else "—",
            "bytes_out": self.bytes_out,
            "bytes_out_text": _describe_bytes(self.bytes_out) if self.bytes_out else "—",
            "transferred_text": _describe_bytes(self.transferred) if self.transferred else "—",
            "objects_done": self.objects_done,
            "objects_total": self.objects_total,
            "objects_text": (
                f"{self.objects_done}/{self.objects_total}"
                if self.objects_total
                else (str(self.objects_done) if self.objects_done else "—")
            ),
            "percent": self.percent,
            "speed": self.speed or "—",
            "retries": self.retries,
            "elapsed": round(self.elapsed, 1),
            "elapsed_text": f"{self.elapsed:.1f} 秒",
            "speed_avg": _speed_text(self.transferred, self.elapsed),
            "summary": self.summary(),
            "ok": self.ok,
        }


def _speed_text(total_bytes: int, seconds: float) -> str:
    if total_bytes <= 0 or seconds <= 0.2:
        return "—"
    return f"{_describe_bytes(int(total_bytes / seconds))}/s"


class TransferMonitor(io.RawIOBase):
    """传输过程的「进度 + 取消 + 详情」监视器。

    同时挂到 dulwich 的两个回调上：

    * ``errstream.write``：服务端下发的可读进度（对象计数、压缩率等）；
    * ``report_activity``：传输层每次收发包都会触发，用于在「已发出请求、等待
      服务端响应」这段没有任何应用层消息的时间里也能及时响应取消，
      并累计已收/已发字节数。

    取消时抛出内部中断异常，dulwich 不会吞掉它，因此可以打断阻塞中的克隆。
    另外会把结构化统计通过 ``detail`` 回调抛出，供界面「进度条 + 详情」展示。
    """

    REPORT_INTERVAL = 0.5
    LINE_INTERVAL = 0.1

    def __init__(
        self,
        kind: str = "",
        progress: ProgressCb | None = None,
        detail: Callable[[dict], None] | None = None,
        cancel: CancelToken | None = None,
    ) -> None:
        super().__init__()
        self.kind = kind
        self._progress = progress
        self._detail = detail
        self._cancel = cancel
        self.stats = TransferStats(kind=kind, label=TRANSFER_KINDS.get(kind, kind))
        self._last_line = ""
        self._last_text = ""
        self._line_at = 0.0
        self._report_at = 0.0

    # ---- errstream 接口 ----

    def writable(self) -> bool:  # noqa: D102
        return True

    def write(self, data) -> int:  # type: ignore[override]  # noqa: D102
        self._check_cancel()
        raw = data if isinstance(data, (bytes, bytearray)) else str(data).encode("utf-8")
        text = raw.decode("utf-8", "replace").strip().replace("\r", "").replace("\n", " ")
        now = time.time()
        if text and text != self._last_line and now - self._line_at >= self.LINE_INTERVAL:
            self._last_line = text
            self._line_at = now
            self._absorb(text)
            self._emit(text)
        return len(raw)

    # ---- report_activity 接口 ----

    def __call__(self, msg_code: int, text: str) -> None:
        self._check_cancel()
        if isinstance(msg_code, int):
            if text == "read":
                self.stats.bytes_in += msg_code
            elif text == "write":
                self.stats.bytes_out += msg_code
        now = time.time()
        if now - self._report_at < self.REPORT_INTERVAL:
            return
        self._report_at = now
        if not self.stats.transferred:
            return
        # 只有在没有更详细的服务端进度时才退化为字节计数，避免两路消息互相刷屏
        if now - self._line_at >= 1.0:
            direction = "接收" if self.stats.bytes_in >= self.stats.bytes_out else "发送"
            counted = self.stats.bytes_in if direction == "接收" else self.stats.bytes_out
            self._emit(f"正在{direction}数据… 已传输 {_describe_bytes(counted)}")
        else:
            self._emit_detail()

    # ---- 对外 ----

    def mark_retry(self, attempt: int, exc: BaseException, wait: float) -> None:
        """记录一次失败重试（供界面在详情里展示）。"""
        self.stats.retries += 1
        self.stats.attempts = attempt + 1
        self.stats.phase = "重试中"
        text = (
            f"网络中断（{_short_reason(exc)}），{wait:.1f} 秒后自动重试"
            f"（第 {attempt + 1}/{NETWORK_ATTEMPTS} 次）…"
        )
        self._emit(text, force=True)

    def set_phase(self, phase: str, message: str = "") -> None:
        self.stats.phase = phase
        if message:
            self._emit(message, force=True)
        else:
            self._emit_detail()

    def finish(self, ok: bool = True, message: str = "") -> None:
        self.stats.finished_at = time.time()
        self.stats.ok = ok
        if ok:
            self.stats.phase = "完成"
        elif not message:
            self.stats.phase = "已中止"
        if message:
            self.stats.message = message
            self._emit(message, force=True)
        else:
            self._emit_detail()

    # ---- 内部 ----

    def _check_cancel(self) -> None:
        if self._cancel is not None and self._cancel.cancelled:
            raise _Cancelled()

    def _absorb(self, text: str) -> None:
        """把服务端进度文本解析进统计。"""
        parsed = parse_progress_line(text)
        if parsed["phase"]:
            self.stats.phase = parsed["phase"]
        if parsed["objects"]:
            self.stats.objects_done = parsed["objects"]
        if parsed["total"]:
            self.stats.objects_total = parsed["total"]
        if parsed["percent"] is not None and parsed["phase"] in ("接收对象", "发送对象", "解析增量"):
            self.stats.percent = parsed["percent"]
        if parsed["speed"]:
            self.stats.speed = parsed["speed"]
        self.stats.message = text

    def _emit(self, text: str, force: bool = False) -> None:
        self.stats.message = text
        if self._progress is not None and (force or text != self._last_text):
            self._last_text = text
            try:
                self._progress(text)
            except _Cancelled:
                raise
            except Exception:  # noqa: BLE001 - 进度回调异常不应影响传输
                log.debug("进度回调异常被忽略", exc_info=True)
        self._emit_detail()

    def _emit_detail(self) -> None:
        if self._detail is None:
            return
        try:
            self._detail(self.stats.as_dict())
        except _Cancelled:
            raise
        except Exception:  # noqa: BLE001
            log.debug("详情回调异常被忽略", exc_info=True)


# 兼容旧调用点
_CloneMonitor = TransferMonitor


# --------------------------------------------------------------------------- #
# 网络重试
# --------------------------------------------------------------------------- #

#: 网络类操作的最大尝试次数（含首次）
NETWORK_ATTEMPTS = 3
_RETRY_BACKOFF = 1.2

_TRANSIENT_HINTS = (
    "connection aborted",
    "connection reset",
    "connectionreset",
    "connection refused",
    "connectionrefused",
    "远程主机强迫",
    "强迫关闭",
    "broken pipe",
    "eof occurred",
    "unexpected end of file",
    "remote end closed",
    "temporarily unavailable",
    "10053",
    "10054",
    "10060",
    "11001",
)

_TRANSIENT_TYPES = (
    ConnectionError,
    socket.timeout,
    TimeoutError,
    BrokenPipeError,
    ssl.SSLEOFError,
)


def _iter_error_chain(exc: BaseException, depth: int = 6):
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and depth > 0 and id(current) not in seen:
        seen.add(id(current))
        yield current
        nxt = current.__cause__ or current.__context__
        if isinstance(current, GitProtocolError) and not nxt:
            break
        current = nxt
        depth -= 1


def is_transient_error(exc: BaseException) -> bool:
    """判断该错误是否属于「重试可能成功」的网络类故障。

    典型场景：``GitProtocolError: ('Connection aborted.', ConnectionResetError(10054))``
    —— 远程主机在传输中途关闭了连接（抖动、代理超时、防火墙拦截）。
    """
    for err in _iter_error_chain(exc):
        if isinstance(err, _TRANSIENT_TYPES):
            return True
    text = _flatten_error_text(exc)
    return any(hint in text for hint in _TRANSIENT_HINTS)


def _flatten_error_text(exc: BaseException) -> str:
    chunks: list[str] = []
    for err in _iter_error_chain(exc):
        chunks.append(str(err))
        args = getattr(err, "args", ()) or ()
        for arg in args:
            if isinstance(arg, BaseException):
                chunks.append(str(arg))
            elif isinstance(arg, (tuple, list)):
                chunks.append(" ".join(str(x) for x in arg))
            else:
                chunks.append(str(arg))
    return " ".join(chunks).lower()


def _short_reason(exc: BaseException) -> str:
    text = _flatten_error_text(exc)
    if "10054" in text or "强迫关闭" in text or "connection reset" in text:
        return "远程主机关闭了连接"
    if "10053" in text:
        return "本机软件中止了连接"
    if "timed out" in text or "timeout" in text:
        return "连接超时"
    if "10060" in text or "11001" in text:
        return "网络不可达"
    return "网络异常"


def _sleep_or_cancel(seconds: float, cancel: CancelToken | None) -> bool:
    """等待若干秒；若等待期间被取消则立刻返回 True（不再等满）。"""
    if cancel is None:
        time.sleep(seconds)
        return False
    try:
        return bool(cancel.wait(seconds))
    except Exception:  # noqa: BLE001 - 自定义令牌没有 wait 时退化为轮询
        deadline = time.time() + seconds
        while time.time() < deadline:
            if cancel.cancelled:
                return True
            time.sleep(0.05)
        return cancel.cancelled


def _retry_network(
    fn: Callable[[], object],
    *,
    monitor: TransferMonitor | None = None,
    label: str = "",
    attempts: int = NETWORK_ATTEMPTS,
    cancel: CancelToken | None = None,
) -> object:
    """执行网络操作，失败且属于可重试错误时按退避策略重试。

    仅在「连接被中断 / 超时」这类瞬时故障上重试；认证失败、推送被拒绝等
    确定性错误会立即抛出，避免无意义地反复打扰服务端。

    传入 ``cancel`` 后，无论正在传输还是正处于重试等待期，都能被立刻打断
    （抛出内部中断异常，由调用方转成 CancelledError）。
    """
    attempts = max(1, int(attempts))
    last: BaseException | None = None
    for attempt in range(1, attempts + 1):
        if cancel is not None and cancel.cancelled:
            raise _Cancelled()
        try:
            return fn()
        except _Cancelled:
            raise
        except Exception as exc:  # noqa: BLE001
            last = exc
            if attempt >= attempts or not is_transient_error(exc):
                raise
            wait = _RETRY_BACKOFF * attempt
            log.warning(
                "%s 第 %d 次尝试失败（%s），%.1f 秒后重试",
                label or "网络操作",
                attempt,
                _short_reason(exc),
                wait,
            )
            if monitor is not None:
                monitor.mark_retry(attempt, exc, wait)
            if _sleep_or_cancel(wait, cancel):
                log.info("%s 已被用户中止，不再重试", label or "网络操作")
                raise _Cancelled()
    assert last is not None  # pragma: no cover
    raise last


def _monitor_for(
    kind: str,
    progress: ProgressCb | None,
    detail: Callable[[dict], None] | None,
    cancel: CancelToken | None = None,
) -> TransferMonitor | None:
    if progress is None and detail is None and cancel is None:
        return None
    return TransferMonitor(kind, progress, detail, cancel)


def _monitor_kwargs(monitor: TransferMonitor | None) -> dict:
    """把监视器包装成 dulwich 的 ``errstream`` + ``report_activity`` 关键字参数。

    监视器为空时两个参数都不传，避免 windowed 打包下默认 ``sys.stderr`` 为 None
    导致写入失败。
    """
    if monitor is None:
        return {}
    return {"errstream": monitor, "report_activity": monitor}


def _finish_monitor(monitor: TransferMonitor | None, ok: bool, message: str = "") -> None:
    if monitor is not None:
        try:
            monitor.finish(ok, message)
        except Exception:  # noqa: BLE001 - 收尾回调异常不影响主流程
            log.debug("传输收尾回调异常", exc_info=True)


def _retries(monitor: TransferMonitor | None) -> int:
    return monitor.stats.retries if monitor is not None else 0


def _cleanup_dir(path: str) -> None:
    """删除克隆失败/取消后残留的目录。"""
    try:
        if os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)
    except Exception:  # noqa: BLE001
        log.debug("清理目录失败: %s", path, exc_info=True)


@dataclass
class Credential:
    """远程仓库凭据。"""

    username: str = ""
    token: str = ""

    @property
    def empty(self) -> bool:
        return not self.token

    def basic_auth(self, platform: str = "github") -> tuple[str | None, str | None]:
        """返回 (username, password)；无令牌时返回 (None, None)。"""
        if not self.token:
            return None, None
        default_user = "oauth2" if platform == "gitee" else "x-access-token"
        return (self.username or default_user), self.token


@dataclass
class SyncState:
    ahead: int = 0
    behind: int = 0
    has_upstream: bool = False
    upstream: str = ""

    @property
    def diverged(self) -> bool:
        return self.ahead > 0 and self.behind > 0


# --------------------------------------------------------------------------- #
# 工具函数
# --------------------------------------------------------------------------- #

_AUTH_ERROR_HINTS = (
    "authentication failed",
    "authentication",
    "401",
    "403",
    "invalid credentials",
    "not authorized",
    "permission denied",
)


def humanize_error(exc: BaseException, retried: int = 0) -> tuple[str, str]:
    """把底层异常转换为 (用户提示, 技术细节)。

    ``retried`` 表示在抛出前已经自动重试过的次数，用于提示文案。
    """
    detail = f"{type(exc).__name__}: {exc}"
    text = str(exc)
    low = text.lower()

    if isinstance(exc, NotGitRepository):
        return "该目录不是一个有效的 Git 仓库。", detail
    if isinstance(exc, WorkingTreeModifiedError):
        return (
            "工作区存在未提交的更改，该操作会覆盖这些改动，已被阻止以保护本地代码。",
            detail,
        )
    if isinstance(exc, SendPackError):
        text_low = text.lower()
        if "non-fast-forward" in text_low or "reject" in text_low or "fetch first" in text_low:
            return (
                "推送被拒绝：远程包含本地没有的提交。请先拉取远程更新后再推送。",
                detail,
            )
        if is_transient_error(exc):
            return _connection_broken_message(exc, retried), detail
        return "远程拒绝了本次推送，请检查分支权限或先同步远程改动。", detail
    if isinstance(exc, UnexpectedCommandError):
        return "服务端不支持该协议版本，请检查仓库地址或代理设置。", detail
    if isinstance(exc, GitProtocolError):
        if any(h in low for h in _AUTH_ERROR_HINTS):
            return "身份验证失败。请检查访问令牌是否有效、是否具备相应权限。", detail
        if is_transient_error(exc):
            return _connection_broken_message(exc, retried), detail
        return f"Git 协议错误：{text}", detail
    if isinstance(exc, ssl.SSLError):
        return "TLS/SSL 握手失败，可能是网络代理或证书问题。", detail
    if isinstance(exc, socket.timeout) or "timed out" in low:
        return "网络超时。请检查网络连接或稍后重试。", detail
    if is_transient_error(exc):
        return _connection_broken_message(exc, retried), detail
    if isinstance(exc, ConnectionError):
        return "无法连接到远程服务器，请检查网络或代理设置。", detail
    if any(h in low for h in _AUTH_ERROR_HINTS):
        return "身份验证失败，请检查访问令牌。", detail
    if "repository not found" in low or "not found" in low and "repo" in low:
        return "仓库不存在或无权访问。", detail
    return f"操作失败：{text}", detail


def _connection_broken_message(exc: BaseException, retried: int = 0) -> str:
    """针对「连接被远程主机中断」的统一提示。"""
    reason = _short_reason(exc)
    tried = f"程序已自动重试 {retried} 次仍未成功。" if retried else ""
    return (
        f"网络连接被中断：{reason}。\n"
        "常见原因：网络不稳定、公司/校园网或代理拦截、仓库体积较大导致传输超时。\n"
        f"{tried}"
        "可尝试：\n"
        "  1) 稍等片刻后重试（这类抖动多为瞬时故障）；\n"
        "  2) 关闭或更换网络代理（VPN / 加速器）后重试；\n"
        "  3) 换用其它网络（如手机热点）再试一次。"
    )



def _to_str(value) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return str(value)


def _norm(p: str) -> str:
    return _to_str(p).replace("\\", "/")


def _drop_index_entry(index, key: bytes) -> None:
    """从索引中移除条目（Index 未实现 __contains__，直接删除更稳妥）。"""
    try:
        del index[key]
    except Exception:
        pass


@contextmanager
def _repo(path: str):
    """打开仓库的统一入口：把「目录不是 Git 仓库」转换为友好错误。"""
    try:
        with porcelain.open_repo_closing(path) as repo:
            yield repo
    except NotGitRepository as exc:
        raise GitError(
            "该目录不是有效的 Git 仓库。\n如果这是普通目录，可先在「本地仓库」中点击「初始化新仓库」。",
            str(exc),
        ) from exc


STAGED_LABELS = {"add": "新增", "modify": "修改", "delete": "删除", "rename": "重命名"}


def _normalize_staged(raw) -> dict[str, str]:
    """把 dulwich 的 staged 结构归一化为 {路径: 类型}。

    dulwich 1.2 返回 {'add': [...], 'delete': [...], 'modify': [...]}；
    老版本返回 {路径: 状态字符}。两种都兼容。
    """
    result: dict[str, str] = {}
    if not raw:
        return result
    if isinstance(raw, dict):
        keys = set(raw.keys())
        if keys & {"add", "delete", "modify", "rename"}:
            for kind in ("add", "modify", "delete", "rename"):
                for path in raw.get(kind) or []:
                    result[_norm(path)] = kind
            return result
        for key, value in raw.items():
            result[_norm(key)] = _to_str(value) or "modify"
        return result
    for path in raw:
        result[_norm(path)] = "modify"
    return result


def _normalize_paths(raw) -> list[str]:
    """兼容 list / dict 两种返回结构，统一成路径列表。"""
    if not raw:
        return []
    if isinstance(raw, dict):
        out: list[str] = []
        for value in raw.values():
            if isinstance(value, (list, tuple, set)):
                out.extend(_norm(v) for v in value)
            else:
                out.append(_norm(value))
        return out
    return [_norm(p) for p in raw]


def open_repo(path: str) -> Repo:
    try:
        return Repo(path)
    except NotGitRepository as exc:
        raise GitError("该目录不是 Git 仓库。", str(exc)) from exc
    except Exception as exc:
        msg, detail = humanize_error(exc)
        raise GitError(msg, detail) from exc


def is_git_repo(path: str) -> bool:
    if not path or not os.path.isdir(path):
        return False
    try:
        Repo(path).close()
        return True
    except Exception:
        return False


# --------------------------------------------------------------------------- #
# 引擎
# --------------------------------------------------------------------------- #


class GitEngine:
    """无状态 Git 操作集合。"""

    # ---------------- 基础信息 ---------------- #

    @staticmethod
    def is_git_repo(path: str) -> bool:
        return is_git_repo(path)

    @staticmethod
    def repo_info(path: str, fetch_stats: bool = True) -> dict:
        """汇总仓库概览信息。"""
        with _repo(path) as repo:
            info: dict = {
                "path": os.path.abspath(path),
                "name": os.path.basename(os.path.abspath(path)),
                "valid": True,
            }
            try:
                info["branch"] = _to_str(porcelain.active_branch(repo))
            except Exception:
                info["branch"] = ""
            try:
                head = repo.head()
                info["head"] = head.decode() if isinstance(head, bytes) else str(head)
                if info["head"]:
                    commit = repo[head.encode() if isinstance(head, str) else head]
                    info["head_message"] = _commit_message(commit).splitlines()[0] if _commit_message(commit) else ""
                    info["head_author"] = _to_str(commit.author).rsplit(" <", 1)[0]
                    info["head_time"] = _fmt_ts(commit.commit_time)
            except Exception:
                info.setdefault("head", "")
                info.setdefault("head_message", "")
                info.setdefault("head_author", "")
                info.setdefault("head_time", "")
            info["remotes"] = GitEngine.remotes(path)
            info["state"] = GitEngine.status(path, untracked=False)
            if fetch_stats:
                try:
                    state = GitEngine.sync_state(path)
                    info["ahead"] = state.ahead
                    info["behind"] = state.behind
                    info["upstream"] = state.upstream
                except Exception:
                    info["ahead"] = info["behind"] = 0
                    info["upstream"] = ""
            return info

    @staticmethod
    def status(path: str, untracked: bool = True) -> dict:
        """工作区/暂存区状态。"""
        with _repo(path) as repo:
            try:
                st = porcelain.status(
                    repo, untracked_files="all" if untracked else "no"
                )
            except Exception as exc:
                msg, detail = humanize_error(exc)
                raise GitError(msg, detail) from exc

            staged = _normalize_staged(getattr(st, "staged", None))
            unstaged = _normalize_paths(getattr(st, "unstaged", None))
            untracked_list = _normalize_paths(getattr(st, "untracked", None))
            try:
                branch = _to_str(porcelain.active_branch(repo))
            except Exception:
                branch = ""
            return {
                "branch": branch,
                "staged": staged,
                "unstaged": sorted(unstaged),
                "untracked": sorted(untracked_list),
                "clean": not staged and not unstaged and not untracked_list,
                "dirty": bool(unstaged or untracked_list),
            }

    @staticmethod
    def sync_state(path: str) -> SyncState:
        """本地分支相对上游的领先/落后提交数（基于本地已知的远程引用）。"""
        with _repo(path) as repo:
            try:
                branch = porcelain.active_branch(repo)
            except Exception:
                return SyncState()
            if not branch:
                return SyncState()
            upstream = _upstream_ref(repo, branch)
            state = SyncState(upstream=_to_str(upstream) if upstream else "")
            if not upstream:
                return state
            state.has_upstream = True
            try:
                local_sha = repo.refs[b"refs/heads/" + branch]
                remote_sha = repo.refs[upstream]
            except KeyError:
                return state
            if local_sha == remote_sha:
                return state
            state.ahead = _count_commits(repo, local_sha, remote_sha)
            state.behind = _count_commits(repo, remote_sha, local_sha)
            return state

    @staticmethod
    def history(path: str, limit: int = 100, skip: int = 0) -> list[dict]:
        """提交历史（按时间倒序）。"""
        with _repo(path) as repo:
            try:
                head = repo.head()
            except Exception:
                return []
            if not head:
                return []
            out: list[dict] = []
            walker = repo.get_walker(include=[head], max_entries=limit + skip)
            for entry in walker:
                if skip > 0:
                    skip -= 1
                    continue
                c: Commit = entry.commit
                out.append(
                    {
                        "sha": c.id.decode(),
                        "short": c.id.decode()[:8],
                        "author": _to_str(c.author).rsplit(" <", 1)[0],
                        "email": _extract_email(c.author),
                        "date": _fmt_ts(c.commit_time),
                        "timestamp": c.commit_time,
                        "message": _commit_message(c),
                        "subject": _commit_message(c).splitlines()[0] if _commit_message(c) else "",
                        "parents": [p.decode() for p in c.parents],
                        "refs": _refs_at(repo, c.id),
                    }
                )
                if len(out) >= limit:
                    break
            return out

    @staticmethod
    def branches(path: str) -> dict:
        """本地与远程分支列表。"""
        with _repo(path) as repo:
            try:
                current = porcelain.active_branch(repo)
            except Exception:
                current = b""
            local, remote = [], []
            refs = repo.get_refs()
            for name, sha in refs.items():
                if name.startswith(b"refs/heads/"):
                    branch = name[len(b"refs/heads/"):]
                    up = _upstream_ref(repo, branch)
                    ahead = behind = 0
                    if up and up in refs:
                        if sha != refs[up]:
                            ahead = _count_commits(repo, sha, refs[up])
                            behind = _count_commits(repo, refs[up], sha)
                    local.append(
                        {
                            "name": _to_str(branch),
                            "sha": sha.decode(),
                            "short": sha.decode()[:8],
                            "current": branch == current,
                            "upstream": _to_str(up) if up else "",
                            "ahead": ahead,
                            "behind": behind,
                        }
                    )
                elif name.startswith(b"refs/remotes/") and not name.endswith(b"/HEAD"):
                    short = name[len(b"refs/remotes/"):]
                    remote.append(
                        {
                            "name": _to_str(short),
                            "sha": sha.decode(),
                            "short": sha.decode()[:8],
                        }
                    )
            local.sort(key=lambda x: (not x["current"], x["name"]))
            remote.sort(key=lambda x: x["name"])
            return {"local": local, "remote": remote, "current": _to_str(current)}

    @staticmethod
    def remotes(path: str) -> list[dict]:
        with _repo(path) as repo:
            config = repo.get_config()
            result = []
            try:
                sections = config.sections()
            except Exception:
                return result
            for section in sections:
                if len(section) == 2 and section[0] == b"remote":
                    name = section[1]
                    url = b""
                    try:
                        url = config.get(section, b"url")
                    except KeyError:
                        pass
                    result.append({"name": _to_str(name), "url": _to_str(url)})
            return result

    # ---------------- 分支操作 ---------------- #

    @staticmethod
    def checkout(path: str, branch: str, force: bool = False) -> None:
        with _repo(path) as repo:
            try:
                porcelain.checkout(repo, branch.encode(), force=force)
            except Exception as exc:
                msg, detail = humanize_error(exc)
                if "unstaged" in str(exc).lower() or "checkout" in str(exc).lower():
                    msg = (
                        f"切换到 {branch} 失败：工作区存在未提交的更改，"
                        "这些更改会被覆盖。请先提交或放弃更改。"
                    )
                raise GitError(msg, detail) from exc

    @staticmethod
    def create_branch(path: str, name: str, base: str | None = None, checkout: bool = True) -> None:
        with _repo(path) as repo:
            try:
                if checkout:
                    porcelain.checkout(repo, (base or "HEAD").encode(), new_branch=name.encode())
                else:
                    porcelain.branch_create(repo, name.encode(), (base or "HEAD").encode())
            except Exception as exc:
                msg, detail = humanize_error(exc)
                raise GitError(f"创建分支 {name} 失败：{msg}", detail) from exc

    @staticmethod
    def delete_branch(path: str, name: str) -> None:
        with _repo(path) as repo:
            try:
                porcelain.branch_delete(repo, name.encode())
            except Exception as exc:
                msg, detail = humanize_error(exc)
                if "not fully merged" in str(exc).lower() or "unmerged" in str(exc).lower():
                    msg = f"分支 {name} 尚未完全合并到其它分支，删除可能丢失提交。"
                raise GitError(msg, detail) from exc

    @staticmethod
    def rename_branch(path: str, old: str, new: str) -> None:
        with _repo(path) as repo:
            try:
                refs = repo.refs
                refs.add_if_new(b"refs/heads/" + new.encode(), refs[b"refs/heads/" + old.encode()])
                if refs.read_loose_ref(b"HEAD") == b"refs/heads/" + old.encode():
                    refs.set_symbolic_ref(b"HEAD", b"refs/heads/" + new.encode())
                refs.remove_if_equals(
                    b"refs/heads/" + old.encode(), refs[b"refs/heads/" + new.encode()]
                )
            except Exception as exc:
                msg, detail = humanize_error(exc)
                raise GitError(f"重命名分支失败：{msg}", detail) from exc

    @staticmethod
    def set_upstream(path: str, branch: str, remote: str) -> bool:
        """把本地分支的上游设置为 remote/branch。"""
        refs_remote = _list_remote_refs(path, remote)
        if refs_remote is None:
            return False
        target = f"refs/heads/{branch}".encode()
        if target not in refs_remote:
            return False
        with _repo(path) as repo:
            porcelain.set_branch_tracking(
                repo, branch.encode(), remote.encode(), f"refs/heads/{branch}".encode()
            )
        return True

    # ---------------- 工作区操作 ---------------- #

    @staticmethod
    def stage(path: str, paths: Sequence[str] | None = None) -> None:
        with _repo(path) as repo:
            try:
                porcelain.add(repo, list(paths) if paths else None)
            except Exception as exc:
                msg, detail = humanize_error(exc)
                raise GitError(f"暂存失败：{msg}", detail) from exc

    @staticmethod
    def unstage(path: str, paths: Sequence[str]) -> None:
        """把指定文件从暂存区退回（索引恢复为 HEAD 中的版本）。"""
        with _repo(path) as repo:
            try:
                index = repo.open_index()
                head_tree = None
                try:
                    head_tree = repo[repo[repo.head()].tree]
                except Exception:
                    head_tree = None
                store = repo.object_store
                for rel in paths:
                    key = _norm(rel).encode("utf-8")
                    sha = None
                    mode = None
                    if head_tree is not None:
                        try:
                            mode_sha = head_tree.lookup_path(store.__getitem__, key)
                            if isinstance(mode_sha, tuple) and len(mode_sha) == 2:
                                candidate_mode, candidate = mode_sha
                                # 目录条目无法退回暂存区，按“新文件”处理
                                if candidate_mode & 0o170000 != 0o040000:
                                    sha = candidate
                                    mode = candidate_mode
                        except Exception:
                            sha = None
                    if not sha:
                        _drop_index_entry(index, key)
                        continue
                    # 统计信息清零：让后续状态检查重新按内容比对，
                    # 否则会把「已修改」误判为「未变化」。
                    index[key] = IndexEntry(0, 0, 0, 0, mode or 0o100644, 0, 0, 0, sha)
                index.write()
            except Exception as exc:
                msg, detail = humanize_error(exc)
                raise GitError(f"取消暂存失败：{msg}", detail) from exc

    @staticmethod
    def discard(path: str, paths: Sequence[str], untracked: Sequence[str] = ()) -> None:
        """丢弃更改：已跟踪文件恢复到 HEAD，未跟踪文件从磁盘删除。"""
        with _repo(path) as repo:
            tracked = list(paths)
            if tracked:
                try:
                    porcelain.checkout(repo, b"HEAD", paths=tracked)
                except Exception as exc:
                    msg, detail = humanize_error(exc)
                    raise GitError(f"放弃更改失败：{msg}", detail) from exc
            for rel in untracked:
                full = os.path.join(path, rel)
                if os.path.isfile(full):
                    try:
                        os.remove(full)
                    except OSError as exc:
                        raise GitError(f"删除文件失败：{full}", str(exc)) from exc

    @staticmethod
    def clean(path: str, target_dir: str | None = None) -> None:
        with _repo(path) as repo:
            try:
                porcelain.clean(repo, target_dir=target_dir)
            except Exception as exc:
                msg, detail = humanize_error(exc)
                raise GitError(f"清理未跟踪文件失败：{msg}", detail) from exc

    @staticmethod
    def commit(
        path: str,
        message: str,
        author_name: str,
        author_email: str,
        amend: bool = False,
    ) -> str:
        with _repo(path) as repo:
            ts = time.time()
            tz = -time.timezone if not time.daylight else -time.altzone
            ident = f"{author_name} <{author_email}>".encode()
            try:
                sha = porcelain.commit(
                    repo,
                    message=message.encode(),
                    author=ident,
                    committer=ident,
                    author_timestamp=ts,
                    commit_timestamp=ts,
                    author_timezone=tz,
                    commit_timezone=tz,
                    amend=amend,
                )
            except Exception as exc:
                msg, detail = humanize_error(exc)
                raise GitError(f"提交失败：{msg}", detail) from exc
            return sha.decode() if isinstance(sha, bytes) else str(sha)

    # ---------------- 差异 ---------------- #

    @staticmethod
    def diff_text(
        path: str,
        file_path: str | None = None,
        staged: bool = False,
        commit: str | None = None,
    ) -> str:
        """返回统一 diff 文本。"""
        buf = io.BytesIO()
        paths = [file_path.encode()] if file_path else None
        with _repo(path) as repo:
            try:
                if commit:
                    target = repo[commit.encode()]
                    parents = getattr(target, "parents", [])
                    if parents:
                        porcelain.diff(
                            repo,
                            commit=parents[0],
                            commit2=target.id,
                            paths=paths,
                            outstream=buf,
                        )
                    else:
                        # 首个提交：全部视为新增
                        porcelain.diff(repo, commit2=target.id, paths=paths, outstream=buf)
                else:
                    porcelain.diff(repo, staged=staged, paths=paths, outstream=buf)
            except GitError:
                raise
            except Exception as exc:
                msg, detail = humanize_error(exc)
                raise GitError(f"生成差异失败：{msg}", detail) from exc
        return buf.getvalue().decode("utf-8", "replace")

    @staticmethod
    def commit_detail(path: str, sha: str) -> dict:
        """单个提交的详情与文件变更清单。"""
        with _repo(path) as repo:
            try:
                commit: Commit = repo[sha.encode()]
            except KeyError as exc:
                raise GitError("找不到该提交。", str(exc)) from exc
            files = []
            try:
                parent_tree = repo[commit.parents[0]].tree if commit.parents else None
                from dulwich.diff_tree import tree_changes

                if parent_tree is None:
                    changes = list(tree_changes(repo.object_store, None, commit.tree))
                else:
                    changes = list(
                        tree_changes(repo.object_store, parent_tree, commit.tree, rename_detector=None)
                    )
                for change in changes:
                    p = _to_str(change.new.path or change.old.path or b"")
                    files.append({"status": _change_type(change), "path": p})
            except Exception as exc:
                log.warning("解析提交文件列表失败 %s: %s", sha, exc)
            return {
                "sha": commit.id.decode(),
                "subject": _commit_message(commit).splitlines()[0] if _commit_message(commit) else "",
                "message": _commit_message(commit),
                "author": _to_str(commit.author).rsplit(" <", 1)[0],
                "email": _extract_email(commit.author),
                "date": _fmt_ts(commit.commit_time),
                "parents": [p.decode() for p in commit.parents],
                "files": files,
            }

    # ---------------- 远程操作 ---------------- #

    @staticmethod
    def remote_url(path: str, remote: str = "origin") -> str:
        with _repo(path) as repo:
            try:
                return _to_str(repo.get_config().get((b"remote", remote.encode()), b"url"))
            except KeyError:
                return ""

    @staticmethod
    def add_remote(path: str, name: str, url: str) -> None:
        with _repo(path) as repo:
            try:
                porcelain.remote_add(repo, name, url)
            except Exception as exc:
                msg, detail = humanize_error(exc)
                raise GitError(f"添加远程失败：{msg}", detail) from exc

    @staticmethod
    def set_remote_url(path: str, name: str, url: str) -> None:
        with _repo(path) as repo:
            config = repo.get_config()
            config.set((b"remote", name.encode()), b"url", url.encode())
            config.write_to_path()

    @staticmethod
    def remove_remote(path: str, name: str) -> None:
        with _repo(path) as repo:
            try:
                porcelain.remote_remove(repo, name)
            except Exception as exc:
                msg, detail = humanize_error(exc)
                raise GitError(f"删除远程失败：{msg}", detail) from exc

    @staticmethod
    def fetch(
        path: str,
        remote: str = "origin",
        credential: Credential | None = None,
        platform: str = "github",
        progress: ProgressCb | None = None,
        prune: bool = False,
        tags: bool = True,
        detail: Callable[[dict], None] | None = None,
    ) -> dict:
        """拉取远程引用到本地（不改变工作区），返回更新情况。"""
        credential = credential or Credential()
        user, pwd = credential.basic_auth(platform)
        monitor = _monitor_for("fetch", progress, detail)
        with _repo(path) as repo:
            before = dict(repo.get_refs())
            if monitor is not None:
                monitor.set_phase("连接远程", f"正在从 {remote} 获取远程更新…")
            try:
                result = _retry_network(
                    lambda: porcelain.fetch(
                        repo,
                        remote,
                        username=user,
                        password=pwd,
                        prune=prune,
                        include_tags=tags,
                        **_monitor_kwargs(monitor),
                    ),
                    monitor=monitor,
                    label=f"获取 {remote}",
                )
            except Exception as exc:
                _finish_monitor(monitor, False)
                msg, dt = humanize_error(exc, retried=_retries(monitor))
                raise GitError(f"拉取远程数据失败：{msg}", dt) from exc
            after = dict(repo.get_refs())
            updated = [
                _to_str(ref)
                for ref, sha in after.items()
                if before.get(ref) != sha and not ref.startswith(b"refs/remotes/")
            ]
            changed_remote_refs = [
                _to_str(ref)
                for ref, sha in after.items()
                if ref.startswith(b"refs/remotes/") and before.get(ref) != sha
            ]
            _finish_monitor(monitor, True, f"已获取 {remote} 的远程引用")
            return {
                "remote_refs": {_to_str(k): v.decode() for k, v in dict(getattr(result, "refs", {}) or {}).items()},
                "updated": updated,
                "updated_remote_refs": changed_remote_refs,
            }

    @staticmethod
    def remote_branch_sha(
        path: str, remote: str, branch: str, credential: Credential, platform: str
    ) -> str | None:
        """不落盘地查询远程分支头，用于变更探测。"""
        url = GitEngine.remote_url(path, remote)
        if not url:
            return None
        user, pwd = credential.basic_auth(platform)
        try:
            result = _retry_network(
                lambda: porcelain.ls_remote(url, username=user, password=pwd),
                label=f"探测 {remote}",
                attempts=2,
            )
        except Exception as exc:
            log.debug("ls-remote 探测失败: %s", exc)
            return None
        refs = dict(getattr(result, "refs", {}) or {})
        for key in (f"refs/heads/{branch}".encode(), b"HEAD"):
            if key in refs:
                sha = refs[key]
                return sha.decode() if isinstance(sha, bytes) else str(sha)
        return None

    @staticmethod
    def pull(
        path: str,
        remote: str = "origin",
        credential: Credential | None = None,
        platform: str = "github",
        progress: ProgressCb | None = None,
        strategy: str = "auto",
        detail: Callable[[dict], None] | None = None,
    ) -> dict:
        """拉取并合并。

        strategy:
          - auto        : 可快进则快进，否则尝试普通合并（冲突时自动回滚）
          - ff_only     : 仅快进，分歧时报错
          - force_local : 用远程强制覆盖本地分支（危险，会丢弃本地提交）
        """
        credential = credential or Credential()
        user, pwd = credential.basic_auth(platform)
        monitor = _monitor_for("pull", progress, detail)
        with _repo(path) as repo:
            branch = porcelain.active_branch(repo)
            if not branch:
                raise GitError("当前处于游离 HEAD 状态，请先切换到具体分支后再拉取。")

            st = porcelain.status(repo, untracked_files="no")
            staged_now = _normalize_staged(getattr(st, "staged", None))
            unstaged_now = _normalize_paths(getattr(st, "unstaged", None))
            if (staged_now or unstaged_now) and strategy != "force_local":
                raise GitError(
                    "工作区存在未提交的更改，直接拉取可能覆盖这些改动。\n"
                    "请先提交或暂存（可使用「暂存当前更改」）后再拉取。"
                )

            before_head = repo.head()
            if monitor is not None:
                monitor.set_phase("连接远程", f"正在从 {remote} 拉取…")
            try:
                _retry_network(
                    lambda: porcelain.fetch(
                        repo,
                        remote,
                        username=user,
                        password=pwd,
                        **_monitor_kwargs(monitor),
                    ),
                    monitor=monitor,
                    label=f"拉取 {remote}",
                )
            except Exception as exc:
                _finish_monitor(monitor, False)
                msg, dt = humanize_error(exc, retried=_retries(monitor))
                raise GitError(f"拉取失败：{msg}", dt) from exc
            if monitor is not None:
                monitor.set_phase("合并", "正在合并远程更新…")

            upstream = _upstream_ref(repo, branch)
            if not upstream:
                # 未设置上游时，尝试自动匹配同名远程分支
                auto = f"refs/remotes/{remote}/{branch.decode()}".encode()
                if auto in repo.get_refs() and GitEngine.set_upstream(path, branch.decode(), remote):
                    upstream = auto
            if not upstream or upstream not in repo.get_refs():
                _finish_monitor(monitor, True, "未找到对应的远程跟踪分支")
                return {"result": "no_upstream", "branch": _to_str(branch), "conflicts": []}

            local_sha = repo.refs[b"refs/heads/" + branch]
            remote_sha = repo.refs[upstream]
            if local_sha == remote_sha:
                _finish_monitor(monitor, True, "本地已是最新")
                return {"result": "up_to_date", "branch": _to_str(branch), "conflicts": []}

            ahead = _count_commits(repo, local_sha, remote_sha)
            behind = _count_commits(repo, remote_sha, local_sha)

            if strategy == "force_local":
                repo.refs[b"refs/heads/" + branch] = remote_sha
                porcelain.reset(repo, "hard", treeish=remote_sha)
                _finish_monitor(monitor, True, "已用远程覆盖本地分支")
                return {
                    "result": "forced",
                    "branch": _to_str(branch),
                    "conflicts": [],
                    "behind": behind,
                }

            if ahead == 0:
                # 快进
                repo.refs[b"refs/heads/" + branch] = remote_sha
                porcelain.reset(repo, "hard", treeish=remote_sha)
                _finish_monitor(monitor, True, f"已快进 {behind} 个提交")
                return {
                    "result": "fast_forward",
                    "branch": _to_str(branch),
                    "conflicts": [],
                    "behind": behind,
                }

            if strategy == "ff_only":
                raise GitError(
                    f"本地分支 {_to_str(branch)} 与远程已分叉（本地领先 {ahead}，远程领先 {behind}），"
                    "无法快进。请先推送本地提交或手动处理分歧。"
                )

            # 先做一次「只读试合并」，在不动工作区的前提下预判冲突
            conflicts = []
            try:
                bases = porcelain.merge_base(repo, [local_sha, remote_sha])
                base = bases[0] if bases else None
                _merged_tree, conflicts = porcelain.merge_tree(repo, base, local_sha, remote_sha)
            except Exception as exc:  # noqa: BLE001
                log.warning("合并预检失败，将直接尝试合并: %s", exc)
                conflicts = []

            if conflicts:
                names = "\n".join(f"  • {_to_str(c)}" for c in conflicts[:20])
                more = f"\n  … 另有 {len(conflicts) - 20} 个文件" if len(conflicts) > 20 else ""
                raise GitError(
                    f"检测到合并冲突（{len(conflicts)} 个文件），已阻止本次拉取。\n"
                    "本地与远程的代码都没有被改动。\n\n"
                    f"冲突文件：\n{names}{more}\n\n"
                    "建议处理方式：\n"
                    "  1) 先提交并推送本地改动，再重新拉取；\n"
                    "  2) 或使用「强制用远程覆盖本地」（会丢弃本地未推送的提交，请谨慎）。"
                )

            # 预检通过，执行真实合并
            try:
                merge_sha, merge_conflicts = porcelain.merge(repo, remote_sha)
            except Exception as exc:
                _abort_merge(repo, before_head)
                _finish_monitor(monitor, False)
                msg, dt = humanize_error(exc)
                raise GitError(f"合并失败，已回滚到操作前状态：{msg}", dt) from exc

            if merge_conflicts:
                _abort_merge(repo, before_head)
                names = "\n".join(f"  • {_to_str(c)}" for c in merge_conflicts[:20])
                raise GitError(
                    "检测到合并冲突，已自动回滚，本地代码未被改动。\n"
                    f"冲突文件（{len(merge_conflicts)} 个）：\n{names}\n"
                    "建议：先提交本地改动并推送到远程，或在本地手动解决分歧后再同步。"
                )
            _finish_monitor(monitor, True, "拉取并合并完成")
            return {
                "result": "merged",
                "branch": _to_str(branch),
                "commit": merge_sha.decode() if isinstance(merge_sha, bytes) and merge_sha else "",
                "conflicts": [],
                "behind": behind,
            }

    @staticmethod
    def push(
        path: str,
        remote: str = "origin",
        branch: str | None = None,
        credential: Credential | None = None,
        platform: str = "github",
        progress: ProgressCb | None = None,
        force: bool = False,
        set_upstream: bool = True,
        detail: Callable[[dict], None] | None = None,
        cancel: CancelToken | None = None,
    ) -> dict:
        """推送本地分支到远程。

        传入 ``cancel`` 后可在传输过程中（含自动重试的等待期内）随时中止，
        中止时抛出 ``CancelledError``。
        """
        credential = credential or Credential()
        user, pwd = credential.basic_auth(platform)
        monitor = _monitor_for("push", progress, detail, cancel)
        with _repo(path) as repo:
            try:
                active = porcelain.active_branch(repo)
            except Exception:
                active = b""
            target = (branch or _to_str(active)).encode()
            if not target:
                raise GitError("无法确定要推送的分支（当前处于游离 HEAD 状态）。")
            refspec = b"refs/heads/" + target
            if cancel is not None and cancel.cancelled:
                _finish_monitor(monitor, False, "推送已停止")
                raise CancelledError("已停止推送。")
            if monitor is not None:
                monitor.set_phase("连接远程", f"正在向 {remote} 推送分支 {_to_str(target)}…")
            try:
                result = _retry_network(
                    lambda: porcelain.push(
                        repo,
                        remote,
                        refspec,
                        username=user,
                        password=pwd,
                        force=force,
                        set_upstream=set_upstream,
                        **_monitor_kwargs(monitor),
                    ),
                    monitor=monitor,
                    label=f"推送 {_to_str(target)}",
                    cancel=cancel,
                )
            except _Cancelled as exc:
                _finish_monitor(monitor, False, "已停止推送")
                raise CancelledError(
                    "已停止推送。",
                    "传输已被用户中止；远程仓库可能只收到了部分数据，"
                    "下次推送会自动补齐。",
                ) from exc
            except Exception as exc:
                _finish_monitor(monitor, False)
                # 兜底：取消信号可能被 dulwich 包装成其它异常抛出
                if isinstance(exc, CancelledError) or (cancel is not None and cancel.cancelled):
                    raise CancelledError(
                        "已停止推送。",
                        "传输已被用户中止；远程仓库可能只收到了部分数据，"
                        "下次推送会自动补齐。",
                    ) from exc
                msg, dt = humanize_error(exc, retried=_retries(monitor))
                raise GitError(f"推送失败：{msg}", dt) from exc
            refs = getattr(result, "refs", None) or {}
            if isinstance(refs, dict):
                refs_out = {_to_str(k): _to_str(v) for k, v in refs.items()}
            else:
                refs_out = {}
            errors = [r for r in refs_out.values() if "reject" in r.lower() or "error" in r.lower()]
            if errors:
                _finish_monitor(monitor, False)
                raise GitError("远程拒绝了推送：\n" + "\n".join(errors))
            _finish_monitor(monitor, True, f"分支 {_to_str(target)} 推送完成")
            return {"refs": refs_out, "branch": _to_str(target)}

    @staticmethod
    def clone(
        url: str,
        target: str,
        credential: Credential | None = None,
        platform: str = "github",
        branch: str | None = None,
        depth: int | None = None,
        progress: ProgressCb | None = None,
        cancel: CancelToken | None = None,
        detail: Callable[[dict], None] | None = None,
    ) -> str:
        credential = credential or Credential()
        user, pwd = credential.basic_auth(platform)
        if cancel is not None and cancel.cancelled:
            _cleanup_dir(target)
            raise CancelledError(detail=f"已中止克隆并清理未完成的目录：{target}")
        parent = os.path.dirname(os.path.abspath(target))
        if parent and not os.path.isdir(parent):
            os.makedirs(parent, exist_ok=True)
        existed = os.path.isdir(target) and bool(os.listdir(target))
        if existed:
            raise GitError(f"目标目录已存在且非空：{target}")
        created = not os.path.isdir(target)
        monitor = _monitor_for("clone", progress, detail, cancel)
        if monitor is not None:
            monitor.set_phase("连接远程", f"正在连接 {url} …")
        try:
            _retry_network(
                lambda: porcelain.clone(
                    url,
                    target,
                    username=user,
                    password=pwd,
                    branch=branch,
                    depth=depth,
                    **_monitor_kwargs(monitor),
                ),
                monitor=monitor,
                label="克隆",
                cancel=cancel,
            )
        except _Cancelled as exc:
            _finish_monitor(monitor, False, "克隆已取消")
            _cleanup_dir(target)
            raise CancelledError(detail=f"已中止克隆并清理未完成的目录：{target}") from exc
        except Exception as exc:  # noqa: BLE001
            if isinstance(exc, CancelledError):
                _finish_monitor(monitor, False, "克隆已取消")
                _cleanup_dir(target)
                raise
            if created:
                _cleanup_dir(target)
            if cancel is not None and cancel.cancelled:
                _finish_monitor(monitor, False, "克隆已取消")
                raise CancelledError(detail=f"已中止克隆并清理未完成的目录：{target}") from exc
            _finish_monitor(monitor, False)
            msg, detail_text = humanize_error(exc, retried=_retries(monitor))
            raise GitError(f"克隆失败：{msg}", detail_text) from exc
        _finish_monitor(monitor, True, "克隆完成")
        return os.path.abspath(target)

    # ---------------- 暂存（stash） ---------------- #

    @staticmethod
    def stash_push(path: str) -> None:
        with _repo(path) as repo:
            try:
                porcelain.stash_push(repo)
            except Exception as exc:
                msg, detail = humanize_error(exc)
                raise GitError(f"暂存更改失败：{msg}", detail) from exc

    @staticmethod
    def stash_pop(path: str) -> None:
        with _repo(path) as repo:
            try:
                porcelain.stash_pop(repo)
            except Exception as exc:
                msg, detail = humanize_error(exc)
                raise GitError(f"恢复暂存失败：{msg}", detail) from exc

    @staticmethod
    def stash_list(path: str) -> list[dict]:
        with _repo(path) as repo:
            try:
                return [
                    {"index": idx, "message": _to_str(msg[1] if isinstance(msg, tuple) else msg)}
                    for idx, msg in porcelain.stash_list(repo)
                ]
            except Exception:
                return []

    # ---------------- 初始化 ---------------- #

    @staticmethod
    def init(path: str, default_branch: str = "main") -> str:
        os.makedirs(path, exist_ok=True)
        repo = porcelain.init(path)
        try:
            if b"refs/heads/" + default_branch.encode() not in repo.get_refs():
                repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/" + default_branch.encode())
        except Exception as exc:  # 不影响仓库创建结果
            log.debug("设置默认分支失败: %s", exc)
        finally:
            repo.close()
        return os.path.abspath(path)

    @staticmethod
    def commit_count(path: str) -> int:
        with _repo(path) as repo:
            try:
                head = repo.head()
            except Exception:
                return 0
            if not head:
                return 0
            return len(list(repo.get_walker(include=[head])))


# --------------------------------------------------------------------------- #
# 内部辅助
# --------------------------------------------------------------------------- #


def _commit_message(commit) -> str:
    msg = getattr(commit, "message", b"") or b""
    return _to_str(msg).strip()


def _abort_merge(repo: Repo, commit_sha: bytes) -> None:
    """合并失败后把索引与工作区恢复到指定提交。

    冲突状态下索引含有未合并条目，dulwich 的 reset 无法直接使用，
    因此这里丢弃索引文件并按目标提交树重建，保证本地代码回到操作前状态。
    """
    from dulwich.index import build_index_from_tree

    try:
        target = repo[commit_sha]
    except KeyError:
        log.error("回滚失败：找不到提交 %s", commit_sha)
        return
    index_path = os.path.join(repo.controldir(), "index")
    try:
        if os.path.exists(index_path):
            os.remove(index_path)
    except OSError as exc:
        log.warning("删除索引文件失败: %s", exc)
    root = os.path.dirname(os.path.normpath(repo.controldir()))
    try:
        build_index_from_tree(root, index_path, repo.object_store, target.tree)
    except Exception as exc:  # noqa: BLE001
        log.error("重建索引失败: %s", exc)


def _extract_email(ident: bytes) -> str:
    text = _to_str(ident)
    if "<" in text and ">" in text:
        return text[text.index("<") + 1 : text.index(">")]
    return ""


def _fmt_ts(ts: int | None) -> str:
    if not ts:
        return ""
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))


def _count_commits(repo: Repo, include: bytes, exclude: bytes) -> int:
    """统计 include 可达但 exclude 不可达的提交数。"""
    try:
        return sum(1 for _ in repo.get_walker(include=[include], exclude=[exclude]))
    except Exception:
        try:
            from dulwich.graph import can_fast_forward  # noqa: F401
        except Exception:
            pass
        return 0


def _upstream_ref(repo: Repo, branch: bytes) -> bytes | None:
    try:
        config = repo.get_config()
        section = (b"branch", branch)
        remote = config.get(section, b"remote")
        merge = config.get(section, b"merge")
    except KeyError:
        return None
    except Exception:
        return None
    if not remote or not merge:
        return None
    remote_name = _to_str(remote)
    if remote_name == ".":
        return merge
    if merge.startswith(b"refs/heads/"):
        return b"refs/remotes/" + remote + b"/" + merge[len(b"refs/heads/"):]
    return merge


def _refs_at(repo: Repo, sha: bytes) -> list[str]:
    try:
        return [_to_str(name) for name, value in repo.get_refs().items() if value == sha]
    except Exception:
        return []


def _change_type(change) -> str:
    from dulwich.diff_tree import CHANGE_ADD, CHANGE_COPY, CHANGE_DELETE, CHANGE_MODIFY, CHANGE_RENAME

    mapping = {
        CHANGE_ADD: "新增",
        CHANGE_DELETE: "删除",
        CHANGE_MODIFY: "修改",
        CHANGE_RENAME: "重命名",
        CHANGE_COPY: "复制",
    }
    return mapping.get(change.type, "变更")


def _list_remote_refs(path: str, remote: str) -> set[bytes] | None:
    with porcelain.open_repo_closing(path) as repo:
        try:
            if not repo.get_config().has_section((b"remote", remote.encode())):
                return None
        except Exception:
            return None
    prefix = f"refs/remotes/{remote}/".encode()
    with porcelain.open_repo_closing(path) as repo:
        return {
            b"refs/heads/" + name[len(prefix):]
            for name in repo.get_refs()
            if name.startswith(prefix)
        }
