# LFELKEEA 插件 API 速查（api_version = 1）

给自开发代理看的精简版。完整文档在桌宠安装目录的 `docs/PLUGIN_DEV.md`。

## 目录

```text
data/plugins/<插件id>/
├─ plugin.json   必需
├─ main.py       必需（或 entry 指定的文件）
├─ README.md     建议
└─ 其他模块/资源  可选
```

插件文件夹在加载时会被加入 `sys.path`，因此 `main.py` 可以直接 `from my_module import ...`。

## plugin.json

```json
{
  "id": "com.example.clock",
  "name": "显示名称",
  "version": "1.0.0",
  "api_version": 1,
  "entry": "main.py",
  "description": "一句话说明",
  "author": "作者",
  "permissions": ["pet.say", "timer", "settings"],
  "settings": {"interval_minutes": 10}
}
```

- `id`：小写字母、数字、点、下划线、短横线，2–64 位；必须和文件夹名一致。
- `api_version`：固定 `1`。
- `entry`：不能指到插件目录之外。
- `settings`：只放普通配置，禁止放 API Key、密码、token。

## 生命周期

```python
def setup(api):
    ...

def teardown():          # 可选
    ...

def open_settings(api, parent=None):  # 可选，返回 QDialog
    ...
```

- `setup()` 在桌宠主线程执行，**不能阻塞**：不要 `time.sleep()`、同步 HTTP、长时间文件扫描。
- 所有事件与定时器回调都在主线程。
- 插件异常会被宿主捕获，状态变成“运行出错（已隔离）”，堆栈写入 `data/plugins.log`。
- 新插件默认不启用，需要用户在「插件管理」里勾选。

## 权限与接口

| 权限 | 接口 | 说明 |
| --- | --- | --- |
| `pet.say` | `api.say(text, duration=5000)` | 气泡显示 1.2–20 秒，文本压成一行，最多 200 字 |
| `pet.action` | `api.action(name)` | `hop` / `spin` / `stretch` / `shake` / `lean` / `nod` |
| `ai.chat` | `api.chat(messages, callback)` | 异步；`callback(reply, error)` 二选一 |
| `screen.capture` | `api.capture_screen()` | 仅主线程；返回 `ScreenContext` |
| `timer` | `api.every(ms, cb)` / `api.after(ms, cb)` | 最小 1000 ms，最大 24 小时 |
| `events` | `api.on(event, cb)` | 见事件表 |
| `settings` | `api.settings` / `api.save_settings(dict)` | 与 manifest 默认值浅合并 |
| `storage` | `api.storage` / `api.save_storage()` | 小数据，存 `data/plugins/.state/<id>.json` |
| 无 | `api.log(text)` | 写入 `data/plugins.log` |

没声明的权限会抛 `PluginPermissionError`，`setup()` 阶段会直接加载失败。

## 事件

| 事件 | 触发时机 | 参数 |
| --- | --- | --- |
| `startup` | 插件加载完成 | 无 |
| `shutdown` | 卸载 / 重载 / 退出 | 无 |
| `settings.saved` | 设置保存后 | 无 |
| `chat.reply` | 聊天框收到 AI 回复 | `reply` |
| `screen.context` | 屏幕观察、请求 AI 之前 | `context` |
| `screen.comment` | 屏幕观察说完话之后 | `text`, `context`, `mode` |
| `pet.pat` | 摸头 | 无 |
| `pet.poke` | 戳一戳 | 无 |

`screen.context` 可以给 `context.notes` 追加提示，或设置 `context.skip = True` 跳过本次观察。

## 设置界面

```python
from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel, QPushButton

def open_settings(api, parent=None):
    dialog = QDialog(parent)
    dialog.setWindowTitle("我的插件")
    layout = QVBoxLayout(dialog)
    layout.addWidget(QLabel("配置项"))
    close = QPushButton("关闭")
    close.clicked.connect(dialog.accept)
    layout.addWidget(close)
    return dialog
```

只有插件运行中才会出现「设置」按钮；插件停用或重载时窗口自动关闭。建议 760×640 左右的深色界面。

## 容易踩的坑

1. **`api.chat()` 不适合写代码**：宿主会把回复截断到 800 字，`max_tokens` 固定 500。需要长文本时，插件应自己用 `urllib` 请求用户配置的接口；绝不能在日志、存储、上传内容里包含 API Key。
2. **不要自己建 Qt 定时器**：用 `api.every` / `api.after`，插件卸载时会自动清理。
3. **不要从后台线程碰界面**：`api.say`、`api.action`、`api.capture_screen` 都只能在主线程；后台结果用 Qt 信号回到主线程。
4. **数据目录**：不要写死盘符。插件自己的目录是 `data/plugins/<id>`，需要数据优先用 `api.storage` / `api.save_settings`。
5. **升级会覆盖插件目录**：需要长期保存的数据放 `api.storage`，不要放插件文件夹。
6. **不要读取或打印 `data/config.json` 的 `api_key`**。
7. **宿主可能用内存旧配置回写**：新增的顶层运行开关请同步进宿主的 `LOCAL_SETTINGS_KEYS`（仅改宿主时需要关心）。

## 安装与验证

1. 插件文件夹放到 `data/plugins/`。
2. 右键桌宠 →「插件管理」→「重新扫描」。
3. 勾选「启用」，确认状态为「运行中」。
4. 报错看 `data/plugins.log`；改完代码点「重载」。
