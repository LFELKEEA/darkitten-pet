# 自开发工坊（lfelkeea.self_dev）

让桌宠自己拿工具写代码、做插件，并且越用越顺手。

启用后，右键桌宠 →「插件管理」→ 找到「自开发工坊（Agent 工具链）」→ 点「设置」，就会打开自开发工作台。

## 它能做什么

- 在专属开发区里读文件、搜代码、改文件、跑命令、查语法、读网页。
- **技能**：把“这种情况该怎么做”沉淀成可复用的做法库，下次直接读；本地没有合适的，就自己上 GitHub 搜索、浏览仓库里的 `SKILL.md` 并安装回来。技能用勾选框开关，关闭后不进入系统提示，也不需要删除。
- **知识库**：把资料、旧结论、任务总结存进本地文件，用中文或代码关键词检索，并保留 `[K文档#片段]` 引用。
- **连接器**：接入 HTTP API，或按需启动 MCP stdio 服务器，把外部能力带进任务；缺连接器时会搜 MCP 仓库、读 README、推断 `npx` / `uvx` 启动命令后安装。连接器同样用勾选框开关。
- **自带目录**：插件内置 44 个技能和 42 个连接器，覆盖 GitHub、GitLab、飞书、Notion、SearXNG、只读 SQL 网关、Context7、Playwright、Blender、各语言包管理器和常用 MCP；也包含 Word / PDF / Excel / PPT 处理、长文写作、知识库、draw.io、技术图表、图像生成、联网调研、本地文件搜索、自然化写作，以及 OpenAI 兼容 API、Tavily、arXiv、Crossref、OpenAlex、维基百科。全部默认关闭，勾选后才生效。
- **依赖检查**：启用后可以先查环境变量、启动命令和下载资源；环境变量可本机保存，界面只显示「已配置 / 缺失」，代理只看到名称和状态。MCP 的 npm / PyPI 包、Playwright 浏览器和技能声明的资源可以在确认后预下载。
- **申请启用**：代理做到一半发现需要某个关闭的技能或连接器时，会说明理由并弹窗申请打开；主人同意后才启用。
- **联网检索**：不熟悉的库、报错、版本差异，可以搜网页、GitHub 仓库和 PyPI 包。
- 不限制步数和输出长度：一直做到任务完成，而不是数到 24 步就收工。
- 把成果直接做成新的桌宠插件：写入 `data/plugins/<id>/`，通过检查后自动启用并重载。
- 改动自动记账：工作台里点「一键还原到初始状态」就能退回代理第一次动手之前的版本；还原前的当前状态也会先备份。
- 已安装的插件可以「导出插件」成 ZIP，方便备份、分享或投稿到插件市场。
- 每一步都实时显示；命令执行、连接器调用、删除和安装插件默认要主人点「允许」。
- 每次任务留下 JSONL 会话日志，改了哪些文件、失败在哪一步都能回看。

它不是一个“随机生成代码”的按钮，而是一套会先读现状、再查资料、再改、改完验证的 Agent 循环：

```text
任务 → 看技能（缺就上网找）→ 查知识库/联网 → 读文件 → 计划 → 改代码 → 检查/运行 → 如实总结
```

## 开发区

插件第一次运行时自动创建：

```text
data/dev/
├─ README.md          使用说明（自动生成）
├─ workspace/         自由项目区，所有相对路径的根
├─ drafts/            插件草稿
├─ skills/            技能库，每个技能一个目录，入口是 SKILL.md
├─ knowledge/         本地知识库（knowledge.json）
├─ connectors.json    HTTP API / MCP 连接器定义
├─ env.json           本机环境变量（明文，权限尽量收紧，不要分享）
├─ sessions/          每次任务的 JSONL 日志
├─ .backups/          覆盖文件、更新插件前的自动备份
├─ .changes/          改动账本与首次修改前的基线（一键还原用）
└─ .trash/            删除技能、连接器、知识文档时的本地回收目录
```

- 工具只接受相对路径，拒绝盘符、绝对路径、`..` 和越界软链接。
- 写文件前自动备份旧版本；更新插件前备份整个旧插件目录（保留最近 8 份）。
- 每个文件/插件只在第一次被修改前记录一份基线；还原时按基线退回，由代理新建的条目会被移除。
- 一键还原前，当前状态会先搬到 `.backups/restore/<时间戳>/`，所以还原本身也可以再找回。
- 不要在插件目录里放长期数据，升级会覆盖它；用 `api.storage` 或 `data/dev/`。

## 技能、连接器、知识库

