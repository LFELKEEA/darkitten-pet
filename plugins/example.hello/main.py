"""示例插件：定时问候。

插件入口必须定义 setup(api)。不要在模块顶层直接操作界面或启动线程。
"""


def setup(api):
    settings = api.settings
    try:
        minutes = max(1, int(settings.get("interval_minutes", 10)))
    except (TypeError, ValueError):
        minutes = 10
    custom = str(settings.get("message", "")).strip()

    def greet():
        if custom:
            api.say(custom)
        else:
            api.say("我还在这儿哦，主人。")

    api.every(minutes * 60 * 1000, greet)
    api.on("startup", lambda: api.log(f"定时问候已启动，每 {minutes} 分钟一次"))


def teardown():
    # 可选。定时器会由插件 API 自动清理。
    pass
