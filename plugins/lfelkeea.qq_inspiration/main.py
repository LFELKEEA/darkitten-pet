"""灵感获取（QQ桥接 / 桌宠对话）入口。"""

from __future__ import annotations

import sys

# 插件升级/热重载时，宿主不会清掉依赖包；这里主动丢掉旧模块，确保加载新代码。
for _name in [
    name
    for name in sys.modules
    if name == "lfelkeea_qq_inspiration" or name.startswith("lfelkeea_qq_inspiration.")
]:
    sys.modules.pop(_name, None)

from lfelkeea_qq_inspiration.service import open_settings, setup, teardown  # noqa: E402

__all__ = ["setup", "teardown", "open_settings"]

