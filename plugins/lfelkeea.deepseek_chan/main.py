from __future__ import annotations

"""桌宠插件入口：深度求索娘（大肥鱼）皮肤。"""

import sys

_service = None


def setup(api) -> None:
    global _service
    from deepseek_chan.service import DeepSeekChanService

    _service = DeepSeekChanService(api)
    _service.start()


def open_settings(api, parent=None):
    service = _service
    if service is None:
        api.say("皮肤还没启动完成，稍后再打开设置。", 4000)
        return None
    from deepseek_chan.ui import DeepSeekChanDialog

    return DeepSeekChanDialog(service, parent)


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
        if key == "deepseek_chan" or key.startswith("deepseek_chan.")
    ]:
        sys.modules.pop(name, None)
