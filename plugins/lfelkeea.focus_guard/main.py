"""专注时间：专注 / 休息循环提醒。

启用后立即开始一个专注段；专注结束提醒休息，休息结束提醒回来。
时长可以在 config.json 的 plugins.settings.lfelkeea.focus_guard 里改：

    {"focus_minutes": 50, "break_minutes": 5}

专注次数按天记在插件自己的本地存储里，不会上传。
"""

from datetime import date

DEFAULT_FOCUS_MINUTES = 50
DEFAULT_BREAK_MINUTES = 5
MIN_FOCUS = 5
MAX_FOCUS = 180
MIN_BREAK = 1
MAX_BREAK = 30


def _clamp(value, fallback, low, high):
    try:
        number = int(value)
    except (TypeError, ValueError):
        return fallback
    return max(low, min(high, number))


def setup(api):
    settings = api.settings
    focus = _clamp(settings.get("focus_minutes"), DEFAULT_FOCUS_MINUTES, MIN_FOCUS, MAX_FOCUS)
    rest = _clamp(settings.get("break_minutes"), DEFAULT_BREAK_MINUTES, MIN_BREAK, MAX_BREAK)
    state = {"token": 0, "running": True}

    def schedule(delay_ms, callback):
        # api.after 不返回可取消的句柄，用 token 让旧回调自动失效。
        state["token"] += 1
        token = state["token"]

        def fire():
            if not state["running"] or token != state["token"]:
                return
            callback()

        api.after(delay_ms, fire)

    def bump_count():
        try:
            store = api.storage
            today = date.today().isoformat()
            if store.get("date") != today:
                store["date"] = today
                store["count"] = 0
            store["count"] = int(store.get("count", 0) or 0) + 1
            api.save_storage()
            return store["count"]
        except Exception as exc:  # 计数失败不影响计时
            api.log(f"记录专注次数失败：{exc}")
            return 0

    def finish_focus():
        count = bump_count()
        suffix = f"今天第 {count} 段" if count else "这一段"
        api.say(f"专注 {focus} 分钟完成，{suffix}。起来走动一下，看看远处。", 8000)
        api.action("stretch")
        schedule(rest * 60 * 1000, finish_break)

    def finish_break():
        api.say(f"休息结束，回来继续。下一段 {focus} 分钟。", 5000)
        api.action("nod")
        schedule(focus * 60 * 1000, finish_focus)

    api.say(f"专注时间开始：{focus} 分钟。先把最要紧的那件事做掉。", 6000)
    api.action("nod")
    schedule(focus * 60 * 1000, finish_focus)
    api.log(f"专注时间已启动：专注 {focus} 分钟，休息 {rest} 分钟")


def teardown():
    # 定时器由插件 API 自动清理。
    pass