工作台顶部有「开发专区」和「技能 / 连接器 / 知识库」两个入口：前者直接打开内置开发能力分组，后者打开完整资源管理界面，也可以让代理自己维护。

### 开发专区

「开发专区」把插件自带的 44 个技能和 42 个连接器按用途分成五组，每组一个独立界面：

| 分组 | 内容 | 数量 |
| --- | --- | --- |
| 编程核心 | 代码评审、测试、调试、Git、发布、安全、工程规范等 | 26 个技能 |
| 文档与创作 | Word / PDF / Excel / PPT、长文、知识库、draw.io、技术图表、图像、调研、本地搜索、自然化写作 | 12 个技能 |
| API 专用 | GitHub / 飞书 / Notion / SQL / SearXNG / 网页抓取 | 6 个技能 |
| HTTP 连接器 | GitHub、包管理器、模型与搜索 API、论文与百科等 | 26 个连接器 |
| MCP 连接器 | Context7、文件系统、Git、SQLite、Playwright、Blender 等 | 16 个连接器 |

- 「一键开启推荐套装」：打开 10 个常用开发技能和 7 个常用连接器（列表里带 ★ 标记）。
- 「全部开启」：打开全部 44 个技能和 42 个连接器。
- 「全部关闭」：把这批内置条目全部关掉。
- 每组的「开启本组 / 关闭本组」只影响当前分组；列表里的勾选框可以单独开关，双击可以编辑。
- 开启只改开关，不会自动申请密钥或安装运行时；缺什么到「环境与依赖」里处理。
- 资源界面的「技能」「连接器」两页只放你自己创建的条目，内置开发条目统一收在开发专区。

### 技能

技能是一个带 `SKILL.md` 的目录，文件头用 YAML 写 `name`、`description` 和 `enabled`，正文写适用场景、步骤和检查点。首次运行会放入三个示例技能：

- `pet-plugin-craft`：桌宠插件开发流程；
- `verify-before-finish`：交付前自检；
- `research-before-coding`：先查官方文档再写代码。

技能默认关闭。资源界面左侧每个技能前都有勾选框，勾选即启用，取消即关闭；`set_skill_enabled(skill_id, true/false)` 也可以让代理自己切换。关闭的技能不会进入系统提示，`read_skill` 也会拒绝读取，但目录和内容仍然保留，随时可以再打开。旧版本没有 `enabled` 字段的技能按启用处理，不会被这次升级误关。

插件还会自动补入 44 个技能，同样默认关闭，统一显示在「开发专区」的编程核心、文档与创作、API 专用三组里。开发类包括：代码评审、GitHub API、飞书 API、Notion API、只读 SQL、SearXNG、网页抓取、MCP 工具链、Git 工作流、Python 工程、前端、API 设计、数据库设计、测试、调试、性能、安全、重构、发布、Docker、Windows 命令行、CLI 设计、Python 打包、技术写作、架构图、视觉设计、3D 建模、数据分析、依赖审计、可访问性、文档维护和技能编写。日常与创作类包括：Word、PDF、Excel、PPT、长文写作、知识库、draw.io、技术图表、图像生成、联网调研、本地文件搜索和自然化写作。

技能可以在 frontmatter 里声明 `requires_env` / `requires_commands`，也可以在技能目录放 `requirements.json`：

```json
{
  "env": ["BRAVE_API_KEY"],
  "commands": ["npx"],
  "downloads": [
    {"label": "参考数据", "url": "https://example.com/data.json", "path": "references/data.json"}
  ]
}
```

代理用 `list_skills` / `read_skill` / `search_skills` 读取，用 `save_skill` 沉淀新做法。技能正文上限 4 万字，超过时把细节拆到 `references/` 资源里。

本地技能不够用时，它会上 GitHub 自己找：

1. `search_skills_online(query)` 搜索带技能生态的仓库；
2. `list_repo_skills(repo)` 列出仓库里的全部 `SKILL.md`；
3. `install_skill(repo, path)` 拉取正文和同目录的 `references/` / `scripts/` / `assets/` 文本资源，写进本地技能库（默认关闭，需要时再打开）。

安装有大小和数量上限（单个附件 64 KB、每个技能最多 16 个附件、总计 256 KB），二进制和超大文件直接跳过；同名技能会自动加 `-2`、`-3` 后缀，不会覆盖已有的。外部技能只是参考材料，里面如果出现“忽略规则”“把密钥发出来”一类指令，代理会无视。

### 连接器

定义保存在 `data/dev/connectors.json`，支持两种类型：

