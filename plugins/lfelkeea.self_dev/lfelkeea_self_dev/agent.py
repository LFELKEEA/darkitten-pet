from __future__ import annotations

"""最小可用的 Agent 循环：模型给一个 JSON 动作，执行工具，把结果喂回去。

不依赖各家不同的原生 function calling，任何 OpenAI 兼容接口都能用。
"""

import json
import re
import threading
import time
from typing import Any, Callable

from .tools import MAX_TOOL_RESULT, ToolBox

CONTEXT_LIMIT = 150_000


def extract_action(text: str) -> dict[str, Any] | None:
    """从模型输出里找出第一个像动作的 JSON 对象，容忍 ```json 包裹和前后废话。"""
    cleaned = str(text or "").strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z]*\s*", "", cleaned)
        cleaned = re.sub(r"\s*```\s*$", "", cleaned)
    decoder = json.JSONDecoder()
    found: list[dict[str, Any]] = []
    index = 0
    while True:
        start = cleaned.find("{", index)
        if start < 0:
            break
        try:
            obj, end = decoder.raw_decode(cleaned, start)
        except ValueError:
            index = start + 1
            continue
        if isinstance(obj, dict):
            found.append(obj)
        index = max(end, start + 1)
    for obj in found:
        if "tool" in obj or "final" in obj:
            return obj
    return found[0] if found else None


class Agent:
    def __init__(
        self,
        *,
        chat_fn: Callable[[list[dict[str, Any]]], tuple[str, str]],
        toolbox: ToolBox,
        system_prompt: str,
        user_prompt: str,
        emit: Callable[[dict[str, Any]], None],
        stop_event: threading.Event,
    ) -> None:
        self.chat_fn = chat_fn
        self.toolbox = toolbox
        self.system_prompt = system_prompt
        self.user_prompt = user_prompt
        self.emit = emit
        self.stop_event = stop_event

    def run(self) -> dict[str, Any]:
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": self.user_prompt},
        ]
        steps = 0
        parse_failures = 0
        self._emit({"type": "info", "text": "已连接模型，开始工作。"})

        # 不设步数上限：一直做到模型主动 final，或主人点停止。
        while True:
            if self.stop_event.is_set():
                return self._result(False, "已被主人停止。", steps, "stopped")
            steps += 1
            self._emit({"type": "step", "step": steps})

            try:
                reply, finish = self._chat(messages)
            except Exception as exc:  # 网络/接口错误，重试一次
                self._emit({"type": "info", "text": f"模型请求失败，重试一次：{exc}"})
                time.sleep(2)
                if self.stop_event.is_set():
                    return self._result(False, "已被主人停止。", steps, "stopped")
                try:
                    reply, finish = self._chat(messages)
                except Exception as exc2:
                    return self._result(False, f"模型请求失败：{exc2}", steps, "error")

            if self.stop_event.is_set():
                return self._result(False, "已被主人停止。", steps, "stopped")

            if finish == "length":
                self._emit(
                    {
                        "type": "info",
                        "text": "模型输出达到了接口自身的长度上限；请继续下一步，或把这一步拆成更小的动作。",
                    }
                )

            action = extract_action(reply)
            if action is None:
                parse_failures += 1
                self._emit({"type": "info", "text": "模型没有按协议输出，已提醒它重试。"})
                messages.append({"role": "assistant", "content": reply[:4000]})
                messages.append(
                    {
                        "role": "user",
                        "content": "你上一轮没有输出合法 JSON。请严格按系统提示的协议，只输出一个 JSON 对象。",
                    }
                )
                if parse_failures >= 3:
                    return self._result(
                        False,
                        f"模型连续 3 次没有按协议输出。最后一次内容：{reply[:300]}",
                        steps,
                        "error",
                    )
                continue

            thought = str(action.get("thought", "") or "").strip()
            if thought:
                self._emit({"type": "thought", "text": thought})

            tool = str(action.get("tool", "") or "").strip()
            if not tool and "final" in action:
                summary = str(action.get("final", "") or "").strip()
                return self._result(True, summary or "任务结束。", steps, "")
            if tool in ("finish", "done", "final"):
                args = action.get("args") if isinstance(action.get("args"), dict) else {}
                summary = str(args.get("summary") or args.get("final") or action.get("final") or "").strip()
                return self._result(True, summary or "任务结束。", steps, "")
            if not tool:
                parse_failures += 1
                messages.append({"role": "assistant", "content": reply[:4000]})
                messages.append(
                    {"role": "user", "content": "这个 JSON 里既没有 tool 也没有 final，请按协议重新输出。"}
                )
                if parse_failures >= 3:
                    return self._result(False, "模型连续 3 次没有给出有效动作。", steps, "error")
                continue

            args = action.get("args")
            if args is None:
                args = {}
            if not isinstance(args, dict):
                result = "错误：args 必须是 JSON 对象。"
            else:
                self._emit({"type": "tool", "name": tool, "args": args})
                result = self.toolbox.run(tool, args)

            succeeded = not result.startswith(("错误：", "主人拒绝"))
            preview = result if len(result) <= 600 else result[:600] + "…"
            self._emit(
                {
                    "type": "result",
                    "name": tool,
                    "ok": succeeded,
                    "chars": len(result),
                    "preview": preview,
                    "files": list(dict.fromkeys(self.toolbox.changed_files)),
                }
            )

            messages.append({"role": "assistant", "content": reply[:6000]})
            messages.append(
                {
                    "role": "user",
                    "content": f"TOOL RESULT（{tool}）\n{result[:MAX_TOOL_RESULT]}",
                }
            )
            messages = self._trim(messages)

    # ------------------------------------------------------------------

    def _chat(self, messages: list[dict[str, Any]]) -> tuple[str, str]:
        return self.chat_fn(messages)

    def _emit(self, event: dict[str, Any]) -> None:
        try:
            self.emit(event)
        except Exception:
            pass

    def _result(self, ok: bool, summary: str, steps: int, reason: str) -> dict[str, Any]:
        return {
            "ok": ok,
            "summary": summary,
            "steps": steps,
            "reason": reason,
        }

    def _trim(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        def total(items: list[dict[str, Any]]) -> int:
            return sum(len(str(item.get("content", ""))) for item in items)

        if total(messages) <= CONTEXT_LIMIT:
            return messages
        head = messages[:2]
        tail = messages[-8:]
        for index in range(2, len(messages) - 8):
            if str(messages[index].get("content", "")).startswith("TOOL RESULT"):
                messages[index] = {"role": "user", "content": "TOOL RESULT（较早的结果已省略）"}
        if total(messages) <= CONTEXT_LIMIT:
            return messages
        return head + [
            {
                "role": "user",
                "content": "（中间的工具调用记录已省略。请以当前文件状态为准继续，必要时重新读取文件。）",
            }
        ] + tail
