from __future__ import annotations

"""插件设置的默认值与归一化。"""

from typing import Any

DEFAULTS: dict[str, Any] = {
    "approval_mode": "confirm_danger",
    "shell_mode": "auto",
    "temperature": 0.3,
    "allow_network": True,
    "allow_private_network": False,
    "allow_self_modify": False,
    "auto_enable_plugin": True,
    "notify_on_finish": True,
    "model_override": "",
    "workspace_dir": "",
}

APPROVAL_MODES = ("auto", "confirm_danger", "confirm_all")
SHELL_MODES = ("auto", "bash", "cmd", "powershell", "sh", "off")


def _as_float(value: Any, fallback: float, low: float, high: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return fallback
    return max(low, min(high, number))


def _as_bool(value: Any, fallback: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if text in ("1", "true", "yes", "on"):
            return True
        if text in ("0", "false", "no", "off", ""):
            return False
    if isinstance(value, (int, float)):
        return bool(value)
    return fallback


def normalize_settings(raw: dict[str, Any] | None) -> dict[str, Any]:
    data = dict(DEFAULTS)
    if isinstance(raw, dict):
        data.update(raw)

    approval = str(data.get("approval_mode", "confirm_danger")).strip().lower()
    if approval not in APPROVAL_MODES:
        approval = "confirm_danger"
    shell = str(data.get("shell_mode", "auto")).strip().lower()
    if shell not in SHELL_MODES:
        shell = "auto"

    return {
        "approval_mode": approval,
        "shell_mode": shell,
        "temperature": _as_float(data.get("temperature"), 0.3, 0.0, 1.5),
        "allow_network": _as_bool(data.get("allow_network"), True),
        "allow_private_network": _as_bool(data.get("allow_private_network"), False),
        "allow_self_modify": _as_bool(data.get("allow_self_modify"), False),
        "auto_enable_plugin": _as_bool(data.get("auto_enable_plugin"), True),
        "notify_on_finish": _as_bool(data.get("notify_on_finish"), True),
        "model_override": str(data.get("model_override", "") or "").strip(),
        "workspace_dir": str(data.get("workspace_dir", "") or "").strip(),
    }