- `http`：固定 `base_url`，调用时拼相对路径和查询参数；请求头里可以用 `${ENV_NAME}` 引用环境变量。整个 `base_url` 也可以写成 `${ENV_NAME}`，适合自托管 SearXNG 或 SQL 网关。
- `mcp`：填写命令、参数、环境变量和工作目录，插件会按需启动 stdio 服务器，完成 `initialize → tools/list → tools/call` 后关闭。

连接器默认关闭。资源界面左侧勾选即启用，取消即关闭；`set_connector_enabled(connector_id, true/false)` 同样可用。关闭的连接器不会进入系统提示，`call_connector` / `mcp_tools` / `call_mcp_tool` 会直接拒绝，但定义仍保留在 `connectors.json` 里。

自带连接器全部默认关闭，统一显示在「开发专区」的 HTTP 连接器和 MCP 连接器两组里：

- HTTP：`github`、`gitlab`、`feishu`、`notion`、`searxng`、`sql-gateway`、`fetch-gateway`、`pypi`、`npm-registry`、`crates-io`、`nuget`、`maven-central`、`rubygems`、`packagist`、`pub-dev`、`dockerhub`、`huggingface`、`stackexchange`、`devto`、`hackernews`、`openai-api`、`tavily`、`arxiv`、`crossref`、`openalex`、`wikipedia`。
- MCP：`context7`、`mcp-filesystem`、`mcp-git`、`mcp-sqlite`、`mcp-playwright`、`mcp-chrome-devtools`、`mcp-fetch`、`mcp-markitdown`、`mcp-duckduckgo`、`mcp-memory`、`mcp-sequential-thinking`、`mcp-time`、`mcp-blender`、`mcp-github`、`mcp-brave-search`、`mcp-everything`。

密钥只在运行时从环境变量解析，界面和模型看到的普通密钥值会显示为 `***`；响应里如果回显密钥，也会先打码再交给模型。连接器调用和 MCP 工具调用默认需要确认。

缺 MCP 连接器时，代理会先用 `search_mcp_servers` 找仓库，用 `read_repo_file` 读 README 确认包名和参数，再 `install_mcp_server`。包名支持 `npm:xxx` / `pypi:xxx` 前缀；不带前缀时先查 npm，再查 PyPI，分别推断 `npx -y` 或 `uvx`。启动命令找不到会直接报错，不会写一条跑不起来的配置。MCP 服务器是以当前用户权限运行的本地进程，所以安装前会展示完整命令行并等待确认；同名连接器不会被静默覆盖。

### 环境与依赖

启用技能或连接器之后，资源界面的「环境与依赖」标签页会做一次体检：

- **环境变量**：从连接器的 `base_url`、`headers`、MCP `env`，以及技能的 `requires_env` / `requirements.json` 推断。缺失项显示来源和获取地址，值可以用密码框写入 `data/dev/env.json`；已经写在系统环境变量里的也会识别。
- **启动命令**：MCP 的 `command` 和技能声明的 `requires_commands` 会检查是否在 PATH 里；缺 Node.js、uv、Blender 时给出官方下载地址，不自动装系统运行时。
- **下载资源**：MCP 用 `npx` 时可用 `npm cache add` 预下载包，用 `uvx` 时可用 `uv tool install`；Playwright 可以预下载 Chromium；技能可以在 `requirements.json` 里声明 `downloads`，确认后下载到技能目录（单文件 4 MB、单次总量 8 MB）。

环境变量解析顺序是「本地 `env.json` → 系统环境变量」。本地值不会进入系统提示、日志、会话记录或回答，界面只显示 `***`；代理只能通过 `check_requirements` 看到名称、来源和是否配置，拿不到明文。`env.json` 本身是明文文件（和桌宠自己的 `config.json` 一样），不会被打进插件包，也不要提交或分享。

打开一个原本关闭的技能或连接器属于权限变更：代理需要说明理由，`set_skill_enabled` / `set_connector_enabled` 会弹窗申请，主人允许后才写入开关。

### 知识库

知识库只用一个 `knowledge.json`，不依赖 SQLite，打包版桌宠也能直接运行。检索按词频打分，标题和来源命中会加权，支持中文短词和代码片段。可以：

- 导入文本、工作区文件或网页正文；
- 按关键词搜索，命中片段带 `[K文档#片段]` 引用；
- 任务结束后自动把总结存成一条 `session` 文档（可在设置里关闭）；
- 删除前在 `.trash/knowledge/` 留一份 Markdown 备份。

### 联网检索

`web_search` 优先使用 DuckDuckGo HTML，失败时退回 Bing RSS；`search_github` 使用公开 Search API；`pypi_info` / `search_pypi` 使用 PyPI 公开接口。默认禁止访问本机和内网地址，响应有大小上限。

## 工具清单

