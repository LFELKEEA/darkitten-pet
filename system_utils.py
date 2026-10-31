from __future__ import annotations

import sys
from pathlib import Path

try:
    import winreg
except ImportError:  # pragma: no cover
    winreg = None


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
