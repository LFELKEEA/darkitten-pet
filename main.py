from __future__ import annotations

import os
import sys
import traceback

_APP_DIR = os.path.dirname(os.path.abspath(__file__))
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

from PySide6.QtCore import QSharedMemory
from PySide6.QtWidgets import QApplication, QMessageBox

from app import run
from app_paths import DATA_DIR


def _write_error_log(exc: BaseException) -> str:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = DATA_DIR / "error.log"
    with open(path, "w", encoding="utf-8") as f:
        f.write("".join(traceback.format_exception(type(exc), exc, exc.__traceback__)))
    return str(path)


def main() -> int:
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("LFELKEEA")
    except Exception:
        pass

    app = QApplication(sys.argv)
    app.setApplicationName("LFELKEEA")
    app.setApplicationDisplayName("LFELKEEA · 桌面宠物")
    app.setOrganizationName("LFELKEEA")
    app.setQuitOnLastWindowClosed(False)

    shared = QSharedMemory("RinDesktopPet_SingleInstance_v1")
    if not shared.create(1):
        QMessageBox.information(
            None,
            "LFELKEEA",
            "桌宠已经在运行了（可能是旧版）。请先在托盘右键退出，再重新启动 LFELKEEA。",
        )
        return 0
    app._rin_shared_memory = shared  # type: ignore[attr-defined]

    try:
        return run(app)
    except Exception as exc:  # pragma: no cover
        log_path = _write_error_log(exc)
        QMessageBox.critical(
            None,
            "LFELKEEA 启动失败",
            f"桌宠启动时遇到问题：\n{exc}\n\n详细信息：{log_path}",
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