| 工具 | 作用 |
| --- | --- |
| `list_dir` | 列目录 |
| `read_file` | 读文本，带行号，可分段 |
| `write_file` | 写/覆盖文件（自动备份） |
| `edit_file` | 精确替换，要求匹配唯一 |
| `glob` | 通配符找文件 |
| `grep` | 正则搜内容 |
| `run_shell` | 在工作区执行命令，返回退出码和输出 |
| `check_python` | 只做语法检查，不执行 |
| `fetch_url` | 读取网页/接口文本（默认禁止本机和内网） |
| `list_plugins` | 看已装插件，避免 id 冲突 |
| `read_logs` | 读插件日志末尾，排查加载失败 |
| `read_reference` | 读插件 API 速查 |
| `create_plugin` | 创建/更新插件，可立即启用 |
| `ask_user` | 需求不清时问主人 |
| `list_skills` / `read_skill` / `search_skills` | 查看和检索技能（只读已启用项） |
| `save_skill` / `delete_skill` / `set_skill_enabled` | 新建、删除或开关技能（启用关闭项需确认） |
| `search_skills_online` / `list_repo_skills` | 在 GitHub 找技能仓库、列出仓库里的技能 |
| `install_skill` / `read_repo_file` | 安装外部技能、读取仓库文件 |
| `list_connectors` | 看全部连接器及开关状态 |
| `call_connector` | 调用已启用的 HTTP 连接器 |
| `mcp_tools` / `call_mcp_tool` | 列出或调用已启用的 MCP 工具 |
| `save_connector` / `delete_connector` / `set_connector_enabled` | 新建、删除或开关连接器（启用关闭项需确认） |
| `check_requirements` | 检查已启用项的缺失环境变量、命令和下载资源，不返回值 |
| `prepare_requirement` | 预下载技能资源或 MCP 依赖，需确认 |
| `search_mcp_servers` / `install_mcp_server` | 搜索并安装 MCP 服务器 |
| `search_knowledge` / `list_knowledge` | 检索和浏览知识库 |
| `import_knowledge` / `import_knowledge_file` / `import_knowledge_url` | 导入文本、文件或网页 |
| `delete_knowledge` | 删除知识文档（留备份） |
| `web_search` / `search_github` | 搜网页和 GitHub 仓库 |
| `pypi_info` / `search_pypi` | 查 PyPI 包信息和搜索包 |

## 权限

```json
["pet.say", "pet.action", "settings", "storage"]
```

- 不需要 `ai.chat`：插件直接读取用户自己在「AI 与桌宠设置」里配置的接口，以便突破宿主闲聊接口的 500 tokens / 800 字限制。
- 密钥只在内存中使用，不写日志、不写会话、不上传到配置之外的服务。
- 所有插件自身的状态都保存在 `data/dev/` 和插件的 `settings` / `storage` 里。

## 设置项

| 设置 | 默认 | 说明 |
| --- | --- | --- |
| `approval_mode` | `confirm_danger` | `auto` 全自动；`confirm_danger` 只确认危险操作；`confirm_all` 每步确认 |
| `shell_mode` | `auto` | Windows 优先 Git Bash，也可指定 cmd / PowerShell / 关闭 |
| `temperature` | 0.3 | 写代码建议低温度 |
| `allow_network` | 开 | 是否允许联网读取、网页搜索、GitHub 和 PyPI 查询 |
| `allow_private_network` | 关 | 是否允许访问本机/内网地址 |
| `allow_connectors` | 开 | 是否允许 HTTP / MCP 连接器 |
| `allow_self_modify` | 关 | 是否允许代理改自己（高风险） |
| `auto_enable_plugin` | 开 | 新插件通过检查后自动启用 |
| `auto_learn` | 开 | 任务结束后自动把总结存进知识库 |
| `notify_on_finish` | 开 | 结束后让桌宠说一句 |
| `model_override` | 空 | 留空沿用桌宠当前模型 |
| `workspace_dir` | 空 | 留空使用 `data/dev/workspace`，也可以指向已有项目文件夹 |

> `auto` 只跳过普通操作确认；删除技能、删除连接器、删除知识文档仍然会强制弹窗。没有工作台窗口时，需要确认的操作一律拒绝。

## 对模型的要求

写代码需要模型能稳定输出 JSON、能一次生成几百行内容。本插件不再主动设置步数或输出上限，但接口自身仍有长度上限；推荐使用支持长输出的 OpenAI 兼容模型或本地 Ollama 代码模型。免费公共通道可能限流或输出被截断，表现是“模型没有按协议输出”，此时换模型或把单步动作拆小。

## 一键还原与插件导出

