from __future__ import annotations

"""桌宠插件入口：自开发工坊。"""

import sys

_service = None
_dialog = None


def setup(api) -> None:
    global _service
    from lfelkeea_self_dev.service import DevService

    _service = DevService(api)
    api.log("自开发工坊已启动（数据目录：%s）" % _service.paths.data_dir)


def open_settings(api, parent=None):
    global _dialog
    service = _service
    if service is None:
        api.say("自开发工坊还没准备好，看一下插件日志吧。", 4000)
        return None
    from lfelkeea_self_dev.ui import DevStudioDialog

    _dialog = DevStudioDialog(service, parent)
    return _dialog


def teardown() -> None:
    global _service, _dialog
    service = _service
    _service = None
    _dialog = None
    if service is not None:
        try:
            service.shutdown()
        except Exception:
            pass
    for name in [
        key
        for key in list(sys.modules)
        if key == "lfelkeea_self_dev" or key.startswith("lfelkeea_self_dev.")
    ]:
        sys.modules.pop(name, None)
