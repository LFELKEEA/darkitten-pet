from __future__ import annotations

"""路径解析：让插件在源码运行、PyInstaller 安装版、任意盘符下都能找到 data/。

解析顺序：
1. 环境变量 ``LFELKEEA_DATA_DIR``（测试和高级用户手动指定）；
2. 宿主已加载的 ``app_paths.DATA_DIR``（桌宠运行时一定有）；
3. 从插件自身位置反推 ``<data>/plugins/<id>``；
4. 源码布局 ``desktop_pet/plugins/<id>`` → ``desktop_pet/data``。

除此之外不依赖任何绝对路径，复制到别人的电脑上也能正常工作。
"""

import os
import sys
from dataclasses import dataclass
from pathlib import Path

PLUGIN_FOLDER = Path(__file__).resolve().parent.parent

WORKSPACE_README = """# LFELKEEA 自开发区

这里是桌宠自己写代码的地方，由「自开发工坊」自动创建。

- `workspace/`：自由项目区。所有相对路径都以它为根，工具不能跑到它外面。
- `drafts/`：插件草稿。还没安装到 `data/plugins/` 的代码放这里。
- `sessions/`：每次自开发任务的步骤日志（JSONL，方便回看）。
- `.backups/`：覆盖文件、更新插件之前的自动备份。

安全边界：
工具只接受相对路径；写文件前会备份旧版本；执行命令和启用新插件默认需要在工作台里点「允许」。
"""


def _host_data_dir() -> Path | None:
    """优先使用宿主已经算好的数据目录，避免重复推断。"""
    module = sys.modules.get("app_paths")
    value = getattr(module, "DATA_DIR", None) if module is not None else None
    if value:
        return Path(value)
    try:  # 插件被单独加载时（例如测试）再尝试一次导入
        from app_paths import DATA_DIR  # noqa: PLC0415

        return Path(DATA_DIR)
    except Exception:
        return None


def _looks_like_data(path: Path) -> bool:
    try:
        if path.name.lower() == "data":
            return True
        return (path / "config.json").is_file()
    except OSError:
        return False


def discover_data_dir(plugin_folder: Path | str | None = None) -> Path:
    env = str(os.environ.get("LFELKEEA_DATA_DIR", "") or "").strip()
    if env:
        return Path(env).expanduser().resolve()

    host = _host_data_dir()
    if host is not None:
        return host.resolve()

    folder = Path(plugin_folder or PLUGIN_FOLDER).resolve()
    parent = folder.parent.parent
    if _looks_like_data(parent):
        return parent
    source = parent / "data"
    if _looks_like_data(source) or source.is_dir():
        return source.resolve()
    return parent


@dataclass(frozen=True)
class DevPaths:
    data_dir: Path
    plugin_folder: Path
    dev_dir: Path
    workspace: Path
    drafts: Path
    sessions: Path
    backups: Path
    plugins_dir: Path
    config_path: Path
    references: Path

    @classmethod
    def discover(
        cls,
        workspace_setting: str = "",
        plugin_folder: Path | str | None = None,
    ) -> "DevPaths":
        folder = Path(plugin_folder or PLUGIN_FOLDER).resolve()
        data_dir = discover_data_dir(folder)
        dev_dir = data_dir / "dev"
        text = str(workspace_setting or "").strip()
        if text:
            workspace = Path(text).expanduser()
            if not workspace.is_absolute():
                workspace = dev_dir / workspace
            workspace = workspace.resolve()
        else:
            workspace = (dev_dir / "workspace").resolve()
        return cls(
            data_dir=data_dir,
            plugin_folder=folder,
            dev_dir=dev_dir,
            workspace=workspace,
            drafts=(dev_dir / "drafts").resolve(),
            sessions=(dev_dir / "sessions").resolve(),
            backups=(dev_dir / ".backups").resolve(),
            plugins_dir=(data_dir / "plugins").resolve(),
            config_path=(data_dir / "config.json").resolve(),
            references=(folder / "references").resolve(),
        )

    def ensure(self) -> "DevPaths":
        for path in (self.dev_dir, self.workspace, self.drafts, self.sessions, self.backups):
            path.mkdir(parents=True, exist_ok=True)
        readme = self.dev_dir / "README.md"
        if not readme.exists():
            try:
                readme.write_text(WORKSPACE_README, encoding="utf-8")
            except OSError:
                pass
        return self
