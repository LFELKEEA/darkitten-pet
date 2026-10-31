from __future__ import annotations

"""聊天图片处理：下载、转 data URI，以及视觉不可用时的本地 OCR 兜底。"""

import base64
import os
import shutil
import subprocess
import tempfile
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote, urlparse

MAX_IMAGES = 3
MAX_BYTES = 6 * 1024 * 1024
OCR_SCRIPT = Path(__file__).resolve().parent.parent / "assets" / "ocr_zh.ps1"


def _mime(data: bytes) -> str:
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"GIF8"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data.startswith(b"BM"):
        return "image/bmp"
    return "image/jpeg"


def read_image(source: str) -> tuple[bytes, str] | None:
    """把 http(s)、file:// 或本地路径读成 (bytes, mime)。"""
    value = str(source or "").strip().strip('"')
    if not value:
        return None
    try:
        if value.startswith(("http://", "https://")):
            request = urllib.request.Request(
                value,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
                    )
                },
            )
            with urllib.request.urlopen(request, timeout=15) as response:
                data = response.read(MAX_BYTES + 1)
        elif value.startswith("file://"):
            parsed = urlparse(value)
            path = Path(unquote(parsed.path))
            if os.name == "nt":
                path = Path(unquote(parsed.path.lstrip("/")))
            data = path.read_bytes()
        else:
            data = Path(value).read_bytes()
    except (OSError, ValueError):
        return None
    if not data or len(data) > MAX_BYTES:
        return None
    return data, _mime(data)


def to_data_uri(data: bytes, mime: str) -> str:
    return f"data:{mime};base64," + base64.b64encode(data).decode("ascii")


def looks_like_vision_rejection(error: str) -> bool:
    text = (error or "").lower()
    return any(
        key in text
        for key in (
            "image",
            "vision",
            "multimodal",
            "content must be a string",
            "invalid content",
            "image_url",
        )
    )


@dataclass
class PreparedImages:
    uris: list[str] = field(default_factory=list)
    blobs: list[tuple[bytes, str]] = field(default_factory=list)
    ocr_text: str = ""
    error: str = ""


def prepare(sources: list[str], *, run_ocr_when_empty: bool = True) -> PreparedImages:
    """下载图片并转成 data URI；全部失败时提前跑一次 OCR。"""
    prepared = PreparedImages()
    for source in list(sources)[:MAX_IMAGES]:
        item = read_image(source)
        if item is None:
            continue
        data, mime = item
        prepared.blobs.append((data, mime))
        prepared.uris.append(to_data_uri(data, mime))
    if not prepared.uris and run_ocr_when_empty and prepared.blobs:
        prepared.ocr_text = ocr_images(prepared.blobs)
    elif not prepared.uris and not prepared.blobs:
        prepared.error = "图片下载失败"
    return prepared


def ocr_images(blobs: list[tuple[bytes, str]], limit: int = 1500) -> str:
    """用 Windows 自带的 OCR 引擎识别图片文字，失败返回空串。"""
    if not blobs or not OCR_SCRIPT.is_file():
        return ""
    temp_dir = Path(tempfile.mkdtemp(prefix="qq_bridge_ocr_"))
    try:
        texts: list[str] = []
        for index, (data, mime) in enumerate(blobs[:2]):
            suffix = "." + (mime.rsplit("/", 1)[-1] or "jpg")
            image_path = temp_dir / f"image_{index}{suffix}"
            output_path = temp_dir / f"text_{index}.txt"
            image_path.write_bytes(data)
            if _run_ocr_script(image_path, output_path):
                text = output_path.read_text(encoding="utf-8", errors="replace").strip()
                if text:
                    texts.append(text)
        return "\n".join(texts)[:limit]
    except OSError:
        return ""
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def _run_ocr_script(image_path: Path, output_path: Path) -> bool:
    powershell = shutil.which("powershell.exe") or "powershell.exe"
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    command = [
        powershell,
        "-NoLogo",
        "-NoProfile",
        "-NonInteractive",
        "-STA",
        "-WindowStyle",
        "Hidden",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(OCR_SCRIPT),
        "-ImagePath",
        str(image_path),
        "-OutputPath",
        str(output_path),
    ]
    try:
        result = subprocess.run(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=25,
            creationflags=flags,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0
