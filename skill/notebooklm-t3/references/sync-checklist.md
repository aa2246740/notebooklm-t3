# NotebookLM-T3 漂移同步与维护巡检清单

仅在以下情况手动执行本清单，普通对话任务不得自动触发：

- 用户明确要求巡检或升级 `notebooklm-py` 版本。
- 遇到 RPC 异常（如 `rpc_code=3` 参数漂移）、下载 schema 报错或 Google NotebookLM 前端大改版。

巡检全程保持严格只读：不得创建、修改或删除用户现有笔记本、来源、笔记、产物或分享权限。

## 1. 本地引擎、代理与健康体检

```bash
~/.local/share/notebooklm-t3/src/install.sh --doctor --json
~/.agents/skills/notebooklm-t3/scripts/nlm status --json
~/.agents/skills/notebooklm-t3/scripts/nlm check
```

- 确认 `notebooklm-py` 版本位于受支持范围（`>=0.8.6,<0.9`），且引擎专属 `uv tool` 环境中已包含 `socksio`（若使用 SOCKS 代理）。
- 确认 `accounts.google.com` 3 秒连通性通过，`auth_status.json` 未超过 45 分钟时效窗口。
- 如需升级 `notebooklm-py` 到新的受支持小版本，先对照上游 release notes 核查 `notebooklm/mcp/tools/`、`generate <type> --help` 的 `--language` 能力变化及 `CREATE_ARTIFACT` RPC 结构，再执行：

```bash
uv tool install --force --with socksio "notebooklm-py[browser,mcp,markdown,impersonate,cookies]>=0.8.6,<0.9"
```

## 2. 线上网页能力对照（只读）

- 使用共享浏览器或只读方式访问 `https://notebook.google.com/`，核查六个界面面是否增删功能或更名：
  1. 入口域名与前端构建版本。
  2. Home 仪表盘标签与集合（`All`、`My notebooks`、`Discover`、`Collections`）。
  3. 笔记本顶栏菜单（`Share`、`Configure Chat`、`Customize notebook`、`Analytics`）。
  4. 左侧 `Sources`（支持来源类型、配额上限、Fast/Deep Research 模式）。
  5. 中间 `Chat`（内联引用格式、`Configure Chat` 选项）。
  6. 右侧 `Studio`（全部产物磁贴及对应 `studio_generate` / `studio_download` 支持情况，复测 `data-table` 的 `R7cb6c` 漂移是否已被上游修复）。

## 3. 可视化 Schema 与对抗回归检查

当 Google 更新 Quiz、Flashcards 或 Mind Map 的导出 JSON 结构时，同步调整 `scripts/render_study_card.py` 或 `scripts/render_mind_map.py`，并必须跑通以下回归：

- 使用真实导出的 `quiz.json`（覆盖单选/多选/填空/简答 4 种题型）、`flashcards.json`（覆盖 `flashcardContentBlock` 嵌套块）与 `mind-map.json` 正常编译，退出码为 0。
- 使用对抗样本（标题/正文包含 `<!--<script>`、`</script><img src=x onerror=alert(1)>`、`"`、`&`、中文及字符串 `"false"`）编译，确认 HTML 中提取出的脚本通过语法检查且 `html_preview` 返回 `contentHeight > 0`、`consoleMessages` 为空。
- 验证畸形/空 JSON 返回非零退出码，确保上游 Agent 自动退回 Markdown 交付。

## 4. 跨 Provider 挂载与保活核对

- 运行 `install.sh --doctor --json`，核查是否出现安装后新建的 T3 Antigravity 实例目录缺少 `skills/notebooklm-t3` 软链或 `mcp_config.json` 缺少 `notebooklm` 项。
- 核对 macOS `launchctl print gui/$(id -u)/io.github.aa2246740.notebooklm-t3.keepalive` 或 Linux `systemctl --user status notebooklm-t3-keepalive.timer` 处于启用状态，并查看 `~/.local/state/notebooklm-t3/keepalive.log` 最近记录。
