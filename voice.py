from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import wave
from array import array
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QObject, QTimer, Signal

from ai_client import AIRequest, SYSTEM_PROMPT
from app_paths import ASSET_DIR
from config import Config
from conversation import Conversation
from local_replies import local_reply

if TYPE_CHECKING:
    from pet import PetWindow


MAX_RECORD_SECONDS = 30
MIN_RECORD_SECONDS = 0.35
SAMPLE_RATES = (16000, 48000, 44100, 22050)

MODEL_DIR = ASSET_DIR / "models" / "paraformer_zh_small"
PARAFORMER_MODEL = MODEL_DIR / "model.int8.onnx"
PARAFORMER_TOKENS = MODEL_DIR / "tokens.txt"

_paraformer: Any = None
_paraformer_failed = False
_paraformer_lock = threading.Lock()


def _ascii_tokens_path() -> Path:
    """sherpa-onnx 的 C++ 词表读取不支持中文路径，必要时复制一份到 Public。"""
    try:
        str(PARAFORMER_TOKENS).encode("ascii")
        return PARAFORMER_TOKENS
    except UnicodeEncodeError:
        pass
    public = Path(os.environ.get("PUBLIC", r"C:\Users\Public"))
    target = public / "LFELKEEA" / "paraformer_tokens.txt"
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists() or target.stat().st_size != PARAFORMER_TOKENS.stat().st_size:
            shutil.copy2(PARAFORMER_TOKENS, target)
        return target
    except Exception:
        return PARAFORMER_TOKENS


def _load_paraformer() -> Any:
    """懒加载本地 Paraformer 模型，失败时返回 None 并回退到 Windows 听写。"""
    global _paraformer, _paraformer_failed
    if _paraformer is not None:
        return _paraformer
    if _paraformer_failed:
        return None
    with _paraformer_lock:
        if _paraformer is not None:
            return _paraformer
        if _paraformer_failed:
            return None
        if not PARAFORMER_MODEL.exists() or not PARAFORMER_TOKENS.exists():
            _paraformer_failed = True
            return None
        try:
            import sherpa_onnx

            _paraformer = sherpa_onnx.OfflineRecognizer.from_paraformer(
                paraformer=str(PARAFORMER_MODEL),
                tokens=str(_ascii_tokens_path()),
                num_threads=2,
                sample_rate=16000,
                feature_dim=80,
                decoding_method="greedy_search",
                debug=False,
            )
        except Exception:
            _paraformer_failed = True
            _paraformer = None
        return _paraformer


def _read_wav(path: Path) -> tuple[int, array]:
    with wave.open(str(path), "rb") as handle:
        if handle.getsampwidth() != 2:
            raise VoiceError("录音格式不支持。")
        channels = handle.getnchannels()
        rate = handle.getframerate()
        raw = handle.readframes(handle.getnframes())
    samples = array("h")
    samples.frombytes(raw)
    if sys.byteorder == "big":
        samples.byteswap()
    if channels > 1:
        samples = array("h", samples[::channels])
    return rate, samples


def _normalized_float(samples: array) -> array:
    """麦克风音量普遍偏小，识别前把峰值拉回可用范围，避免糊字。"""
    if not samples:
        return array("f")
    peak = max(max(samples), -min(samples))
    gain = 1.0
    if 0 < peak < 6000:
        gain = min(6.0, 28000.0 / peak)
    scale = gain / 32768.0
    return array("f", (sample * scale for sample in samples))


def _recognize_paraformer(wav_path: Path) -> str | None:
    recognizer = _load_paraformer()
    if recognizer is None:
        return None
    rate, samples = _read_wav(wav_path)
    stream = recognizer.create_stream()
    stream.accept_waveform(rate, _normalized_float(samples))
    recognizer.decode_stream(stream)
    text = (stream.result.text or "").strip()
    # Paraformer 偶尔会在汉字之间插空格，中文对话里去掉更自然
    text = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", text)
    return text.strip()


class VoiceError(Exception):
    pass


