from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes
from pathlib import Path

try:
    import winreg
except ImportError:  # pragma: no cover
    winreg = None


class _SHFILEOPSTRUCTW(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("wFunc", wintypes.UINT),
        ("pFrom", wintypes.LPCWSTR),
        ("pTo", wintypes.LPCWSTR),
        ("fFlags", wintypes.WORD),
        ("fAnyOperationsAborted", wintypes.BOOL),
        ("hNameMappings", ctypes.c_void_p),
        ("lpszProgressTitle", wintypes.LPCWSTR),
    ]


def move_to_trash(path: Path) -> tuple[bool, str]:
    """把文件或目录移入系统回收站，失败时返回原因。

    不使用永久删除：用户从插件管理里删掉的插件仍然可以从回收站找回。
    """
    target = Path(path)
    if not target.exists() and not target.is_symlink():
        return False, "路径不存在。"
    if sys.platform != "win32":
        return False, "当前系统不支持回收站。"

    text = str(target.resolve())
    # SHFileOperationW 要求 pFrom 以两个 NUL 结尾，直接给 Python 字符串会被截断。
    buffer = ctypes.create_unicode_buffer(text, len(text) + 2)
    operation = _SHFILEOPSTRUCTW()
    operation.hwnd = None
    operation.wFunc = 3  # FO_DELETE
    operation.pFrom = ctypes.cast(buffer, wintypes.LPCWSTR)
    operation.pTo = None
    # FOF_SILENT | FOF_NOCONFIRMATION | FOF_ALLOWUNDO | FOF_NOERRORUI
    operation.fFlags = 0x0004 | 0x0010 | 0x0040 | 0x0400
    try:
        shell32 = ctypes.windll.shell32
        shell32.SHFileOperationW.argtypes = [ctypes.POINTER(_SHFILEOPSTRUCTW)]
        shell32.SHFileOperationW.restype = ctypes.c_int
        result = shell32.SHFileOperationW(ctypes.byref(operation))
    except Exception as exc:  # pragma: no cover - 依赖 Windows shell
        return False, f"{type(exc).__name__}: {exc}"
    if result != 0:
        return False, f"系统返回错误码 {result}。"
    if operation.fAnyOperationsAborted:
        return False, "操作已取消。"
    if target.exists() or target.is_symlink():
        return False, "文件仍被占用或没有删除权限。"
    return True, ""


RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "LFELKEEA"
LEGACY_RUN_VALUES = ("RinDesktopPet",)


def startup_command() -> str:
    if getattr(sys, "frozen", False):
        return f'"{Path(sys.executable).resolve()}"'
    script = Path(__file__).resolve().parent / "main.py"
    python_exe = Path(sys.executable).resolve()
    pythonw = python_exe.with_name("pythonw.exe")
    runner = pythonw if pythonw.exists() else python_exe
    return f'"{runner}" "{script}"'


def is_startup_enabled() -> bool:
    if not winreg:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            for name in (RUN_VALUE, *LEGACY_RUN_VALUES):
                try:
                    value, _ = winreg.QueryValueEx(key, name)
                except FileNotFoundError:
                    continue
                if value:
                    return True
            return False
    except OSError:
        return False


def set_startup_enabled(enabled: bool) -> bool:
    if not winreg:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            if enabled:
                winreg.SetValueEx(key, RUN_VALUE, 0, winreg.REG_SZ, startup_command())
                for legacy in LEGACY_RUN_VALUES:
                    try:
                        winreg.DeleteValue(key, legacy)
                    except FileNotFoundError:
                        pass
            else:
                for name in (RUN_VALUE, *LEGACY_RUN_VALUES):
                    try:
                        winreg.DeleteValue(key, name)
                    except FileNotFoundError:
                        pass
        return True
    except OSError:
        return False
