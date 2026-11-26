# notebooklm-t3

[English](./README.md)

面向 T3 Code 全平台（**Antigravity、Claude Code、Codex、Grok、Cursor、Pi**）的 Google NotebookLM 自主决策技能套件，包含非交互防呆 CLI 包装器、WAL 指纹安装卸载器、20 分钟后台保活与 3 套确定性 T3 交互可视化渲染器。

使用本套件不绑定 Gemini 模型。NotebookLM 的音频、PPT、信息图、测验与调研全部运行在 Google 服务端，你在 T3 Code 里使用任意模型都能独立编排完整流程，并直接在对话中翻页看 PPT、听双人播客、点选 4 题型测验、翻看闪卡，以及在横向交互思维导图画布上平滑展开分支、路径聚光高亮、关键词检索与导出 2x PNG。

## 实机宣传视频

https://github.com/user-attachments/assets/b07ea83e-6c5a-4950-9957-4bfc590cd18c

- [下载中文版宣传视频（1080p MP4）](https://github.com/aa2246740/notebooklm-t3/releases/download/v0.1.1/notebooklm-t3-launch-zh.mp4)
- [下载英文版宣传视频（1080p MP4）](https://github.com/aa2246740/notebooklm-t3/releases/download/v0.1.1/notebooklm-t3-launch-en.mp4)

## 快速上手（Agent-First，普通用户推荐）

大多数 T3 Code 用户不需要自己敲终端命令。在 T3 Code 中打开任意 Agent（Antigravity、Claude、Codex、Grok、Cursor 或 Pi），直接粘贴下面这一句话（绑定固定发布 tag，绝不用 `curl | sh`）：

```text
请帮我按 https://github.com/aa2246740/notebooklm-t3/blob/v0.1.2/docs/INSTALL_FOR_AGENTS.md 的步骤，下载校验 v0.1.2 发布包并安装配置 notebooklm-t3。
```

Agent 会自动完成：在 `$TMPDIR` 下载并执行 SHA-256 校验 -> 只读探测环境（邮箱脱敏）-> 输出完整 `--plan` 变更清单并在写入 `$HOME` 前预检沙盒权限 -> 一次性请你确认工作区目录、浏览器 Profile 和保活选项 -> 执行落盘、前台绑定账号和 `--doctor` 体检 -> 提醒你新开一条线程。

新开线程后，你只需直接提目标，不用关心 MCP、CLI 参数或渲染脚本：

- “用 NotebookLM 把这个 YouTube 视频提炼成中文思维导图和复习测验。”
- “搜集今天 AI 圈最热的推特动态，用 NotebookLM 做成可翻页预览的中文 PPT 和一期双人深度播客。”
- “用 NotebookLM 联网调研 MCP 和 A2A 的架构边界，整理带引用的选型简报。”

## 终端手动安装与卸载

唯一前置依赖：macOS 或 Linux 上的 [`uv`](https://docs.astral.sh/uv/getting-started/installation/)。

```sh
# 终端交互式向导（非 TTY 环境需显式传 --probe / --plan / --apply --non-interactive）
./install.sh

# 全项健康体检（引擎版本、socksio、代理/PAC、accounts.google.com 3 秒连通性、认证、软链与 MCP）
./install.sh --doctor --json

# 从校验后的指定 Release Tag 升级
./install.sh --update --tag v0.1.2

# 基于 WAL Manifest 指纹校验的无损卸载
./uninstall.sh
```

## 安装清单与卸载清单

安装器在执行每一步前都会把目标真实路径、改动前快照和写入指纹写入 `~/.config/notebooklm-t3/manifest.json`，支持崩溃回滚与重复运行幂等。

| 组件 | `install.sh` 安装或修改的内容 | `uninstall.sh` 的行为 |
|---|---|---|
| 底层引擎（`notebooklm-py`） | 若未安装兼容版本，执行 `uv tool install --with socksio "notebooklm-py[browser,mcp,markdown,impersonate,cookies,headless]>=0.8.6,<0.9"` | 默认保留；仅在由本工具安装且显式传入 `--uninstall-engine` 时卸载。永远不卸载 `uv`。 |
| 技能真源目录 | 复制 `skill/notebooklm-t3/` 到 `~/.agents/skills/notebooklm-t3` 并写入 `.notebooklm-t3-managed.json` 标记 | 仅在目录包含受管标记文件时删除。 |
| 源码更新缓存 | 复制到 `~/.local/share/notebooklm-t3/src`（供 `--update` 使用，误删不影响技能运行） | 仅在包含受管标记文件时删除。 |
| 跨 Provider 软链 | 自动探测本机已启用的 T3 Antigravity 实例（`providers/antigravity/*/config/skills`）、已启用的 `claudeAgent` 实例（含自定义 `homePath`）与已存在的 `~/.codex/skills`、`~/.grok/skills`、`~/.pi/agent/skills`、可选 `~/.gemini/config/skills`，按 `realpath` 去重挂链 | 仅当当前 `readlink()` 与记录完全一致时删除；被用户改过的软链保留并在报告里明示。 |
| 配置与状态 | `~/.config/notebooklm-t3/{config.json,manifest.json}`（`0600` 权限）与 `~/.local/state/notebooklm-t3/` | 恢复原配置或删除。 |
| Antigravity MCP（可选） | 原子合并 `~/.t3/userdata/providers/antigravity/*/config/mcp_config.json` 中的 `mcpServers.notebooklm`（保软链、保权限位、保留其他所有键） | 指纹一致时恢复原值或删键；若之后被用户手工修改则原样保留并报告。 |
| 后台 20 分钟保活（可选） | macOS 写 `~/Library/LaunchAgents/io.github.aa2246740.notebooklm-t3.keepalive.plist`；Linux 写 `systemd --user` 定时器；`browser_fallback` 默认关闭 | 停用并按指纹删除。 |
| 登录态与用户产物 | 卸载时默认绝不碰触 | 默认保留 `~/.notebooklm/` 与所有 `notebooklm-outputs/`（仅显式传 `--purge-auth` 才删 `~/.notebooklm/`）。 |

## 冷启动与平台支持矩阵

| 用户环境 | 推荐路径 |
|---|---|
| 过夜休眠不掉登录态（推荐） | 终端前台执行一次 `nlm setup-auth master-token`（自动复用系统 Google Chrome 完成一次登录并写入 `0600` `master_token.json`；此后电脑休眠醒来由 `keepalive` 与 `nlm recover` 自动无浏览器重铸 Cookie，不碰 macOS Keychain）。 |
| Chrome / Brave / Edge 已登录 NotebookLM | 前台运行 `nlm setup-auth inspect --browser "chrome::<dir>" --json`（输出脱敏邮箱序号）-> `nlm setup-auth apply --index <k>` 绑定真实邮箱（macOS 首次读钥匙串会弹 `Chrome Safe Storage`，点“始终允许”）。 |
| 只用 Safari / 直接浏览器登录 | 读取 Safari Cookie 需给 T3 授予完全磁盘访问权限，推荐跑 `nlm setup-auth master-token` 或前台 `nlm login --browser chrome`（`nlm login` 会自动探测并复用本机 Chrome/Edge，避免额外下载 ~600MB Playwright 内置 Chromium）。 |
| 无图形浏览器 / Linux 服务器 / WSL | 在另一台已登录机器导出 Cookie 后用 `notebooklm auth import-cookies` 导入。 |
| Linux `systemd --user` 保活 | 若希望登出后继续保活，请运行 `loginctl enable-linger "$USER"`（WSL 默认未启用 systemd）。 |
| Windows 原生 | v1 明确不支持（受 Chrome App-Bound Encryption 限制，本地 Cookie 解密会失败）。 |

## 安全边界与已知约束

- **CLI 硬护栏与 MCP 边界**：`nlm` 在代码层硬拦截裸 `--browser-cookies`（防串号覆盖）、非白名单 `--account`（防 `click.confirm` 阻塞卡死）、`--allow-headless`、`language set` 与 `skill install`。Antigravity 原生 MCP 调用绕过 CLI 包装层，因此删除笔记本等高危操作的确认依赖 `SKILL.md` 提示词约束。
- **后台保活防弹窗**：20 分钟定时保活严格 120 秒子进程超时，严禁传 `--allow-headless` 或调 `login`，且默认关闭 `browser_fallback`，避免 macOS 后台弹出 Keychain 授权框无限挂起；状态写入 `~/.local/state/notebooklm-t3/auth_status.json`（超过 45 分钟按 `unknown` 处理）。
- **邮箱隐私保护**：`--probe --json`、`nlm status` 与 `nlm setup-auth inspect` 对邮箱一律脱敏为 `x***@domain` 再交回模型上下文，明文仅保存在 `0600` 权限的本地文件中。
- **`notebooklm-py 0.8.6` 已知边界**：`quiz`/`flashcards` 不支持 `--language`（`nlm` 按 `--help` 实时能力探测自动改写为提示词约束）；`generate data-table` 当前在上游 `R7cb6c` 报 `rpc_code=3`，`SKILL.md` 已默认自动改走 `chat_ask` 结构化出表。

## 旧版无损迁移

如果你的机器上已经手工安装过未带受管标记的 `~/.agents/skills/notebooklm-t3` 或旧版 macOS LaunchAgent，执行以下命令即可先备份接管、停用旧定时任务再加载新版：

```sh
./install.sh --apply --non-interactive --adopt-legacy \
  --bootout-legacy-label <旧的.launchd.label> \
  --allowed-roots "<你的工作区目录>" \
  --keepalive
```

## 致谢

- 底层 RPC、CLI 与 MCP 传输引擎依赖 [`teng-lin/notebooklm-py`](https://github.com/teng-lin/notebooklm-py)（MIT 协议）。
- 场景思路受 [`WeZZard/antigravity-notebooklm`](https://github.com/WeZZard/antigravity-notebooklm) 启发。本仓库全部代码、渲染器、安装器与文档均为独立原创实现，采用 MIT 协议开源。
