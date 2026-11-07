# LFELKEEA 桌宠

住在 Windows 桌面上的白发红瞳猫耳少女。支持 AI 聊天、长按语音对话、主动搭话、定时观察屏幕，以及本地插件扩展。

当前源码版本：`1.4.0`。

- 下载安装包：[pet-v1.4.0](https://github.com/LFELKEEA/darkitten-launcher/releases/tag/pet-v1.4.0)
- 插件库：[lfelkeea.github.io/darkitten-launcher/plugins.html](https://lfelkeea.github.io/darkitten-launcher/plugins.html)
- 插件源码：[`plugins/`](plugins)（示例插件、专注守护、QQ 桥接）

## 功能

- 桌面宠物窗口：拖动、置顶、缩放、睡眠、摸头、戳一戳、动作动画。
- AI 聊天：免费公共通道 / OpenAI 兼容接口 / 本地 Ollama。
- 长按语音：本地 Paraformer 识别，失败时回退 Windows 中文听写。
- 主动搭话：按频率随机说一句，不打断聊天和语音。
- **屏幕观察**：按间隔看一眼前台窗口或主屏幕，主动说一句话。
  - `window`：只读窗口标题和进程名，不截屏、不上传。
  - `vision`：截取主屏幕、压缩后发给支持视觉的模型。
  - `ocr`：本地 Windows OCR 识别屏幕文字，再把文字交给 AI。
  - `auto`：当前模型支持视觉时用截图，否则用窗口标题。
  - 隐私闸门：敏感窗口、全屏应用、聊天中、语音中、刚和桌宠互动过时自动跳过。
- **Live2D 换装**：1.4.0 起插件可以通过 `pet.model` 接口换 Live2D 模型、注册动作和表情，通过 `pet.persona` 替换台词与 AI 人格。运行库随包放在 `vendor/`（live2d-py + GLFW），不需要单独安装。
- **插件系统**：`data/plugins/` 下的本地 Python 插件，带权限声明、事件、定时器、AI、截屏、设置和存储 API。
- 插件管理界面：启用/停用/重载、删除（移入回收站并清理配置和状态）、权限展示、错误隔离、日志。
- **插件市场**：从插件管理点「插件市场」，浏览站点全部插件，查看版本、权限和外部依赖，直接下载安装；安装后默认不运行，需要手动启用。
- **自动更新**：从站点清单检查桌宠新版本，下载后校验 SHA-256，退出时由隐藏脚本替换程序文件并自动重启，`data/` 完整保留。右键桌宠、托盘菜单、设置页都能手动检查更新。
- **插件更新**：读取插件库 `plugins.json`，支持单个或全部更新；同样校验 SHA-256，更新前自动备份旧插件。

## Live2D 模型从哪来

桌宠、插件和发布包都**不附带 Live2D 模型文件**。需要「深度求索娘（大肥鱼）」皮肤时：

1. 到 B 站 **神宫凉子（UID：13737731）** 的主页下载 `DeepSeek免费公皮.zip`；
2. 把 ZIP 放到 `D:\` 根目录、下载目录或桌面目录，插件会自动识别并解到 `data/models/deepseek_chan/`；
3. 也可以安装 `plugins/lfelkeea.deepseek_chan` 后，在插件设置里手动选择 ZIP 或 `.model3.json`。

原画及拆分：神宫凉子；Live2D 建模：霓虹猫_NeonNeko。粉丝二创，非 DeepSeek 官方形象。请不要把模型文件重新上传到网盘或素材站。

## 目录

```text
desktop_pet/
├─ main.py                 # 入口，单实例
├─ app.py                  # 应用控制器，接线桌宠/聊天/语音/屏幕/插件
├─ pet.py                  # 桌宠窗口
├─ live2d_model.py         # Live2D 渲染、动作片段与表情
├─ chat.py                 # 聊天窗口
├─ voice.py                # 录音与语音识别
├─ screen_watcher.py       # 屏幕观察、OCR、视觉请求
├─ conversation.py         # 聊天/语音/屏幕共用上下文
├─ plugin_manager.py       # 插件发现、加载、权限、事件、API
├─ plugin_updater.py       # 插件更新：清单、下载、校验、替换
├─ plugins_ui.py           # 插件管理界面
├─ plugin_market.py        # 在线插件市场：浏览、下载、安装
├─ settings.py             # 设置窗口
├─ updater.py              # 桌宠更新：清单、下载、校验、替换脚本
├─ update_ui.py            # 更新检查与下载界面
├─ ui_widgets.py           # 可拖动标题栏等共用控件
├─ ai_client.py            # AI 请求与视觉能力判断
├─ config.py               # 默认配置与合并
├─ version.py              # 版本号常量
├─ version.json            # 打包进程序的版本标记，更新时用于校验
├─ assets/                 # 人物图、图标、OCR/语音脚本、本地模型
├─ plugins/                # 内置示例插件（首次运行复制到 data/plugins）
├─ docs/                   # 插件开发文档
├─ tests/                  # 单元测试与冒烟测试
├─ vendor/                 # Live2D 运行库（live2d-py、GLFW）
└─ LFELKEEA.spec           # PyInstaller 打包配置
```

运行时数据在 `data/`：

```text
data/
├─ config.json             # 用户配置（可能含 API Key，不要提交）
├─ error.log               # 启动错误
├─ plugins.log             # 插件日志
└─ plugins/                # 用户插件与状态
```

## 开发运行

需要 Python 3.10+、PySide6、sherpa-onnx、sounddevice。

```bash
pip install -r requirements.txt
python main.py
```

如果没有安装语音依赖，聊天和屏幕观察仍然可用，长按语音会提示缺少组件或回退 Windows 听写。

### 语音模型

`assets/models/paraformer_zh_small/` 是第三方语音识别模型，体积约 80 MB，不随源码仓库分发。缺少模型时，长按语音会自动回退到 Windows 自带的中文听写，不影响其他功能。

想用本地 Paraformer 识别，把以下两个文件放到该目录即可：

```text
assets/models/paraformer_zh_small/model.int8.onnx
assets/models/paraformer_zh_small/tokens.txt
```

模型可自行从 sherpa-onnx 官方模型库获取对应版本，使用前请确认模型自身的许可条款。

## 测试

```bash
python -m unittest discover -s tests -p "test_*.py" -v
```

测试覆盖：

- 对话上下文截断与清理；
- 插件发现、启用、权限拒绝、异常隔离、设置与存储；
- 视觉模型判断、敏感窗口判断、屏幕观察提示词、插件跳过截屏；
- 桌宠更新清单解析、SHA-256 校验、安全解压、版本匹配；
- 插件更新清单解析、下载校验、替换与备份、路径穿越拦截。

## 打包

```bash
pyinstaller LFELKEEA.spec
```

产物在 `dist/LFELKEEA/`。发布 ZIP 的根目录必须是 `LFELKEEA/`，启动器会寻找 `LFELKEEA.exe`。
`version.json` 会被打进 `_internal/`，自动更新时用于核对更新包版本。

**不要把 `data/config.json` 打进发布包。** 它包含用户 API Key。

## 插件开发

- 完整指南：[`docs/PLUGIN_DEV.md`](docs/PLUGIN_DEV.md)
- 给其他 AI 的提示词：[`docs/AI_PLUGIN_PROMPT.md`](docs/AI_PLUGIN_PROMPT.md)
- 示例：
  - `plugins/example.hello/`：定时问候，演示 say / every / settings / events。
  - `plugins/example.screen_note/`：屏幕观察补充，演示 screen.context。

插件默认不运行，需要在右键菜单 →「插件管理」里手动启用。

## 屏幕观察的隐私说明

- 默认 `auto` 模式在免费公共通道下只读取窗口标题，不截屏。
- 选择 `vision` 或 `ocr` 才会截取主屏幕；截图只在内存中压缩，不写入磁盘。
- OCR 使用 Windows 自带 OCR，识别过程在本机；临时图片放在 `C:\Users\Public\LFELKEEA\ocr`，结束后删除。
- 发送给 AI 的内容取决于模式：`window` 只发窗口标题/进程名，`ocr` 发识别文字，`vision` 发压缩截图。
- 命中密码、支付、银行、验证码、无痕浏览、远程桌面等关键词，或前台是全屏应用时，默认跳过。
- 屏幕观察默认关闭，必须由用户主动开启。

## 许可证

本项目代码使用 MIT 许可证，见 [`LICENSE`](LICENSE)。

`assets/models/paraformer_zh_small/` 是第三方语音识别模型，遵循其原始许可；重新分发前请自行确认模型来源的授权条款。
