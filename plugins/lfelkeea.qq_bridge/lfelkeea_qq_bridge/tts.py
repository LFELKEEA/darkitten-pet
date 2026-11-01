from __future__ import annotations

"""本地 TTS 合成：Windows 系统语音和 GPT-SoVITS HTTP API。

插件只负责把文字交给 TTS 引擎、把返回的 WAV 交给 SnowLuma，不包含
GPT-SoVITS，也不附带任何音色数据。参考音频和模型由用户自行准备。
"""

import os
import re
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


class TTSError(RuntimeError):
    pass


_CLEAN_PATTERNS = (
    re.compile(r"\[(?:face|mface|表情|图片|语音|voice|silent|quiet)[^\]]*\]", re.IGNORECASE),
    re.compile(r"https?://\S+", re.IGNORECASE),
    re.compile(r"[*_#`>]+"),
)


def clean_text(value: str, limit: int = 300) -> str:
    """去掉表情标记、链接和 Markdown 符号，只保留能读出来的文字。"""
    text = str(value or "")
    for pattern in _CLEAN_PATTERNS:
        text = pattern.sub(" ", text)
    text = re.sub(r"\s+", " ", text).strip()
    try:
        size = max(1, min(1000, int(limit)))
    except (TypeError, ValueError):
        size = 300
    return text[:size].strip()


def _powershell() -> str:
    for name in ("powershell.exe", "powershell"):
        found = shutil.which(name)
        if found:
            return found
    raise TTSError("找不到 PowerShell，无法使用 Windows 本地语音。")


def _creation_flags() -> int:
    return int(getattr(subprocess, "CREATE_NO_WINDOW", 0)) if os.name == "nt" else 0


def _run_script(script: str, args: list[str], timeout: int) -> subprocess.CompletedProcess:
    with tempfile.TemporaryDirectory(prefix="darkitten-tts-") as tmp:
        path = Path(tmp) / "tts.ps1"
        # Windows PowerShell 5.1 靠 BOM 判断 UTF-8，这里必须用 utf-8-sig。
        path.write_text(script, encoding="utf-8-sig")
        command = [
            _powershell(),
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(path),
            *args,
        ]
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=max(5, int(timeout)),
                creationflags=_creation_flags(),
            )
        except subprocess.TimeoutExpired as exc:
            raise TTSError("TTS 合成超时。") from exc
        except OSError as exc:
            raise TTSError(f"启动 PowerShell 失败：{exc}") from exc
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()
            raise TTSError(detail[-400:] or "PowerShell 执行失败。")
        return result


_VOICE_LIST_SCRIPT = r"""
param([string]$OutPath)
$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Speech
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
try {
    $names = @($synth.GetInstalledVoices() | ForEach-Object { $_.VoiceInfo.Name })
    [IO.File]::WriteAllLines($OutPath, $names, [Text.Encoding]::UTF8)
} finally {
    $synth.Dispose()
}
"""


def windows_voices(timeout: int = 20) -> list[str]:
    """列出 Windows 已安装的 SAPI 语音。"""
    with tempfile.TemporaryDirectory(prefix="darkitten-voices-") as tmp:
        out = Path(tmp) / "voices.txt"
        _run_script(_VOICE_LIST_SCRIPT, [str(out)], timeout)
        try:
            lines = out.read_text(encoding="utf-8-sig").splitlines()
        except OSError as exc:
            raise TTSError(f"读取语音列表失败：{exc}") from exc
    return [line.strip() for line in lines if line.strip()]


