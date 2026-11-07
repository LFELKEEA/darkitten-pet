"""在本机寻找「大肥鱼」模型，必要时从用户自己的 ZIP 里解出来。

模型有明确的使用约束：不得把模型文件重新上传到网盘或素材站。
所以插件包里不带任何 moc3 / 贴图，只做本机定位和解包。
"""

from __future__ import annotations

import json
import os
import shutil
import zipfile
from dataclasses import dataclass
from pathlib import Path

MODEL_NAME = "大肥鱼.model3.json"
MODEL_DIR_NAME = "deepseek_chan"

# 只解运行需要的文件，跳过 PSD、预览图之类的大家伙。
KEEP_SUFFIXES = (
    ".moc3",
    ".model3.json",
    ".physics3.json",
    ".cdi3.json",
    ".motion3.json",
    ".exp3.json",
    ".png",
    ".txt",
)
SKIP_DIRS = {"psd", "__macosx"}


@dataclass
class ModelLocation:
    model: Path | None = None
    directory: Path | None = None
    zip_path: Path | None = None
    source: str = ""

    @property
    def ready(self) -> bool:
        return self.model is not None and self.model.is_file()


def find_model_file(root: Path) -> Path | None:
    root = Path(root)
    if root.is_file():
        return root if root.suffix.lower() == ".json" and root.name.endswith(".model3.json") else None
    if not root.is_dir():
        return None
    exact = root / MODEL_NAME
    if exact.is_file():
        return exact
    preferred = sorted(root.rglob("*.model3.json"), key=lambda p: (p.name != MODEL_NAME, len(p.parts)))
    if preferred:
        return preferred[0]
    return None


def _candidate_user_dirs() -> list[Path]:
    home = Path.home()
    return [
        home / "Downloads",
        home / "Desktop",
        home / "Documents",
        home / "下载",
        home / "桌面",
        Path("D:/"),
        Path("E:/"),
    ]


def locate(settings: dict) -> ModelLocation | None:
    """快速定位：不复制、不解包，返回模型 / 目录 / ZIP。"""
    manual = str(settings.get("model_path") or "").strip()
    if manual:
        loc = _classify(Path(manual).expanduser(), "手动指定")
        if loc is not None:
            return loc

    manual_zip = str(settings.get("zip_path") or "").strip()
    if manual_zip:
        loc = _classify(Path(manual_zip).expanduser(), "手动指定压缩包")
        if loc is not None:
            return loc

    env_path = str(os.environ.get("DEEPSEEK_CHAN_MODEL") or "").strip()
    if env_path:
        loc = _classify(Path(env_path).expanduser(), "环境变量 DEEPSEEK_CHAN_MODEL")
        if loc is not None:
            return loc

    # 常见的直接路径
    home = Path.home()
    direct = [
        Path("D:/DeepSeek免费公皮.zip"),
        Path("D:/DeepSeek免费公皮"),
        home / "Downloads" / "DeepSeek免费公皮.zip",
        home / "Downloads" / "DeepSeek免费公皮",
        home / "Desktop" / "DeepSeek免费公皮.zip",
        home / "Desktop" / "DeepSeek免费公皮",
        home / "下载" / "DeepSeek免费公皮.zip",
        home / "桌面" / "DeepSeek免费公皮.zip",
    ]
    for path in direct:
        loc = _classify(path, "自动发现")
        if loc is not None:
            return loc

    # 只看用户目录和磁盘根目录的一层，避免全盘扫描。
    for root in _candidate_user_dirs():
        if not root.is_dir():
            continue
        try:
            children = list(root.iterdir())
        except OSError:
            continue
        for child in children:
            name = child.name.lower()
            if "deepseek" not in name and "大肥鱼" not in name and "公皮" not in name:
                continue
            loc = _classify(child, "自动发现")
            if loc is not None:
                return loc
    return None


def _classify(path: Path, source: str) -> ModelLocation | None:
    try:
        if path.is_file():
            if path.suffix.lower() == ".zip":
                return ModelLocation(zip_path=path, source=source)
            if path.name.endswith(".model3.json"):
                return ModelLocation(model=path, source=source)
            return None
        if path.is_dir():
            model = find_model_file(path)
            if model is not None:
                return ModelLocation(model=model, directory=path, source=source)
            try:
                zips = sorted(path.glob("*.zip"))
            except OSError:
                zips = []
            if zips:
                return ModelLocation(zip_path=zips[0], source=source)
    except OSError:
        return None
    return None


def extract_zip(zip_path: Path, data_dir: Path, *, status=None) -> Path:
    """把 ZIP 里模型所在目录解到 data_dir/models/deepseek_chan。"""
    zip_path = Path(zip_path)
    dest = Path(data_dir) / "models" / MODEL_DIR_NAME
    signature = _signature(zip_path)
    marker = dest / ".source.json"
    existing = find_model_file(dest)
    if existing is not None and _read_json(marker) == signature:
        return existing

    if status:
        status("正在从压缩包解出模型…")
    dest.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(zip_path) as archive:
        infos = archive.infolist()
        model_info = next(
            (item for item in infos if item.filename.endswith(MODEL_NAME)),
            None,
        )
        if model_info is None:
            model_info = next(
                (item for item in infos if item.filename.endswith(".model3.json")),
                None,
            )
        if model_info is None:
            raise ValueError("压缩包里没有找到 .model3.json")
        base = model_info.filename.rsplit("/", 1)[0] + "/"

        for item in infos:
            name = item.filename
            if item.is_dir() or not name.startswith(base):
                continue
            relative = name[len(base):]
            parts = [part for part in relative.split("/") if part]
            if not parts or any(part in (".", "..") for part in parts):
                continue
            if any(part.lower() in SKIP_DIRS for part in parts[:-1]):
                continue
            if not relative.lower().endswith(KEEP_SUFFIXES):
                continue
            target = dest.joinpath(*parts)
            if not _inside(dest, target):
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(item) as source, open(target, "wb") as handle:
                shutil.copyfileobj(source, handle, 1024 * 1024)

    model = find_model_file(dest)
    if model is None:
        raise ValueError("解包完成，但没有找到模型文件")
    _write_json(marker, signature)
    if status:
        status("模型解包完成")
    return model


def resolve(settings: dict, data_dir: Path) -> Path | None:
    location = locate(settings)
    if location is None:
        return None
    if location.ready:
        return location.model
    if location.zip_path is not None:
        return extract_zip(location.zip_path, data_dir)
    if location.directory is not None:
        return find_model_file(location.directory)
    return None


def _signature(path: Path) -> dict:
    stat = path.stat()
    return {
        "path": str(path.resolve()),
        "size": int(stat.st_size),
        "mtime": int(stat.st_mtime),
    }


def _inside(base: Path, target: Path) -> bool:
    try:
        target.resolve().relative_to(base.resolve())
        return True
    except (OSError, ValueError):
        return False


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_json(path: Path, value: dict) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass
