"""示例插件：给屏幕观察补充上下文。

screen.context 事件会在请求 AI 之前触发。插件可以读取 context，
也可以向 context.notes 追加一句简短说明，或设置 context.skip = True。
"""

CODE_PROCESSES = ("code", "devenv", "pycharm64", "idea64", "sublime_text", "notepad++")


def setup(api):
    def annotate(context):
        process = (context.process or "").lower()
        title = (context.title or "").lower()
        if any(name in process for name in CODE_PROCESSES):
            context.notes.append("主人可能正在写代码")
        elif "bilibili" in title or "youtube" in title or "netflix" in title:
            context.notes.append("主人可能在看视频")
        elif "steam" in process:
            context.notes.append("主人可能准备玩游戏")

    api.on("screen.context", annotate)


def teardown():
    pass
