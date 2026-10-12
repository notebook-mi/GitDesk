"""日志系统：滚动文件 + 内存订阅（供界面实时展示）。"""

from __future__ import annotations

import logging
import sys
import threading
import time
import traceback
from logging.handlers import RotatingFileHandler

from .paths import log_dir

LOGGER_NAME = "gitdesk"
_FILE_FORMAT = "%(asctime)s | %(levelname)-7s | %(threadName)-14s | %(name)s | %(message)s"
_UI_FORMAT = "%(asctime)s  %(levelname)-7s  %(message)s"

_configured = False


class BusHandler(logging.Handler):
    """把日志事件广播给订阅者（线程安全的简单回调列表）。"""

    def __init__(self) -> None:
        super().__init__()
        self._listeners: list = []
        self._lock = threading.Lock()

    def add_listener(self, callback) -> None:
        with self._lock:
            if callback not in self._listeners:
                self._listeners.append(callback)

    def remove_listener(self, callback) -> None:
        with self._lock:
            if callback in self._listeners:
                self._listeners.remove(callback)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = self.format(record)
        except Exception:
            return
        with self._lock:
            listeners = list(self._listeners)
        for callback in listeners:
            try:
                callback(record.levelno, record.levelname, message)
            except Exception:
                pass


bus_handler = BusHandler()
bus_handler.setFormatter(logging.Formatter(_UI_FORMAT, datefmt="%H:%M:%S"))


def setup_logging(level: int = logging.INFO) -> logging.Logger:
    """初始化日志（幂等）。"""
    global _configured
    logger = logging.getLogger(LOGGER_NAME)
    if _configured:
        return logger

    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    logger.handlers.clear()

    log_path = log_dir() / f"gitdesk-{time.strftime('%Y-%m-%d')}.log"
    file_handler = RotatingFileHandler(
        log_path, maxBytes=2 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    file_handler.setFormatter(logging.Formatter(_FILE_FORMAT))
    file_handler.setLevel(logging.DEBUG)
    logger.addHandler(file_handler)

    console = logging.StreamHandler(sys.stderr) if sys.stderr is not None else None
    if console is not None:
        console.setFormatter(logging.Formatter(_FILE_FORMAT))
        console.setLevel(level)
        logger.addHandler(console)

    bus_handler.setLevel(logging.DEBUG)
    logger.addHandler(bus_handler)

    logging.getLogger("dulwich").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)

    def _excepthook(exc_type, exc_value, exc_tb):
        logger.critical(
            "未捕获异常:\n%s", "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        )
        if sys.__excepthook__ is not None:
            sys.__excepthook__(exc_type, exc_value, exc_tb)

    sys.excepthook = _excepthook
    _configured = True
    logger.info("=" * 62)
    logger.info("GitDesk 启动，日志文件: %s", log_path)
    return logger


def get_logger(name: str = "") -> logging.Logger:
    return logging.getLogger(f"{LOGGER_NAME}.{name}" if name else LOGGER_NAME)


def current_log_file() -> str:
    return str(log_dir() / f"gitdesk-{time.strftime('%Y-%m-%d')}.log")


LEVEL_COLORS = {
    "DEBUG": "#7a8699",
    "INFO": "#2f6f4f",
    "WARNING": "#b4761a",
    "ERROR": "#c0392b",
    "CRITICAL": "#8e1b1b",
}
