"""GitDesk 可执行文件入口。"""

from __future__ import annotations

import multiprocessing
import sys


def _entry() -> int:
    from gitdesk.main import main

    return main()


if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(_entry())