_WINDOWS_TTS_SCRIPT = r"""
param(
    [string]$TextPath,
    [string]$OutPath,
    [string]$Voice,
    [int]$Rate,
    [int]$Volume
)
$ErrorActionPreference = "Stop"
$text = [IO.File]::ReadAllText($TextPath, [Text.Encoding]::UTF8)
Add-Type -AssemblyName System.Speech
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
try {
    if ($Voice) {
        $synth.SelectVoice($Voice)
    }
    $synth.Rate = [Math]::Max(-10, [Math]::Min(10, $Rate))
    $synth.Volume = [Math]::Max(0, [Math]::Min(100, $Volume))
    if (Test-Path -LiteralPath $OutPath) {
        Remove-Item -LiteralPath $OutPath -Force
    }
    $synth.SetOutputToWaveFile($OutPath)
    $synth.Speak($text)
} finally {
    $synth.Dispose()
}
"""


def synthesize_windows(
    text: str,
    out_path: str | Path,
    *,
    voice: str = "",
    rate: int = 0,
    volume: int = 100,
    timeout: int = 90,
) -> Path:
    """用 Windows SAPI 合成 WAV。"""
    value = clean_text(text, 1000)
    if not value:
        raise TTSError("没有可合成的文字。")
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="darkitten-tts-text-") as tmp:
        text_path = Path(tmp) / "text.txt"
        text_path.write_text(value, encoding="utf-8")
        _run_script(
            _WINDOWS_TTS_SCRIPT,
            [
                str(text_path),
                str(out),
                str(voice or ""),
                str(max(-10, min(10, int(rate)))),
                str(max(0, min(100, int(volume)))),
            ],
            timeout,
        )
    if not out.is_file() or out.stat().st_size < 100:
        raise TTSError("Windows 语音没有生成有效的 WAV 文件。")
    return out


def gpt_sovits_endpoint(base_url: str) -> str:
    value = str(base_url or "").strip().strip('"').rstrip("/")
    if not value:
        raise TTSError("还没有填写 GPT-SoVITS 地址。")
    if not value.startswith(("http://", "https://")):
        value = "http://" + value
    if value.endswith("/tts"):
        return value
    return value + "/tts"


def synthesize_gpt_sovits(
    text: str,
    out_path: str | Path,
    *,
    base_url: str,
    ref_audio: str,
    prompt_text: str = "",
    prompt_lang: str = "zh",
    text_lang: str = "zh",
    speed: float = 1.0,
    timeout: int = 180,
) -> Path:
    """调用 GPT-SoVITS 的 /tts 接口，保存返回的 WAV。"""
    value = clean_text(text, 1000)
    if not value:
        raise TTSError("没有可合成的文字。")
    reference = str(ref_audio or "").strip().strip('"')
    if not reference:
        raise TTSError("GPT-SoVITS 需要先填写参考音频路径。")
    endpoint = gpt_sovits_endpoint(base_url)
    params = {
        "text": value,
        "text_lang": str(text_lang or "zh"),
        "ref_audio_path": reference,
        "prompt_text": str(prompt_text or ""),
        "prompt_lang": str(prompt_lang or "zh"),
        "text_split_method": "cut5",
        "batch_size": "1",
        "media_type": "wav",
        "streaming_mode": "false",
        "speed_factor": f"{max(0.5, min(2.0, float(speed))):.2f}",
    }
    url = endpoint + "?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Darkitten-QQBridge/1.5",
            "Accept": "audio/wav,application/octet-stream,*/*",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=max(10, int(timeout))) as response:
            data = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read(600).decode("utf-8", errors="replace").strip()
        raise TTSError(f"GPT-SoVITS 返回 HTTP {exc.code}：{detail or exc.reason}") from exc
    except urllib.error.URLError as exc:
        raise TTSError(f"连接 GPT-SoVITS 失败：{exc.reason}") from exc
    except TimeoutError as exc:
        raise TTSError("连接 GPT-SoVITS 超时。") from exc

    if not data or len(data) < 1000:
        raise TTSError("GPT-SoVITS 返回的音频为空。")
    if data[:4] != b"RIFF":
        detail = data[:200].decode("utf-8", errors="replace").strip()
        raise TTSError(f"GPT-SoVITS 返回的不是 WAV：{detail or data[:16]!r}")
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    return out
