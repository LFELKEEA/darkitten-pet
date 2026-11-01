# QQ 桥接（SnowLuma）

把 QQ 私聊和群聊接进桌宠：人格卡、长期记忆、黑话库、白名单、盯人即时回复、按聊天习惯回复、收藏表情、QQ 空间与评论自动回复、SnowLuma 与 GPT-SoVITS 自动启停、聊天图片识别，以及 Windows 本地语音 / GPT-SoVITS 自定义音色的 TTS 语音回复。1.7.0 起还可以和「活人感内核」联动，让回复带上她当前的状态和按话题召回的记忆。

插件本身不包含 SnowLuma，也不代替 QQ 登录。它通过 SnowLuma 的 OneBot 11 HTTP 接口收发消息。

## 需要什么

- Windows 桌面版 QQ（NT 架构）
- SnowLuma **2.2.1 或更高版本**，官方 Releases：
  [https://github.com/SnowLuma/SnowLuma/releases](https://github.com/SnowLuma/SnowLuma/releases)
- Windows 部署说明：
  [https://snowluma.github.io/zh/docs/guide/deploy/windows](https://snowluma.github.io/zh/docs/guide/deploy/windows)
- 桌宠 1.1.1 或更高版本（建议 1.2.1，修复了 DeepSeek 思考模式导致的空回复）
- 可选：同目录的「活人感内核（姐妹人格与记忆）」插件，用于状态、长期记忆和节律联动

SnowLuma 的许可允许个人使用，但不允许把它的原生组件打包进第三方安装包。所以这里只给官方下载链接，不附带 SnowLuma 文件。

## 安装

1. 从插件库下载 `lfelkeea.qq_bridge-1.7.0.zip`。
2. 解压得到 `lfelkeea.qq_bridge` 文件夹，整个放进桌宠的插件目录。
   - 右键桌宠 →「插件管理」→「打开插件目录」，或手动放到 `data/plugins/`。
3. 回到插件管理，点「重新扫描」，勾选「QQ 桥接（SnowLuma）」启用。
4. 启用后桌宠会说一句「QQ 桥接已启动」，插件卡片上出现「设置」按钮。

> 可视化设置面板需要桌宠 **1.1.1 或更高版本**。旧版桌宠没有插件设置按钮，只能手动改 `data/config.json`。

## 配置 SnowLuma

1. 解压并运行 SnowLuma，按它的提示登录 QQ。
2. 打开 WebUI（默认 `http://127.0.0.1:5099`）。
3. 确认 OneBot HTTP 服务在 `127.0.0.1:3000` 上开启，记下它的 `accessToken`。
4. 新建一个 **HTTP 事件上报（httpClients）**，地址填：
   `http://127.0.0.1:8766/onebot`
   - `messageFormat` 选 `array`（插件也兼容 `string`）。
   - `reportSelfMessage` 保持关闭，避免机器人回复自己。
   - 如果给这个上报项设置了 `accessToken`，把同样的值填到插件的 `event_secret`；SnowLuma 会用 HMAC-SHA1 给请求签名。
   - 如果上报项不设令牌，插件的 `event_secret` 留空即可（服务只监听 `127.0.0.1`）。
5. 在 SnowLuma 里重新加载 OneBot 配置，或在 WebUI 里点保存。

对应的 `onebot_<QQ号>.json` 片段大致是这样（键名以你当前 SnowLuma 版本为准）：

```json
{
  "networks": {
    "httpServers": [
      {
        "name": "http-default",
        "host": "127.0.0.1",
        "port": 3000,
        "path": "/",
        "enableWebSocket": false,
        "accessToken": "HTTP_TOKEN",
        "messageFormat": "array",
        "reportSelfMessage": false
      }
    ],
    "httpClients": [
      {
        "name": "darkitten",
        "enabled": true,
        "url": "http://127.0.0.1:8766/onebot",
        "accessToken": "EVENT_SECRET",
        "timeoutMs": 5000,
        "messageFormat": "array",
        "reportSelfMessage": false
      }
    ]
  }
}
```

HTTP 服务和 WebSocket 服务的令牌是分别生成的，不要混用。插件只走 HTTP，不需要 WebSocket。

## 插件设置

桌宠 1.1.1 起，插件管理里点「设置」即可打开可视化面板：

| 页签 | 内容 |
| --- | --- |
| 连接 | SnowLuma 地址、accessToken、主人 QQ、本机监听端口和事件路径；可一键检测连接、复制事件地址；可设置自动启动 SnowLuma，并可接管重启已运行的实例 |
| 白名单 | 好友和群的放行名单增删，可为每个号添加文字备注；单独的盯人名单，命中后不看 @ 和接话概率 |
| 人格 | 编辑人格卡、查看字数、恢复默认 |
| 记忆 | 查看/添加/删除长期记忆，手动触发整理 |
| 黑话 | 查看/添加/删除词条和出现次数 |
| 习惯 | 各会话的消息数、短句率、问句、表情和最后活跃时间 |
| QQ 空间 | 开关、可见范围、指定好友、发布、读取和删除说说；自动回复最近说说下的新评论 |
| 高级 | 机器人名字、触发方式、回复概率、字数、表情、收藏表情、记忆/黑话上限等 |
| 语音 | 选择 Windows 本地语音或 GPT-SoVITS，试听、发送 TTS 语音和自动语音概率 |
| 诊断 | 运行状态、测试发送、插件日志 |

点「保存并应用」后，连接类字段变化会自动重建客户端和事件监听；白名单、人格、记忆、黑话是点按钮立即生效。

设置最终保存在桌宠的 `data/config.json`：

`plugins.settings.lfelkeea.qq_bridge`

也可以手动编辑，改完在插件管理里点「重载」或重启桌宠。自动启动、程序目录和图片识别会额外备份到插件的状态目录 `data/plugins/.state/lfelkeea.qq_bridge/settings.json`，避免桌宠退出时用旧配置覆盖；改配置时以这里为准。

| 设置 | 默认 | 说明 |
| --- | --- | --- |
| `snowluma_http` | `http://127.0.0.1:3000` | SnowLuma 的 OneBot HTTP 地址 |
| `access_token` | 空 | 对应 httpServers 的 `accessToken` |
| `owner_qq` | 空 | 主人 QQ，只有这个号能用指令 |
| `allow_private` | `[]` | 允许私聊的好友 QQ |
| `allow_groups` | `[]` | 允许回复的群号 |
| `allow_notes` | `{}` | 白名单备注，键为 `private:QQ` 或 `group:群号`，只用于显示 |
| `watch_users` | `[]` | 盯人名单。名单里的 QQ 私聊直接回，不受接话概率限制；在群里只对白名单群生效 |
| `http_host` | `127.0.0.1` | 事件服务监听地址，建议不要改成 `0.0.0.0` |
| `http_port` | `8766` | 事件服务端口；被占用时会自动往后试 5 个端口 |
| `event_path` | `/onebot` | 事件上报路径 |
| `event_secret` | 空 | 对应 httpClients 的 `accessToken`，用于校验 `X-Signature` |
| `bot_name` | `小猫` | 群聊里叫这个名字也会触发回复 |
| `command_prefix` | `/` | 指令前缀 |
| `group_trigger` | `at` | `at`：只有 @ 或叫名字才回；`all`：按 `reply_chance` 随机接话 |
| `reply_chance` | `0.15` | `group_trigger=all` 时的接话概率 |
| `max_replies_per_minute` | `6` | 每分钟最多回几条，防止刷屏 |
| `max_reply_chars` | `300` | 单条回复最大字数 |
| `auto_face` | `true` | 按习惯附带 QQ 表情 |
| `face_ids` | `["14","13","178","179"]` | 可随机使用的 QQ 表情 id |
| `face_probability` | `0.03` | 自动补表情的基础概率 |
| `sticker_paths` | `[]` | 表情包图片路径列表，路径要能被 SnowLuma 读到 |
| `sticker_probability` | `0.03` | 发一条回复时附表情包的概率 |
| `memory_enabled` | `true` | 开启长期记忆整理 |
| `memory_interval_minutes` | `30` | 两次自动整理的最小间隔 |
| `memory_min_batch` | `8` | 至少积累多少条消息才整理 |
| `memory_batch_size` | `40` | 每次最多整理多少条 |
| `memory_max_items` | `80` | 长期记忆上限 |
| `slang_enabled` | `true` | 开启黑话学习 |
| `slang_max_terms` | `200` | 黑话库上限 |
| `habit_enabled` | `true` | 统计对方的消息长度、问句、表情和活跃时间 |
| `local_echo` | `false` | 发出 QQ 消息时让桌宠也念一遍 |
| `announce` | `false` | 启动时额外说一句上线提示 |
| `qzone_enabled` | `false` | 是否允许发 QQ 空间 |
| `qzone_permission` | `friends` | `public` / `friends` / `some` / `private` / `hide` |
| `qzone_target_uins` | `[]` | 仅 `some` 权限时可见的 QQ 号 |
| `qzone_auto_reply` | `false` | 自动回复自己最近说说下的新评论 |
| `qzone_reply_interval` | `10` | 两次检查新评论的间隔，单位分钟 |
| `qzone_reply_max` | `3` | 每轮最多回复几条评论 |
| `vision_enabled` | `true` | 识别 QQ 图片：接口支持视觉时直接发图，否则用本地 OCR 兜底 |
| `tts_engine` | `windows` | TTS 引擎：`windows`（系统语音）或 `gpt_sovits`（本地 GPT-SoVITS API） |
| `tts_windows_voice` | 空 | Windows 语音名，例如 `Microsoft Huihui Desktop`；留空用系统默认 |
| `tts_rate` | `0` | Windows 语音语速，范围 -10 到 10 |
| `tts_volume` | `100` | Windows 语音音量，范围 0 到 100 |
| `tts_gpt_sovits_url` | `http://127.0.0.1:9880` | GPT-SoVITS `api_v2.py` 地址，插件会自动补 `/tts` |
| `tts_sovits_dir` | 空 | GPT-SoVITS 整合包根目录（含 `api_v2.py` 和 `runtime\python.exe`），留空自动查找 `D:\GPT-SoVITS`、`C:\GPT-SoVITS` 等位置 |
| `tts_auto_start` | `true` | 插件启用时自动在后台启动 GPT-SoVITS（隐藏窗口，随桌宠退出） |
| `tts_ref_audio` | 空 | 参考音频的本地路径，用于零样本音色克隆 |
| `tts_prompt_text` | 空 | 参考音频里说的内容，要和音频一致 |
| `tts_prompt_lang` | `zh` | 参考音频语言：`zh` / `en` / `ja` / `ko` / `yue` / `auto` |
| `tts_text_lang` | `zh` | 要合成的文字语言，取值同上 |
| `tts_speed` | `1.0` | GPT-SoVITS 语速倍率，范围 0.5 到 2.0 |
| `tts_reply_enabled` | `false` | AI 回复时按概率附带一条 TTS 语音 |
| `tts_reply_probability` | `0.08` | 自动附带 TTS 语音的概率 |
| `tts_max_chars` | `120` | 单条 TTS 最多合成多少字 |
| `custom_face_enabled` | `false` | AI 回复时按概率附带一个收藏表情 |
| `custom_face_probability` | `0.05` | 附带收藏表情的概率 |
| `custom_face_ids` | `[]` | 指定可用的收藏表情 id；留空则使用已读取到的全部收藏表情 |
| `auto_start_snowluma` | `true` | 插件启用时自动在后台启动 SnowLuma（无窗口） |
| `snowluma_dir` | 空 | SnowLuma 所在目录，留空自动查找（如 `D:\SnowLuma`） |

## 自动启动 SnowLuma

在「连接」页勾选「插件启用时在后台启动 SnowLuma」，并确认程序目录（留空会自动查找 `D:\SnowLuma`、`C:\SnowLuma`、用户目录下的 `SnowLuma` 等位置）：

- 插件加载时先检查 OneBot HTTP 是否已通；已通就直接用，不会重复启动。
- 没通且 SnowLuma 进程没在运行：用 `node.exe index.mjs` 在后台启动，不弹控制台窗口，输出写到插件状态目录的 `snowluma-stdout.log`。
- SnowLuma 进程在运行、但 OneBot HTTP 端口没监听：插件不会自动结束它，只写日志提示。到「连接」页点「接管并重启」可以在确认后结束这个 `node.exe`，再由插件在后台启动。
- 关闭插件、重载插件或退出桌宠时，**由插件启动的** SnowLuma 会被一起结束。你自己手动启动的实例不会被接管，也不会被关闭。
- 桌宠异常退出时，插件用 Windows 作业对象（Job Object）兜底，系统会一并结束它启动的 SnowLuma。

第一次自动启动如果 QQ 还没登录，打开 `http://127.0.0.1:5099` 完成扫码即可；插件会继续等端口就绪，并在连上后自动检测连接。

## 自动启动 GPT-SoVITS

「语音」页选中「GPT-SoVITS 本地 API」后，可以勾选「插件启用时在后台启动 GPT-SoVITS（隐藏窗口，不弹控制台）」：

- 安装目录留空时自动查找 `D:\GPT-SoVITS`、`C:\GPT-SoVITS`、`E:\GPT-SoVITS`、用户目录、桌面和下载目录；也可以点「自动检测」或「选择」指定。
- 目录判定标准是同时存在 `api_v2.py` 和 `runtime\python.exe`，所以填 `D:\GPT-SoVITS` 或 `D:\GPT-SoVITSpp` 都可以。
- 启动命令是整合包自带的 `runtime\python.exe api_v2.py -a 127.0.0.1 -p <地址里的端口> -c GPT_SoVITS/configs/tts_infer.yaml`，工作目录就是整合包根目录，控制台窗口是隐藏的；输出写到插件状态目录的 `sovits-stdout.log`。
- 第一次加载模型约 20~40 秒；合成前如果发现端口没通，插件会先拉起服务并等它就绪，合成不会因为没开服务直接失败。
- 「音色」下拉列表会扫描整合包和它上一级目录里的 `refs\*.wav`，如果旁边有同名 `.txt`，选中后会自动填好参考文字。换音色只要选中对应音频，再点「保存」。
- 关闭插件、重载插件或退出桌宠时，**由插件启动的** GPT-SoVITS 会被一起结束，不会在后台残留占显存的进程。你自己手动开着的服务不会被接管。
- 「启动服务」「停止服务」按钮可以手动控制；手动启动会忽略自动启动开关。

## TTS 语音

「语音」页把文字合成为 WAV，再交给 SnowLuma 转成 QQ 语音。不再使用语音包，也不需要提前准备一批录音。

### Windows 本地语音

默认引擎，开箱即用：

1. 「合成方式」选「Windows 本地语音」。
2. 点「刷新音色」，插件会列出系统已安装的 SAPI 语音；中文 Windows 通常自带 `Microsoft Huihui Desktop`。
3. 调整语速和音量，输入文字点「试听」，确认没问题后选白名单对象点「发送语音」。

### GPT-SoVITS 自定义音色

想用特定音色（例如大飞鱼少女音）时，选「GPT-SoVITS 本地 API」。插件只负责调用接口，不附带模型或音频：

1. 按 [GPT-SoVITS 官方文档](https://github.com/RVC-Boss/GPT-SoVITS) 准备好整合包，默认监听 `http://127.0.0.1:9880`。
2. 在「安装目录」填整合包根目录（留空会自动查找），勾选后台自动启动；插件会自己拉起 `api_v2.py`，不需要手动开窗口。
3. 「音色」下拉选择 `refs` 里的参考音频，或者直接填「参考音频」路径；旁边有同名 `.txt` 时参考文字会自动填好。
4. 如果不是同名 `.txt`，在「参考文字」里填写这条音频实际说的内容，语言要和「参考语言」一致；否则音色会跑偏。
5. 「合成语言」选要读的文字语言，「语速倍率」按需调整，然后试听或发送。

音色素材可以参考 ModelScope 上的
[大飞鱼少女音数据集](https://modelscope.cn/datasets/YinZilu/da-fei-yu-girl-voice-zh)。
它是 **CC BY-NC 4.0** 的数据集，只提供参考音频，不是在线 TTS 接口，需要本机 GPT-SoVITS 推理才能用。请仅用于个人非商业用途，不要冒充他人或用于诈骗。

### 自动语音与指令

- 打开「AI 回复时按概率附带一条 TTS 语音」后，模型回复会按 `tts_reply_probability` 附带一条语音。
- 模型在回复末尾写 `[voice]` 或 `[语音]` 时，必定尝试合成并发送；关闭自动语音后仍可用手动发送。
- 主人可以在 QQ 里发 `/voice 要读的文字` 给当前会话发 TTS 语音，或 `/voice group:123456 要读的文字` 指定对象。

合成出来的 WAV 保存在插件状态目录 `tts/` 下，只保留最近 20 个；SnowLuma 读取后转码，不上传到插件服务器。参考音频、模型和数据集都由你自行准备并确认授权。

## 聊天图片识别

「高级」页默认勾选「识别聊天里的图片」。白名单里的好友或群发来图片时：

1. 插件从消息段里取图片地址，下载后转成 data URI，连同文字一起交给桌宠配置的 AI 接口。
2. 如果当前模型不支持图片（接口返回 `image`、`vision`、`invalid content` 之类的错误），插件会自动退回文字模式，并改用 Windows 自带的 OCR 识别图片里的文字再回复；识别不到文字就按「对方发来一张图片」处理。
3. 直连图片需要 AI 通道支持视觉，例如 `qwen-vl`、`gpt-4o`、`glm-4v` 等模型。免费公共通道和纯文本模型只能走 OCR 兜底。
4. 图片只在本机下载，随后发给桌宠配置的 AI 接口；不会保存到插件目录。不想发图就在「高级」页取消勾选。

## 第一次使用

1. 在插件管理里点「设置」→「连接」，填好 SnowLuma 地址、accessToken 和主人 QQ，点「保存并应用」。
2. 点「检测连接」，看到「已连接：昵称（QQ号）」就说明打通了。
3. 到「白名单」页添加要回复的好友和群；主人 QQ 自动放行。
4. 在 QQ 里私聊机器人，发 `/status`，确认运行状态。
5. 私聊里直接说话即可；群里默认要 @ 机器人，或者消息以 `小猫` 开头。
6. 不想要某个对象时，在「白名单」页删掉，或发 `/allow del ...`。

白名单为空时插件不会主动回复任何人（主人私聊除外）。群聊仍要显式加进群白名单。

## 盯人

「白名单」页下方有一个单独的「盯人」分组，填 QQ 号后点「开始盯」即可。它解决的是「这个人一说话就要回」的场景：

- 被盯的账号私聊时，不需要 @、不需要叫名字、也不受 `reply_chance` 和 `max_replies_per_minute` 限制，直接生成回复。
- 被盯的账号在白名单群里发言时同样直接回复；不在白名单的群仍然不会响应，避免在陌生群里刷屏。
- 盯人名单里的账号即使不在好友白名单里，也能直接私聊。
- 同一个会话正在生成回复时仍会去重，不会因为连发几条消息而重复排队。
- 盯人只影响「要不要回」，人格卡、记忆、黑话和回复长度设置照常生效。

## 指令

默认前缀 `/`。只有 `owner_qq` 发的指令会被执行。

| 指令 | 作用 |
| --- | --- |
| `/help` | 显示指令 |
| `/status` | 查看连接、端口、白名单、记忆和黑话数量 |
| `/check` | 立刻测试 SnowLuma 连接 |
| `/persona` | 查看人格卡 |
| `/persona set 文字` | 修改人格卡 |
| `/persona reset` | 恢复插件自带的人格卡 |
| `/memory list` | 查看长期记忆 |
| `/memory add 内容` | 手动记一条 |
| `/memory del 关键词` | 删除匹配的记忆 |
| `/slang list` | 查看黑话库 |
| `/slang add 词=含义` | 手动加一条黑话 |
| `/slang del 词` | 删除黑话 |
| `/allow list` | 查看白名单和备注 |
| `/allow add 123456` | 加好友 |
| `/allow add 123456 备注` | 加好友并写备注 |
| `/allow add group:123456` | 加群 |
| `/allow add group:123456 备注` | 加群并写备注 |
| `/allow del 123456` | 移出白名单 |
| `/say group:123456 内容` | 手动往白名单群发一条 |
| `/say private:123456 内容` | 手动往白名单好友发一条 |
| `/voice 要读的文字` | 给当前会话发一条 TTS 语音 |
| `/voice group:123456 要读的文字` | 指定对象发一条 TTS 语音 |
| `/qzone 内容` | 发一条说说（需先开启） |
| `/qzone on` / `/qzone off` | 开关 QQ 空间发送 |
| `/qzone list` | 查看最近的说说 |
| `/qzone del tid` | 删除一条说说 |
| `/compact` | 立刻整理记忆和黑话 |

`/say` 和 `/qzone` 只接受白名单里的对象；`/qzone` 还要求 `qzone_enabled` 为 `true`。

## 活人感内核（可选）

1.7.0 起可以读取「活人感内核（姐妹人格与记忆）」导出的状态和记忆，让 QQ 里的回复更像同一个人。两边只通过本机文件通信，不导入对方代码：

| 文件 | 方向 | 用途 |
| --- | --- | --- |
| `data/plugins/.state/lfelkeea.alive_core/export/context.json` | 内核 → 桥接 | 名字、当前状态、最近的自我认识、记忆规则 |
| `data/plugins/.state/lfelkeea.alive_core/export/memories.json` | 内核 → 桥接 | 可召回的长期记忆 |
| `data/plugins/.state/lfelkeea.alive_core/inbox.jsonl` | 桥接 → 内核 | 聊天情节、召回回执、更正、好奇和状态事件 |

启用后，每次回复会按当前消息召回最多 N 条相关记忆（默认 5 条），并把「她现在的状态」「她最近的自我认识」拼进系统提示词，同时要求模型只使用写出来的内容、不确定就说记不清。聊天结束后，主人和盯人对象的发言、AI 的回复、真正用到的记忆都会写回内核，供它做巩固和下一次召回。

在「记忆」页可以：

- 关闭「接入活人感内核」，立即退回原来的人格卡 + 本地记忆逻辑；
- 调整「每次最多召回」的条数（0 表示只带状态、不召回记忆）；
- 关闭「把聊天写回内核」，只读不写。

内核没安装、没启用，或导出超过 10 分钟没更新时，桥接会自动跳过联动，不会报错，也不会阻塞回复。

## 人格卡、记忆和黑话

- **人格卡**：`data/plugins/.state/lfelkeea.qq_bridge/persona.md`，也可以直接用 `/persona set` 改。默认是一只说话简短的桌面小猫。
- **长期记忆**：插件把聊天记录攒到一定数量后，交给桌宠当前配置的 AI 整理成短句，存进 `memory.json`。只记稳定的事实、偏好、称呼和约定，不记寒暄。
- **黑话库**：整理时顺带收集有特殊含义的 2 到 8 字词，存进 `slang.json`；也可以手动 `/slang add`。
- **聊天习惯**：`habits.json` 记录每个会话的消息长度、问句比例、感叹号、省略号、表情数量和活跃小时。回复时会把摘要写进提示词，并按习惯决定要不要补表情。
- **最近对话**：`runtime.json` 只保留最近几条，用于接上下文；不会无限增长。

所有数据都在本机 `data/plugins/.state/lfelkeea.qq_bridge/`，不上传。删除这个目录等于清空人格卡、记忆、黑话和习惯。

## 表情和表情包

- QQ 表情用 `face_ids` 里的 id，例如 `14` 是微笑。模型偶尔也会自己输出 `[face:14]`，插件会解析成真正的表情段。
- 表情包用 `sticker_paths`，填本机图片路径，例如 `C:/Users/你/Pictures/sticker.png`。路径必须能被 SnowLuma 读取；如果发出去是裂图，先确认 SnowLuma 有权限访问该文件。
- 自动补表情的概率取 `face_probability` 和「对方历史表情比例的一半」中的较大值，最高 0.5，不会每条都带。
- **收藏表情**：在「高级」页点「读取收藏表情」，插件会通过 SnowLuma 的 `fetch_custom_face_detail` 拉取你 QQ 里收藏的表情并列出。勾选「AI 回复时按概率附带一个收藏表情」，再按需要勾选只使用哪几个；不勾选具体表情时，从全部已读取的收藏表情里随机挑。回复发出后会按 `custom_face_probability` 的概率单独补发一条收藏表情。收藏表情只在本地列表里保存，不会上传；如果读取失败，先确认 SnowLuma 已登录且版本支持收藏表情接口。

## QQ 空间

QQ 空间默认关闭。开启方式：

- 在设置里把 `qzone_enabled` 改成 `true`，或在 QQ 里发 `/qzone on`。
- 用 `qzone_permission` 控制可见范围，默认 `friends`。
- 只有白名单里的主人能触发，普通聊天不会自动发说说。

「QQ 空间」页还可以打开「自动回复我最近说说下的新评论」：

- 插件每隔 `qzone_reply_interval` 分钟检查一次自己最近 5 条说说，读取评论区里还没回复过的评论。
- 第一次打开时只建立基线，把当时已有的评论记为「已读」，不会突然补回一堆旧评论；之后出现的新评论才会回复。
- 每轮最多回复 `qzone_reply_max` 条，避免触发风控；已回复的评论 id 会记在本地，重启后不会重复回复。
- 回复内容由当前人格卡、长期记忆和黑话库生成，限制在 60 字以内，只写一句自然的话。
- AI 失败、返回空内容或评论发送失败时不会标记为已回复，下一轮会再试。

发说说和自动回复都依赖 SnowLuma 的 QZone 动作。如果报「QQ 空间操作失败」，先确认 SnowLuma 版本支持 `send_qzone_msg`，并查看 SnowLuma 日志；QQ 空间接口受腾讯风控影响，失败时不要连续重试。

## SnowLuma 日志出现 401

如果 SnowLuma 日志反复出现：

```
[httppost-1] POST http://127.0.0.1:8766/onebot returned 401
```

说明插件拒收了事件签名。SnowLuma 2.2.1 发送的 `X-Signature` 带 `sha1=` 前缀，1.1.0 及更早版本只认裸十六进制，于是所有事件都被丢掉，表现就是「人在白名单里，但收不到任何回复」。升级到 **1.1.1** 后，在插件管理里点一次「重载」即可，不需要改任何 Token。

## OneBot 3003 端口没监听

SnowLuma 的日志里如果出现 `listen EADDRINUSE: address already in use 127.0.0.1:3003`，通常是 QQ 会话短时间内重连了两次：第二个实例绑不上端口，第一个实例也已经退出，最后 3003 没有任何监听。处理办法：

1. 在 SnowLuma WebUI 里保存一次 OneBot 配置，它会热重载网络层并重新绑定端口。
2. 或者在「连接」页点「接管并重启」：确认后插件会结束旧的 `node.exe`，在后台重新启动一个由插件托管的实例。

## 安全边界

- 插件以你的 Windows 账户权限运行，能执行 Python 代码本身能做到的事。只启用你信得过的插件。
- 强烈建议保持 `http_host=127.0.0.1`，不要把事件端口暴露到局域网或公网。
- 白名单是唯一的外发闸门；不要把不认识的群加进去。
- 插件通过桌宠的 `api.chat` 调用 AI，读不到明文 API Key，也不会把 Key 写进日志或数据文件。
- 开启图片识别后，白名单会话里的图片会被下载并发给桌宠配置的 AI 接口；不需要时在「高级」页关掉。
- 插件不会自动同意好友、不会自动进群、不会代替你发好友申请。

## 限制

- 只支持 OneBot 11 HTTP，不支持 WebSocket；这是为了不依赖额外 Python 包。
- SnowLuma 的原生 hook 与 QQ 版本绑定。QQ 自动升级后如果 hook 失效，先看 SnowLuma 的版本兼容说明。
- 一个 SnowLuma 实例对应一个 QQ 号；多账号需要分别配置。
- 群聊默认只响应 @ 或叫名字；想让它随机接话，把 `group_trigger` 改成 `all` 并调低 `reply_chance`。
- 记忆整理依赖 AI，AI 不可用时只会记录聊天，不会自动总结。
- 活人感联动依赖内核插件正在运行；内核停止或卸载后，QQ 桥接会自动退回本地人格卡和记忆。
- GPT-SoVITS 音色克隆需要本机自行部署模型和推理服务；Windows 本地语音只能使用系统已安装的 SAPI 音色。
- 插件不包含 SnowLuma；请从官方 Releases 下载，并遵守它的许可。

## 更新记录

- **1.7.0**：新增「活人感内核」联动。回复会按当前话题召回内核里的长期记忆，并带上她当前的状态和最近的自我认识；聊天情节、AI 回复、召回回执、`/memory add|del` 会更正会写回内核。内核未安装或导出过期时自动降级，原逻辑不变。「记忆」页新增接入开关、召回条数和写回开关。
- **1.6.0**：GPT-SoVITS 不再需要手动开控制台。「语音」页新增安装目录（自动检测/选择）、后台自动启动、服务启停和音色下拉；插件用整合包自带的 `runtime\python.exe` 隐藏窗口启动 `api_v2.py`，随插件和桌宠一起退出；合成前自动等待服务就绪；音色列表扫描 `refs\*.wav` 并读取同名 `.txt` 作为参考文字。新增设置 `tts_sovits_dir`、`tts_auto_start`。
- **1.5.0**：移除语音包导入与随机发送，改为 TTS 语音合成。新增 Windows 本地语音引擎（开箱即用，可列出系统音色、调语速和音量）和 GPT-SoVITS 本地 API 引擎（用参考音频做零样本音色克隆，支持中/英/日/韩/粤语）；「语音」页可试听和手动发送，AI 回复仍支持按概率附带语音和 `[voice]` 标记，`/voice` 指令改为读取指定文字。
- **1.4.6**：新增盯人名单，被盯的 QQ 私聊或在白名单群发言时直接回复，不受 @ 和接话概率限制；QQ 空间新增「自动回复最近说说下的新评论」，首次开启只建基线，之后按间隔检查并逐条回复，已回复评论会去重；新增收藏表情发送，可在「高级」页读取并勾选收藏表情，AI 回复后按概率补发一条。
- **1.4.5**：修复记忆整理提示“接口返回了空回复”。DeepSeek 默认开启思考模式，思维链会占掉回复的 token 预算；桌宠 1.2.1 起会在 DeepSeek 模型上关闭思考模式，空回复错误也会带上 finish_reason 和 token 诊断。插件侧遇到空回复或非 JSON 时，会用更短的输入和更少的条数自动重试一次，仍失败则保留聊天记录。
- **1.4.4**：白名单里的好友和群可以添加文字备注。设置面板新增备注输入框和「保存备注」，列表会显示「号码 · 备注」；新增时可直接带备注，删除条目会一并清理备注。`/allow add 123456 备注` 和 `/allow list` 也支持备注，备注不参与白名单匹配，旧配置无需迁移。
- **1.4.3**：修复自动启动或接管后弹出 SnowLuma 控制台窗口的问题。改用隐藏控制台方式启动，SnowLuma 内部再启动的 node 子进程也不会再弹出 Windows Terminal；插件卸载、关闭桌宠或桌宠异常退出时，仍由作业对象结束整棵 SnowLuma 进程树。
- **1.4.2**：修复记忆整理失败与记录被误清空的问题：模型输出被截断或夹杂解释时会先抢救能读到的 JSON；模型完全没有返回 JSON 时保留聊天记录并提示原因；网络抖动、限流和 5xx 会自动重试一次；单次整理条数下调，避免输入过长导致超时。「记忆」页新增「立即整理」，「习惯」页可以选择会话删除统计。
- **1.4.1**：设置窗口改为无边框可拖动，标题栏任意位置都能拖；右下角可拉伸大小；窗口打开时按屏幕可用区域自动收缩并居中，不会再跑到屏幕外；十个标签页统一可滚动，窗口调小也不会被内容顶破。
- **1.4.0**：新增语音包导入与本机扫描、发送 QQ 语音，AI 回复可按概率自动带语音。
- **1.3.0**：新增聊天图片识别（视觉接口 + Windows OCR 兜底）。
