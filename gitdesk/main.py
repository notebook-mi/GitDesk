"""GitDesk 程序入口。"""

from __future__ import annotations

import ctypes
import os
import sys


def _configure_environment() -> None:
    """打包运行时的环境准备。"""
    # 关闭 Qt 高分屏兼容性告警，启用高 DPI 缩放
    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")
    if sys.platform.startswith("win"):
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass


def main() -> int:
    _configure_environment()

    if "--self-test" in sys.argv:
        from .core.logger import setup_logging
        from .selftest import run_self_test

        setup_logging()
        index = sys.argv.index("--self-test")
        report = sys.argv[index + 1] if len(sys.argv) > index + 1 else None
        return run_self_test(report)

    if "--net-check" in sys.argv:
        from .core.logger import setup_logging
        from .selftest import run_net_check

        setup_logging()
        return run_net_check()

    from PySide6.QtCore import Qt
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication, QMessageBox

    from . import __app_name__, __version__
    from .core.logger import get_logger, setup_logging
    from .core.paths import resource_path

    setup_logging()
    log = get_logger("main")

    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    app = QApplication(sys.argv)
    app.setApplicationName(__app_name__)
    app.setApplicationVersion(__version__)
    app.setOrganizationName("GitDesk")

    from .ui.style import apply_light_theme

    apply_light_theme(app)

    icon_path = resource_path("assets", "gitdesk.ico")
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))

    try:
        from .ui.main_window import MainWindow

        window = MainWindow()
        window.show()
    except Exception as exc:  # noqa: BLE001
        import traceback

        log.critical("启动失败: %s\n%s", exc, traceback.format_exc())
        box = QMessageBox()
        box.setWindowTitle("GitDesk 启动失败")
        box.setIcon(QMessageBox.Critical)
        box.setText(f"程序启动时发生错误：\n{exc}")
        box.setDetailedText(traceback.format_exc())
        box.exec()
        return 1

    log.info("界面已就绪")
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
