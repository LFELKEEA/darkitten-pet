from __future__ import annotations

"""桌宠插件入口：启动 / 停止 QQ 桥接。"""

import sys

_service = None


def setup(api) -> None:
    global _service
    from lfelkeea_qq_bridge.service import QQBridgeService

    _service = QQBridgeService(api)
    _service.start()


def open_settings(api, parent=None):
    """插件管理里的「设置」按钮入口。"""
    service = _service
    if service is None:
        api.say("QQ 桥接还没启动完成，稍后再打开设置。", 4000)
        return None
    from lfelkeea_qq_bridge.ui import QQBridgeDialog

    return QQBridgeDialog(service, parent)


def teardown() -> None:
    global _service
    service = _service
    _service = None
    if service is not None:
        try:
            service.stop()
        except Exception:
            pass
    for name in [
        key
        for key in list(sys.modules)
        if key == "lfelkeea_qq_bridge" or key.startswith("lfelkeea_qq_bridge.")
    ]:
        sys.modules.pop(name, None)