class VoiceRecorder:
    """把默认麦克风的录音保存成临时 WAV。"""

    def __init__(self) -> None:
        self._stream = None
        self._chunks: list[bytes] = []
        self._lock = threading.Lock()
        self._rate = 16000

    def _callback(self, indata, frames, time_info, status) -> None:
        try:
            data = bytes(indata)
        except Exception:
            return
        if data:
            with self._lock:
                self._chunks.append(data)

    def start(self) -> None:
        try:
            import sounddevice as sd
        except Exception as exc:
            raise VoiceError("缺少麦克风组件，无法开始录音。") from exc

        last_error = None
        for rate in SAMPLE_RATES:
            stream = None
            with self._lock:
                self._chunks = []
            try:
                stream = sd.RawInputStream(
                    samplerate=rate,
                    channels=1,
                    dtype="int16",
                    callback=self._callback,
                )
                stream.start()
            except Exception as exc:
                last_error = exc
                if stream is not None:
                    try:
                        stream.close()
                    except Exception:
                        pass
                continue
            self._stream = stream
            self._rate = rate
            return

        detail = str(last_error) if last_error else "没有可用的输入设备"
        raise VoiceError(f"麦克风打不开：{detail}")

    def stop(self) -> Path | None:
        stream = self._stream
        self._stream = None
        if stream is not None:
            try:
                stream.stop()
            except Exception:
                pass
            try:
                stream.close()
            except Exception:
                pass

        with self._lock:
            data = b"".join(self._chunks)
            self._chunks = []
        if len(data) < int(self._rate * 2 * MIN_RECORD_SECONDS):
            return None

        fd, name = tempfile.mkstemp(prefix="lfelkeea_voice_", suffix=".wav")
        os.close(fd)
        path = Path(name)
        try:
            with wave.open(str(path), "wb") as handle:
                handle.setnchannels(1)
                handle.setsampwidth(2)
                handle.setframerate(self._rate)
                handle.writeframes(data)
        except Exception:
            path.unlink(missing_ok=True)
            raise
        return path

    def cancel(self) -> None:
        stream = self._stream
        self._stream = None
        if stream is not None:
            try:
                stream.stop()
            except Exception:
                pass
            try:
                stream.close()
            except Exception:
                pass
        with self._lock:
            self._chunks = []


class SpeechSignals(QObject):
    ok = Signal(str)
    fail = Signal(str)


class RecorderSignals(QObject):
    """把后台线程里的麦克风开关结果送回主线程。"""

    ready = Signal(object, int)  # recorder, token
    failed = Signal(str, int)  # error, token
    stopped = Signal(object, object, int, str)  # recorder, wav_path, token, error


class SpeechToTextRequest:
    """调用 Windows 自带的中文听写引擎，在后台线程里识别 WAV。"""

    def __init__(self, wav_path: Path, language: str = "zh-CN") -> None:
        self.wav_path = Path(wav_path)
        self.language = language
        self.signals = SpeechSignals()
        self._cancelled = False

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True).start()

    def cancel(self) -> None:
        self._cancelled = True

    def _recognize_windows(self) -> str:
        script = ASSET_DIR / "recognize_zh.ps1"
        if not script.exists():
            raise VoiceError("语音识别脚本缺失。")
        out_fd, out_name = tempfile.mkstemp(prefix="lfelkeea_stt_", suffix=".txt")
        os.close(out_fd)
        out_path = Path(out_name)
        try:
            powershell = shutil.which("powershell.exe") or "powershell.exe"
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            command = [
                powershell,
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-WindowStyle",
                "Hidden",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(script),
                "-AudioPath",
                str(self.wav_path),
                "-OutputPath",
                str(out_path),
                "-Culture",
                self.language,
            ]
            result = subprocess.run(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=30,
                creationflags=flags,
            )
            text = out_path.read_text(encoding="utf-8", errors="replace").strip() if out_path.exists() else ""
            if result.returncode == 0 and text:
                return text
            if result.returncode == 3 or not text:
                return ""
            if len(text) > 160:
                text = text[:160].rstrip() + "……"
            raise VoiceError(f"识别失败：{text}")
        finally:
            out_path.unlink(missing_ok=True)

    def _run(self) -> None:
        try:
            text = ""
            try:
                text = _recognize_paraformer(self.wav_path) or ""
            except Exception:
                text = ""
            if not text:
                text = self._recognize_windows()
            if self._cancelled:
                return
            if text:
                self.signals.ok.emit(text)
            else:
                self.signals.fail.emit("没听清，再说一次吧。")
        except subprocess.TimeoutExpired:
            if not self._cancelled:
                self.signals.fail.emit("识别等太久了，再试一次吧。")
        except VoiceError as exc:
            if not self._cancelled:
                self.signals.fail.emit(str(exc))
        except Exception as exc:
            if not self._cancelled:
                self.signals.fail.emit(f"识别失败：{exc}")
        finally:
            self.wav_path.unlink(missing_ok=True)


