# 自开发代理工作笔记

## 一次任务的推荐节奏

1. **看清现状**：`list_dir` → `read_file` / `grep`。不要在没读文件时凭空改。
2. **说清计划**：在 `thought` 里用一句话讲这一步做什么，不要长篇大论。
3. **小步修改**：一次改一个文件、一个关注点。`edit_file` 的 `old_string` 带上足够上下文。
4. **立刻验证**：Python 用 `check_python`；能跑就跑 `run_shell`；插件写完最好说明用户该点哪里验证。
5. **如实收尾**：`final` 里写改了什么、怎么验证、还差什么。

## Windows 环境注意事项

- 命令默认走 Git Bash（`bash -c`），也可以切 `cmd` 或 PowerShell。
- 文本编码统一按 UTF-8 处理；外部命令输出会依次尝试 UTF-8 / GB18030。
- 不要用 `taskkill /PID` 这种会被 Git Bash 当路径的写法；需要时用 `//PID` 或 PowerShell。
- 不要结束桌宠、天禧、Qoder、XLSmart 等进程。

## 安全边界

- 不要删除文件。需要“清空”时先备份，或问主人。
- 不要读取、打印、传输任何密钥；`data/config.json` 里的 `api_key` 是禁区。
- 不要访问工作区之外的路径；需要新位置时让主人改设置。
- 执行命令、安装/启用插件都要经过主人确认（默认行为）。
- 需求含糊时用 `ask_user`，不要自己脑补一个很大的功能。

## 做一个插件时的检查单

- [ ] `plugin.json` 是合法 JSON，`id` 与文件夹名一致，`api_version` 为 1。
- [ ] `permissions` 只声明真正用到的权限。
- [ ] `setup(api)` 不阻塞；定时器用 `api.every/after`。
- [ ] 所有界面操作在主线程；后台结果用信号回主线程。
- [ ] 没有读写 `data/config.json`，没有把密钥写进日志或存储。
- [ ] 入口文件通过 `check_python`。
- [ ] 说明清楚启用路径：插件管理 → 重新扫描 → 勾选启用。
