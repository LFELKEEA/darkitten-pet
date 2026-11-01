from __future__ import annotations

"""桌宠插件入口：启动 / 停止活人感内核。"""

import sys

_service = None


def setup(api) -> None:
    global _service
    from lfelkeea_alive_core.service import AliveService

    _service = AliveService(api)
    _service.start()


def open_settings(api, parent=None):
    service = _service
    if service is None:
        api.say("活人感内核还没启动完成，稍后再打开设置。", 4000)
        return None
    from lfelkeea_alive_core.ui import AliveDialog

    return AliveDialog(service, parent)


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
        if key == "lfelkeea_alive_core" or key.startswith("lfelkeea_alive_core.")
    ]:
        sys.modules.pop(name, None)
