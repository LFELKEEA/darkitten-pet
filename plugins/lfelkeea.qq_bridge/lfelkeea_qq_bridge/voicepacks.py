from __future__ import annotations

"""语音包发现与片段读取。

语音包就是一个装着音频文件的文件夹。SnowLuma 会自己把 mp3/wav/amr 等
格式转成 QQ 需要的 silk，所以这里不需要转码，只负责找文件和挑片段。
"""

import os
import random
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

AUDIO_EXTENSIONS = {
    ".mp3",
    ".wav",
    ".amr",
    ".silk",
    ".ogg",
    ".opus",
    ".m4a",
    ".aac",
    ".flac",
    ".wma",
}

PACK_KEYWORDS = (
    "语音",
    "语音包",
    "音效",
    "喊麦",
    "广播剧",
    "voice",
    "voiceline",
    "vocal",
    "asmr",
)

SKIP_DIR_NAMES = {
    "$recycle.bin",
    ".git",
    ".svn",
    "__pycache__",
    "appdata",
    "build",
    "dist",
    "node_modules",
    "program files",
    "program files (x86)",
    "programdata",
    "site-packages",
    "system volume information",
    "temp",
    "windows",
}

MAX_CLIPS = 2000
MAX_PACKS = 40
MAX_SCAN_DIRS = 5000
MAX_SCAN_SECONDS = 8.0


@dataclass(slots=True)
class VoicePack:
    path: Path
    name: str
    count: int

    @property
    def text(self) -> str:
        return f"{self.name}（{self.count} 条）"


def is_audio_file(path: Path) -> bool:
    try:
        return Path(path).is_file() and Path(path).suffix.lower() in AUDIO_EXTENSIONS
    except OSError:
        return False


def find_clips(folder: str | Path, limit: int = MAX_CLIPS) -> list[Path]:
    """递归找出文件夹里的音频文件，按路径排序。"""
    root = Path(folder).expanduser()
    if not root.is_dir():
        return []
    clips: list[Path] = []
    try:
        for current, dirs, files in os.walk(root):
            dirs[:] = [
                name
                for name in dirs
                if name.lower() not in SKIP_DIR_NAMES and not name.startswith(".")
            ]
            for name in files:
                if Path(name).suffix.lower() not in AUDIO_EXTENSIONS:
                    continue
                clips.append(Path(current) / name)
                if len(clips) >= limit:
                    return sorted(clips)
    except OSError:
        return sorted(clips)
    return sorted(clips)


def _has_keyword(name: str) -> bool:
    text = str(name or "").lower()
    return any(keyword in text for keyword in PACK_KEYWORDS)


def _direct_audio_count(folder: Path) -> int:
    count = 0
    try:
        with os.scandir(folder) as entries:
            for entry in entries:
                try:
                    if entry.is_file() and Path(entry.name).suffix.lower() in AUDIO_EXTENSIONS:
                        count += 1
                        if count >= 3:
                            return count
                except OSError:
                    continue
    except OSError:
        return 0
    return count


def default_roots() -> list[Path]:
    home = Path.home()
    candidates = [
        home / "Downloads",
        home / "Desktop",
        home / "Documents",
        home / "Music",
        home / "Videos",
        home / "OneDrive",
        home / "Documents" / "Tencent Files",
        home / "Documents" / "WeChat Files",
    ]
    roots: list[Path] = []
    for path in candidates:
        try:
            if path.is_dir() and path not in roots:
                roots.append(path)
        except OSError:
            continue
    return roots


def scan_packs(
    roots: Iterable[str | Path] | None = None,
    *,
    max_depth: int = 3,
    max_dirs: int = MAX_SCAN_DIRS,
    max_seconds: float = MAX_SCAN_SECONDS,
    max_packs: int = MAX_PACKS,
) -> list[VoicePack]:
    """在常用目录里找“像语音包”的文件夹。

    判定规则很克制：目录名带语音关键词，或目录里直接有至少 3 个音频文件。
    只扫用户目录和少量固定位置，不碰系统目录，也不会全盘遍历。
    """
    root_list = [Path(item).expanduser() for item in (roots or default_roots())]
    started = time.monotonic()
    visited = 0
    packs: dict[str, VoicePack] = {}

    def timeout() -> bool:
        return (
            time.monotonic() - started >= max_seconds
            or visited >= max_dirs
            or len(packs) >= max_packs
        )

    def walk(folder: Path, depth: int) -> None:
        nonlocal visited
        if timeout():
            return
        visited += 1
        try:
            if not folder.is_dir():
                return
            direct = _direct_audio_count(folder)
            keyword = _has_keyword(folder.name)
            if direct >= 3 or (keyword and direct >= 1):
                clips = find_clips(folder, limit=max(1, min(MAX_CLIPS, direct + 200)))
                if clips:
                    key = str(folder.resolve()).lower()
                    packs[key] = VoicePack(
                        path=folder.resolve(), name=folder.name, count=len(clips)
                    )
            if depth >= max_depth:
                return
            with os.scandir(folder) as entries:
                children = []
                for entry in entries:
                    try:
                        if not entry.is_dir(follow_symlinks=False):
                            continue
                    except OSError:
                        continue
                    name = entry.name
                    if name.startswith(".") or name.lower() in SKIP_DIR_NAMES:
                        continue
                    children.append(Path(entry.path))
            for child in sorted(children, key=lambda item: item.name.lower()):
                if timeout():
                    return
                walk(child, depth + 1)
        except OSError:
            return

    for root in root_list:
        if timeout():
            break
        walk(root, 0)

    ordered = sorted(
        packs.values(),
        key=lambda item: (_has_keyword(item.name), item.count, item.name.lower()),
        reverse=True,
    )
    # 如果父目录已经被识别成语音包，就不再重复列它里面的子文件夹。
    result: list[VoicePack] = []
    for pack in ordered[:max_packs]:
        if any(
            parent.path != pack.path and parent.path in pack.path.parents
            for parent in result
        ):
            continue
        result.append(pack)
    return result


def choose_clip(folder: str | Path, rng: random.Random | None = None) -> Path | None:
    clips = find_clips(folder)
    if not clips:
        return None
    return (rng or random).choice(clips)
