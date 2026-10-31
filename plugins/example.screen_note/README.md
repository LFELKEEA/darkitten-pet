# 示例：屏幕观察补充

演示 `screen.context` 事件：

- 事件在截屏/读取窗口标题之后、请求 AI 之前触发；
- 可以向 `context.notes` 追加一句提示；
- 设置 `context.skip = True` 和 `context.skip_reason` 可以跳过这次观察；
- 不要在这里复述密码、聊天记录等隐私内容。

这个插件只需要 `events` 权限。
