"""在本机寻找 Live2D 模型，必要时从用户自己的压缩包里解出来。

默认仍然优先找「大肥鱼」；找不到时会扫描常见目录（包括 D:\\模型），
也支持手动选择任意 .model3.json、模型文件夹、ZIP / RAR / 7Z。

模型有明确的使用约束：不得把模型文件重新上传到网盘或素材站。
所以插件包里不带任何 moc3 / 贴图，只做本机定位和解包。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

MODEL_NAME = "大肥鱼.model3.json"
MODEL_DIR_NAME = "deepseek_chan"

ARCHIVE_SUFFIXES = (".zip", ".rar", ".7z")
MODEL_SUFFIX = ".model3.json"

# 只解运行需要的文件，跳过 PSD、预览图之类的大家伙。
KEEP_SUFFIXES = (
    ".moc3",
    ".model3.json",
    ".physics3.json",
    ".cdi3.json",
    ".pose3.json",
    ".userdata3.json",
    ".motion3.json",
    ".exp3.json",
    ".png",
    ".txt",
)
SKIP_DIRS = {"psd", "__macosx", "__pycache__"}

# 名字里带这些词的压缩包才参与自动发现，避免把驱动包当成模型。
MODEL_NAME_HINTS = (
    "deepseek",
    "大肥鱼",
    "公皮",
    "模型",
    "model",
    "live2d",
    "皮肤",
    "motion",
)


@dataclass
class ModelLocation:
    model: Path | None = None
    directory: Path | None = None
    archive: Path | None = None
    source: str = ""

    @property
    def ready(self) -> bool:
        return self.model is not None and self.model.is_file()

    @property
    def zip_path(self) -> Path | None:
        """兼容旧字段：只对 ZIP 返回路径。"""
        if self.archive is not None and self.archive.suffix.lower() == ".zip":
            return self.archive
        return None

    @property
    def label(self) -> str:
        if self.model is not None:
            stem = self.model.name[: -len(MODEL_SUFFIX)]
            if stem and stem != MODEL_NAME[: -len(MODEL_SUFFIX)]:
                return stem
            return self.model.parent.name
        if self.archive is not None:
            return self.archive.stem
        if self.directory is not None:
            return self.directory.name
        return ""


def is_archive(path: Path) -> bool:
    return Path(path).suffix.lower() in ARCHIVE_SUFFIXES


def find_model_file(root: Path, *, max_depth: int = 6) -> Path | None:
    """在目录里找模型文件，优先同名「大肥鱼」。"""
    root = Path(root)
    if root.is_file():
        return root if root.name.lower().endswith(MODEL_SUFFIX) else None
    if not root.is_dir():
        return None
    exact = root / MODEL_NAME
    if exact.is_file():
        return exact

    found: list[Path] = []
    base_depth = len(root.parts)
    for dirpath, dirnames, filenames in os.walk(root):
        current = Path(dirpath)
        if len(current.parts) - base_depth >= max_depth:
            dirnames[:] = []
        dirnames[:] = [name for name in dirnames if name.lower() not in SKIP_DIRS]
        for name in filenames:
            if name.lower().endswith(MODEL_SUFFIX):
                found.append(current / name)
    if not found:
        return None
    found.sort(key=lambda path: (path.name != MODEL_NAME, len(path.parts), str(path).lower()))
    return found[0]


def _candidate_user_dirs() -> list[Path]:
    home = Path.home()
    dirs = [
        home / "Downloads",
        home / "Desktop",
        home / "Documents",
        home / "下载",
        home / "桌面",
        Path("D:/模型"),
        Path("D:/models"),
        Path("D:/Live2D"),
        Path("D:/live2d"),
        Path("D:/"),
        Path("E:/"),
    ]
    result: list[Path] = []
    seen: set[str] = set()
    for path in dirs:
        try:
            key = str(path.resolve()).lower()
        except OSError:
            key = str(path).lower()
        if key not in seen:
            seen.add(key)
            result.append(path)
    return result


def _legacy_candidates() -> list[Path]:
    home = Path.home()
    return [
        Path("D:/DeepSeek免费公皮.zip"),
        Path("D:/DeepSeek免费公皮"),
        Path("D:/模型/DeepSeek免费公皮.zip"),
        Path("D:/模型/DeepSeek免费公皮"),
        home / "Downloads" / "DeepSeek免费公皮.zip",
        home / "Downloads" / "DeepSeek免费公皮",
        home / "Desktop" / "DeepSeek免费公皮.zip",
        home / "Desktop" / "DeepSeek免费公皮",
        home / "下载" / "DeepSeek免费公皮.zip",
        home / "桌面" / "DeepSeek免费公皮.zip",
    ]


def _looks_like_model_name(name: str) -> bool:
    lowered = str(name).lower()
    return any(hint in lowered for hint in MODEL_NAME_HINTS)


def _archive_has_model(path: Path) -> bool:
    if path.suffix.lower() != ".zip":
        return _looks_like_model_name(path.name)
    try:
        with zipfile.ZipFile(path) as archive:
            return any(item.filename.lower().endswith(MODEL_SUFFIX) for item in archive.infolist())
    except (OSError, zipfile.BadZipFile):
        return False


def _classify(path: Path, source: str, *, max_depth: int = 6) -> ModelLocation | None:
    try:
        if path.is_file():
            if is_archive(path):
                return ModelLocation(archive=path, source=source)
            if path.name.lower().endswith(MODEL_SUFFIX):
                return ModelLocation(model=path, source=source)
            return None
        if path.is_dir():
            model = find_model_file(path, max_depth=max_depth)
            if model is not None:
                return ModelLocation(model=model, directory=path, source=source)
            try:
                archives = sorted(
                    item
                    for item in path.iterdir()
                    if item.is_file() and is_archive(item) and _archive_has_model(item)
                )
            except OSError:
                archives = []
            if archives:
                return ModelLocation(archive=archives[0], source=source)
    except OSError:
        return None
    return None


def _scan(search_dirs: list[Path] | tuple[Path, ...] | None = None) -> list[ModelLocation]:
    locations: list[ModelLocation] = []
    seen: set[str] = set()

    def add(location: ModelLocation | None) -> None:
        if location is None:
            return
        target = location.model or location.archive or location.directory
        if target is None:
            return
        try:
            key = str(Path(target).resolve()).lower()
        except OSError:
            key = str(target).lower()
        if key in seen:
            return
        seen.add(key)
        locations.append(location)

    roots = _candidate_user_dirs() if search_dirs is None else [Path(item) for item in search_dirs]
    for root in roots:
        if not root.is_dir():
            continue
        try:
            children = list(root.iterdir())
        except OSError:
            continue
        # 先看目录本身，再逐个看子项，兼容「直接选到模型目录」和「模型在子目录」。
        if _looks_like_model_name(root.name) or (root / MODEL_NAME).is_file():
            add(_classify(root, "自动发现", max_depth=3))
        for child in children:
            try:
                if child.is_file():
                    if is_archive(child) and _archive_has_model(child):
                        add(_classify(child, "自动发现"))
                elif child.is_dir():
                    # 普通目录只看一两层；名字像模型目录的才继续往下找。
                    depth = 4 if _looks_like_model_name(child.name) else 1
                    location = _classify(child, "自动发现", max_depth=depth)
                    if location is not None and (location.ready or location.archive is not None):
                        add(location)
            except OSError:
                continue
    return locations


def discover_models(settings: dict) -> list[ModelLocation]:
    """返回本机发现的所有模型，供设置界面展示。手动选择永远排在最前。"""
    result: list[ModelLocation] = []

    def add(location: ModelLocation | None) -> None:
        if location is None:
            return
        target = location.model or location.archive or location.directory
        if target is None:
            return
        try:
            key = str(Path(target).resolve()).lower()
        except OSError:
            key = str(target).lower()
        if any(
            str(Path(item.model or item.archive or item.directory).resolve()).lower() == key
            for item in result
            if (item.model or item.archive or item.directory) is not None
        ):
            return
        result.append(location)

    manual = str(settings.get("model_path") or "").strip()
    if manual:
        add(_classify(Path(manual).expanduser(), "手动指定"))
    manual_zip = str(settings.get("zip_path") or "").strip()
    if manual_zip:
        add(_classify(Path(manual_zip).expanduser(), "手动指定压缩包"))
    env_path = str(os.environ.get("DEEPSEEK_CHAN_MODEL") or "").strip()
    if env_path:
        add(_classify(Path(env_path).expanduser(), "环境变量"))

    for path in _legacy_candidates():
        add(_classify(path, "自动发现"))

    for location in _scan(settings.get("search_dirs")):
        add(location)
    return result


def locate(settings: dict) -> ModelLocation | None:
    """快速定位：不复制、不解包，返回模型 / 目录 / 压缩包。"""
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

    for path in _legacy_candidates():
        loc = _classify(path, "自动发现")
        if loc is not None:
            return loc

    candidates = _scan(settings.get("search_dirs"))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (0 if _looks_like_model_name(item.label) else 1, item.label))
    return candidates[0]


def _slug(name: str) -> str:
    cleaned = re.sub(r"[^\w\u4e00-\u9fff.-]+", "_", str(name)).strip("._ ")
    return cleaned[:48] or "model"


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


def _should_keep(relative: Path) -> bool:
    parts = relative.parts
    if not parts:
        return False
    if any(part in (".", "..") or part.lower() in SKIP_DIRS for part in parts[:-1]):
        return False
    return relative.name.lower().endswith(KEEP_SUFFIXES)


def _copy_model_tree(base: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    for path in base.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(base)
        if not _should_keep(relative):
            continue
        target = dest.joinpath(*relative.parts)
        if not _inside(dest, target):
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)


def _zip_contents(zip_path: Path) -> tuple[zipfile.ZipFile, zipfile.ZipInfo, str]:
    archive = zipfile.ZipFile(zip_path)
    infos = archive.infolist()
    model_info = next(
        (item for item in infos if item.filename.endswith(MODEL_NAME)),
        None,
    )
    if model_info is None:
        model_info = next(
            (item for item in infos if item.filename.lower().endswith(MODEL_SUFFIX)),
            None,
        )
    if model_info is None:
        archive.close()
        raise ValueError("压缩包里没有找到 .model3.json")
    base = model_info.filename.rsplit("/", 1)[0] + "/"
    return archive, model_info, base


def _extract_zip_to(zip_path: Path, dest: Path, status=None) -> Path:
    archive, _model_info, base = _zip_contents(zip_path)
    try:
        for item in archive.infolist():
            name = item.filename
            if item.is_dir() or not name.startswith(base):
                continue
            relative = Path(name[len(base):])
            if not _should_keep(relative):
                continue
            target = dest.joinpath(*relative.parts)
            if not _inside(dest, target):
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(item) as source, open(target, "wb") as handle:
                shutil.copyfileobj(source, handle, 1024 * 1024)
    finally:
        archive.close()
    model = find_model_file(dest)
    if model is None:
        raise ValueError("解包完成，但没有找到模型文件")
    return model


@lru_cache(maxsize=8)
def _find_extractor(suffix: str) -> str:
    """找一个能解 RAR / 7Z 的外部工具；找不到返回空字符串。"""
    names = ["7z", "7za"] if suffix == ".7z" else ["7z", "7za", "unrar"]
    for name in names:
        found = shutil.which(name)
        if found:
            return found

    candidates: list[Path] = []
    roots = [
        Path("C:/Program Files"),
        Path("C:/Program Files (x86)"),
        Path("D:/Program Files"),
        Path("D:/Program Files (x86)"),
        Path(os.environ.get("LOCALAPPDATA") or ".") / "Programs",
    ]
    for root in roots:
        if not str(root) or not root.is_dir():
            continue
        for pattern in ("7-Zip/7z.exe", "WinRAR/UnRAR.exe", "WinRAR/WinRAR.exe"):
            candidates.append(root / pattern)
        # 有些程序会把 7z.exe 塞在自己的子目录里，例如 NVIDIA App。
        try:
            candidates.extend(root.glob("*/7z.exe"))
            candidates.extend(root.glob("*/*/7z.exe"))
        except OSError:
            continue
    for candidate in candidates:
        try:
            if candidate.is_file():
                return str(candidate)
        except OSError:
            continue
    return ""


def _external_extract(archive: Path, temp: Path) -> None:
    tool = _find_extractor(archive.suffix.lower())
    if not tool:
        raise ValueError(
            "没有找到解压 RAR/7Z 的工具。请先用 7-Zip / WinRAR 解压成文件夹，"
            "再在插件设置里选择模型文件夹。"
        )
    name = Path(tool).name.lower()
    if name in {"7z.exe", "7z", "7za.exe", "7za"}:
        command = [tool, "x", "-y", "-bd", "-bso0", "-bsp0", f"-o{temp}", str(archive)]
    else:
        command = [tool, "x", "-y", str(archive), str(temp) + os.sep]
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            timeout=300,
            creationflags=flags,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError(f"调用解压工具失败：{exc}") from exc
    if result.returncode not in (0, 1):
        detail = (result.stderr or result.stdout or b"").decode("utf-8", "replace").strip()
        raise ValueError(f"解压失败（{result.returncode}）：{detail[:300]}")


def extract_archive(archive: Path, data_dir: Path, *, status=None) -> Path:
    """把压缩包里的模型目录解到 data_dir/models/<模型名>。"""
    archive = Path(archive)
    if not archive.is_file():
        raise ValueError(f"压缩包不存在：{archive}")
    if not is_archive(archive):
        raise ValueError(f"不支持的压缩格式：{archive.suffix}")

    models_root = Path(data_dir) / "models"
    models_root.mkdir(parents=True, exist_ok=True)
    signature = _signature(archive)

    if archive.suffix.lower() == ".zip":
        with zipfile.ZipFile(archive) as handle:
            model_name = next(
                (
                    item.filename.rsplit("/", 1)[-1]
                    for item in handle.infolist()
                    if item.filename.endswith(MODEL_NAME)
                ),
                "",
            )
            if not model_name:
                model_name = next(
                    (
                        item.filename.rsplit("/", 1)[-1]
                        for item in handle.infolist()
                        if item.filename.lower().endswith(MODEL_SUFFIX)
                    ),
                    "",
                )
        if not model_name:
            raise ValueError("压缩包里没有找到 .model3.json")
        slug = MODEL_DIR_NAME if model_name == MODEL_NAME else f"model_{_slug(model_name[: -len(MODEL_SUFFIX)])}"
        dest = models_root / slug
        marker = dest / ".source.json"
        existing = find_model_file(dest)
        if existing is not None and _read_json(marker) == signature:
            return existing
        if status:
            status("正在从压缩包解出模型…")
        shutil.rmtree(dest, ignore_errors=True)
        model = _extract_zip_to(archive, dest, status)
        _write_json(marker, signature)
        return model

    # RAR / 7Z：先解到临时目录，再按白名单搬运。找不到工具时直接给出可执行提示。
    if status:
        status("正在从压缩包解出模型…")
    temp = Path(tempfile.mkdtemp(prefix=".extract-", dir=str(models_root)))
    try:
        _external_extract(archive, temp)
        source_model = find_model_file(temp)
        if source_model is None:
            raise ValueError("压缩包里没有找到 .model3.json")
        stem = source_model.name[: -len(MODEL_SUFFIX)]
        slug = MODEL_DIR_NAME if source_model.name == MODEL_NAME else f"model_{_slug(stem)}"
        dest = models_root / slug
        marker = dest / ".source.json"
        existing = find_model_file(dest)
        if existing is not None and _read_json(marker) == signature:
            return existing
        shutil.rmtree(dest, ignore_errors=True)
        _copy_model_tree(source_model.parent, dest)
        model = find_model_file(dest)
        if model is None:
            raise ValueError("解包完成，但没有找到模型文件")
        _write_json(marker, signature)
        return model
    finally:
        shutil.rmtree(temp, ignore_errors=True)


def extract_zip(zip_path: Path, data_dir: Path, *, status=None) -> Path:
    """兼容旧调用：解包 ZIP；传 RAR/7Z 时会自动走通用逻辑。"""
    return extract_archive(zip_path, data_dir, status=status)


def resolve(settings: dict, data_dir: Path) -> Path | None:
    location = locate(settings)
    if location is None:
        return None
    if location.ready:
        return location.model
    if location.archive is not None:
        return extract_archive(location.archive, data_dir)
    if location.directory is not None:
        return find_model_file(location.directory)
    return None


def watermark_switch(model_dir: Path | None) -> dict[str, float]:
    """读取模型作者自带的水印开关（shuiyin*.exp3.json）。

    很多免费 Live2D 模型把水印做成了独立表情，参数值 30 表示隐藏。
    这里只读取作者提供的开关，不猜参数名，也不改动模型文件。
    """
    params: dict[str, float] = {}
    if model_dir is None:
        return params
    directory = Path(model_dir)
    if not directory.is_dir():
        return params
    paths: list[Path] = []
    for pattern in ("shuiyin*.exp3.json", "*水印*.exp3.json"):
        for path in sorted(directory.glob(pattern)):
            if path not in paths:
                paths.append(path)
    for path in paths:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        for item in data.get("Parameters") or []:
            if not isinstance(item, dict):
                continue
            param_id = str(item.get("Id") or "").strip()
            if not param_id:
                continue
            try:
                value = float(item.get("Value"))
            except (TypeError, ValueError):
                continue
            params[param_id] = value
    return params