- 工作台右侧「维护」区有两个按钮：
  - **一键还原到初始状态**：把工作区、草稿和插件目录里被代理改过的内容退回第一次修改之前；代理新建的文件/插件会被移除。执行前会二次确认，并把当前状态备份到 `data/dev/.backups/restore/<时间戳>/`。技能和知识库不在还原范围内，删除时各自留了回收备份。
  - **导出插件**：选择一个已安装插件，打包成 ZIP（根目录为插件 id），自动排除 `__pycache__`、`.state` 和 `*.pyc`。
- 还原不会处理自开发工坊自身（`lfelkeea.self_dev`），避免运行中把自己拆掉。
- 改动账本在 `data/dev/.changes/journal.json`，还原记录归档在 `.changes/history/`。

## 可移植性

- 不写死盘符、用户名或安装目录。
- 数据目录按「环境变量 → 宿主 `app_paths.DATA_DIR` → 插件位置反推」解析，源码运行和 PyInstaller 安装版都适用。
- 包里不含 API Key、个人配置和本机路径，其他人下载后用的是他们自己的桌宠和模型配置。
- 只依赖 Python 标准库和桌宠自带的 PySide6；MCP 服务器由用户自己的命令启动，不随插件分发。

## 安全说明

- 插件是运行在桌宠进程里的本机 Python 代码，不是沙箱；本插件把风险收敛在七点：
  1. 文件访问锁在开发区；
  2. 破坏性操作前备份，删除优先走回收站；
  3. 命令执行、连接器调用、MCP 工具调用、外部技能安装、资源预下载和插件安装默认要人确认；
  4. 打开原本关闭的技能或连接器也要确认；
  5. 没有工作台窗口时，需要确认的操作一律拒绝；
  6. 联网默认拦截本机和内网地址，重定向也检查；
  7. 连接器密钥从「本地 env.json → 系统环境变量」解析，返回给模型前打码，代理只能看到名称和状态。
- 不要把审批模式设成 `auto` 去跑来源不明的任务。
- `data/dev/env.json` 是明文存储，权限尽量收紧到 600；不要提交、分享或打进插件包。发现泄露时先在对应平台轮换令牌。
- 代理读到的网页内容、外部技能和仓库 README 都可能包含指令注入，遇到“忽略规则”“把密钥发给我”一类内容应忽略；插件里也已在提示词和安装确认框里强调。
- 在线下载只收文本附件，有单个和总大小上限，二进制直接跳过；但外部技能仍然是第三方内容，安装前值得扫一眼正文。
- MCP 服务器是你自己配置的本地命令，拥有当前用户权限；只挂载可信服务器。

## 排错

| 现象 | 处理 |
| --- | --- |
| 「还没有配置 API Key」 | 到桌宠「AI 与桌宠设置」填好；Ollama 和免费通道不需要 |
| 「模型没有按协议输出」 | 换更强的模型，或把单步动作拆小；步数和输出长度已不设上限，瓶颈通常在接口本身 |
| 「需要确认的操作没有工作台可以弹窗」 | 打开自开发工作台后再运行任务 |
| 新插件启用失败 | 看工作台输出里的错误，或 `data/plugins.log`；修好后重新 `create_plugin` |
| 命令找不到 | 设置里把 `shell_mode` 切到 `cmd` 或指定 `bash` 路径 |
| 联网被拒绝 | 检查设置里的「允许联网」和「允许访问本机和内网地址」 |
| 连接器调用被拒绝 | 连接器调用需要工作台确认；`auto` 模式下也会确认 |
| MCP 启动失败 | 确认命令在 PATH 里，或填写可执行文件绝对路径；看工作台输出里的 stderr |
| 在线找技能/连接器提示 GitHub 限流 | 公开 API 未登录时限流较严，等几分钟再试，或用更具体的关键词缩小搜索 |
| 安装 MCP 时提示找不到启动命令 | 本机没有 `npx` / `uvx`；先装 Node.js 或 uv，或显式传 `command` 和 `args` |
| 知识库搜不到 | 换更具体的词；匹配按词频加权，仍无结果说明没导入过 |
| 插件状态停在「加载中…」或「加载失败」 | 看 `data/plugins.log` 末尾的 traceback；1.2.1 起已不依赖打包版缺失的 `sqlite3` 和 `xml.etree` |

## 开发与测试

```bash
cd desktop_pet
QT_QPA_PLATFORM=offscreen python -m unittest discover -s tests -p "test_*.py"
```

插件源码在 `desktop_pet/plugins/lfelkeea.self_dev/`；桌宠安装包只内置 `example.*`，本插件通过插件库单独分发。