class VoiceChatController(QObject):
    """长按录音、松开识别，并通过桌宠气泡完成一轮对话。"""

    def __init__(
        self,
        config: Config,
        pet: PetWindow,
        parent: QObject | None = None,
        conversation: Conversation | None = None,
    ) -> None:
        super().__init__(parent)
        self.config = config
        self.pet = pet
        self.conversation = conversation or Conversation(12, self)
        self._state = "idle"
        self._token = 0
        self._recorder: VoiceRecorder | None = None
        self._pending_start = False
        self._prime_blocked_until = 0.0
        self._recorder_factory = VoiceRecorder
        self._recorder_signals = RecorderSignals(self)
        self._recorder_signals.ready.connect(self._on_recorder_ready)
        self._recorder_signals.failed.connect(self._on_recorder_failed)
        self._recorder_signals.stopped.connect(self._on_recorder_stopped)
        self._stt: SpeechToTextRequest | None = None
        self._ai: AIRequest | None = None
        self._transcript_at = 0.0
        self._max_timer = QTimer(self)
        self._max_timer.setSingleShot(True)
        self._max_timer.setInterval(MAX_RECORD_SECONDS * 1000)
        self._max_timer.timeout.connect(self.finish_listening)

    def prime(self) -> None:
        """按下鼠标就开始收音，等长按触发时不会漏掉开头几个字。

        麦克风初始化在 Windows 上要 50-200 ms，必须放到后台线程，
        否则每次点击桌宠都会先卡一下。
        """
        if self._state != "idle":
            return
        if time.monotonic() < self._prime_blocked_until:
            return
        self._token += 1
        self._pending_start = False
        self._state = "priming"
        self._open_in_background(self._token)

    def start_listening(self) -> None:
        if self._state == "recording":
            return
        if self._state in ("priming", "pending"):
            self._pending_start = True
            if self._recorder is not None:
                self._begin_recording()
            else:
                self._state = "pending"
            return
        self._reset_active()
        self.pet._last_interaction = time.monotonic()
        self._state = "pending"
        self._pending_start = True
        self._open_in_background(self._token)

    def finish_listening(self) -> None:
        if self._state == "pending":
            # 麦克风还没打开就松手了，这一轮没有录到声音。
            self._reset_active()
            self.pet.say("我没听到声音，长按我再说一次吧。", 4800)
            return
        if self._state != "recording":
            return
        self._max_timer.stop()
        self._state = "stopping"
        token = self._token
        recorder = self._recorder
        self._recorder = None
        self.pet._last_interaction = time.monotonic()
        if recorder is None:
            self._state = "idle"
            return
        threading.Thread(
            target=self._stop_recorder,
            args=(recorder, token),
            name="pet-mic-stop",
            daemon=True,
        ).start()

    def cancel_listening(self) -> None:
        if self._state not in ("priming", "pending", "recording", "stopping"):
            return
        if self._state == "recording":
            self.pet.bubble.hide_now()
        self._reset_active()

    # ---------- 后台开关麦克风 ----------

    def _open_in_background(self, token: int) -> None:
        threading.Thread(
            target=self._open_recorder,
            args=(token,),
            name="pet-mic-open",
            daemon=True,
        ).start()

    def _open_recorder(self, token: int) -> None:
        recorder = None
        try:
            recorder = self._recorder_factory()
            recorder.start()
        except VoiceError as exc:
            self._close_quietly(recorder)
            self._recorder_signals.failed.emit(
                f"{exc} 请在 Windows 设置里允许麦克风访问。", token
            )
        except Exception as exc:
            self._close_quietly(recorder)
            self._recorder_signals.failed.emit(f"麦克风出错了：{exc}", token)
        else:
            self._recorder_signals.ready.emit(recorder, token)

    def _stop_recorder(self, recorder: VoiceRecorder, token: int) -> None:
        wav_path: Path | None = None
        error = ""
        try:
            wav_path = recorder.stop()
        except Exception as exc:
            error = str(exc)
        self._recorder_signals.stopped.emit(recorder, wav_path, token, error)

    @staticmethod
    def _close_quietly(recorder: VoiceRecorder | None) -> None:
        if recorder is None:
            return
        try:
            recorder.cancel()
        except Exception:
            pass

    def _close_in_background(self, recorder: VoiceRecorder) -> None:
        threading.Thread(
            target=self._close_quietly,
            args=(recorder,),
            name="pet-mic-close",
            daemon=True,
        ).start()

    def _begin_recording(self) -> None:
        self._pending_start = False
        self._state = "recording"
        self._max_timer.start()
        self.pet.say("● 我在听，松开结束。", 60000)

    def _on_recorder_ready(self, recorder: object, token: int) -> None:
        if token != self._token or self._state not in ("priming", "pending"):
            self._close_in_background(recorder)  # type: ignore[arg-type]
            return
        self._recorder = recorder  # type: ignore[assignment]
        if self._state == "pending" or self._pending_start:
            self._begin_recording()

    def _on_recorder_failed(self, error: str, token: int) -> None:
        if token != self._token or self._state not in ("priming", "pending"):
            return
        announce = self._state == "pending" or self._pending_start
        self._pending_start = False
        self._state = "idle"
        if announce:
            self.pet.say(error, 5600)
        else:
            # 没有麦克风权限时不要每次点击都重试，避免反复卡后台。
            self._prime_blocked_until = time.monotonic() + 20.0

    def _on_recorder_stopped(
        self, recorder: object, wav_path: object, token: int, error: str
    ) -> None:
        if token != self._token or self._state != "stopping":
            if isinstance(wav_path, Path):
                try:
                    wav_path.unlink(missing_ok=True)
                except OSError:
                    pass
            return
        if error:
            self._state = "idle"
            self.pet.say(f"保存录音失败了：{error}", 5200)
            return
        if not isinstance(wav_path, Path):
            self._state = "idle"
            self.pet.say("我没听到声音，长按我再说一次吧。", 4800)
            return

        self._state = "transcribing"
        self.pet.say("听到了，正在识别……", 60000)
        request = SpeechToTextRequest(wav_path)
        self._stt = request
        request.signals.ok.connect(lambda text, t=token: self._on_speech_ok(text, t))
        request.signals.fail.connect(lambda error, t=token: self._on_speech_fail(error, t))
        request.start()

    def _on_speech_ok(self, text: str, token: int) -> None:
        if token != self._token or self._state != "transcribing":
            return
        self._stt = None
        text = " ".join((text or "").split())
        if not text:
            self._state = "idle"
            self.pet.say("没听清，再说一次吧。", 4800)
            return

        self._state = "thinking"
        self._append_history("user", text)
        shown = text if len(text) <= 110 else text[:110].rstrip() + "……"
        self.pet.say(f"你说：「{shown}」", 2600)
        self._transcript_at = time.monotonic()
        QTimer.singleShot(2750, lambda t=token: self._show_thinking(t))

        messages = [{"role": "system", "content": SYSTEM_PROMPT}] + self.conversation.messages(12)
        request = AIRequest(dict(self.config.ai), messages)
        self._ai = request
        request.signals.ok.connect(lambda reply, t=token: self._on_ai_ok(reply, t))
        request.signals.fail.connect(lambda error, t=token: self._on_ai_fail(error, t))
        request.start()

    def _on_speech_fail(self, error: str, token: int) -> None:
        if token != self._token or self._state != "transcribing":
            return
        self._stt = None
        self._state = "idle"
        self.pet.say(error or "没听清，再说一次吧。", 5200)
        self.pet._last_interaction = time.monotonic()

    def _show_thinking(self, token: int) -> None:
        if token == self._token and self._state == "thinking":
            self.pet.say("嗯……我想想。", 60000)

    def _on_ai_ok(self, reply: str, token: int) -> None:
        if token != self._token or self._state != "thinking":
            return
        self._append_history("assistant", reply)
        elapsed = time.monotonic() - self._transcript_at
        delay = max(0, int((2400 - elapsed * 1000)))
        QTimer.singleShot(delay, lambda r=reply, t=token: self._deliver_reply(r, t))

    def _on_ai_fail(self, error: str, token: int) -> None:
        if token != self._token or self._state != "thinking":
            return
        reply = local_reply(self.conversation.last_user())
        self._append_history("assistant", reply)
        elapsed = time.monotonic() - self._transcript_at
        delay = max(0, int((2400 - elapsed * 1000)))
        QTimer.singleShot(delay, lambda r=reply, t=token: self._deliver_reply(r, t))

    def _deliver_reply(self, reply: str, token: int) -> None:
        if token != self._token or self._state != "thinking":
            return
        self._state = "idle"
        self._ai = None
        reply = (reply or "").strip()
        if len(reply) > 140:
            reply = reply[:140].rstrip() + "……"
        if reply:
            self.pet.say(reply, 7000)
            self.pet._trigger_bounce()
        self.pet._last_interaction = time.monotonic()

    def _append_history(self, role: str, content: str) -> None:
        self.conversation.append(role, content)

    def _reset_active(self) -> None:
        self._token += 1
        self._max_timer.stop()
        self._pending_start = False
        recorder = self._recorder
        self._recorder = None
        if recorder is not None:
            self._close_in_background(recorder)
        if self._stt is not None:
            self._stt.cancel()
            self._stt = None
        if self._ai is not None:
            self._ai.cancel()
            self._ai = None
        self._state = "idle"

    def shutdown(self) -> None:
        self._reset_active()
