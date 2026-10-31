# 示例：定时问候

演示：

- `api.settings` 读取 `plugin.json` 里的默认设置；
- `api.every()` 创建定时器；
- `api.say()` 让桌宠说话；
- `api.on("startup", ...)` 订阅生命周期事件。

启用后，可以在 `data/plugins/example.hello/plugin.json` 里改 `interval_minutes` 和 `message`，重启桌宠生效。

这个插件需要 `pet.say`、`timer`、`events`、`settings` 四项权限。
