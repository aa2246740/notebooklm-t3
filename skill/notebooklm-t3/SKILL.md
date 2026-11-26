---
name: notebooklm-t3
description: Operate Google NotebookLM (notebook.google.com) in T3 Code across Antigravity, Codex, Claude, Grok, Cursor, and Pi via notebooklm-mcp, the nlm safety wrapper, preview_* browser, and interactive T3 html_render/inline media visualizers. Trigger whenever the user asks to use NotebookLM (用 NotebookLM) for YouTube transcripts, multi-source synthesis, Web/Deep Research (联网调研), Audio/Video Overviews (播客), Slide Decks (PPT), Infographics (信息图), Mind Maps (思维导图), Quiz (测验), Flashcards (闪卡), Reports (报告), or cited Q&A.
---

# NotebookLM for T3 Code

使用本机 `notebooklm-py`（`>=0.8.6,<0.9`）与 T3 Code 原生可视化、共享浏览器及多 Agent 编排自动完成 Google NotebookLM（`https://notebook.google.com/`）任务。

## 1. 全自动最优决策原则（用户只提目标，Agent 自主选择路径）

- 用户只需说明“用 NotebookLM 做什么”，绝不把 MCP/CLI 选型、账号 profile、`--language` 参数差异或上游 RPC 漂移抛给用户决定。
- 路由边界（Ground-Truth-First）：普通本地代码、Markdown、纯文本或单份小 PDF 的阅读与问答直接读本地文件，不绕道 NotebookLM；仅在 YouTube 视频文字稿、多源大规模语料综合、Google Drive / Play Books / Web Research、或生成 Studio 多模态产物时启用 NotebookLM。抽取来源时优先取回原始文字稿与引用原文。
- 自动引擎路由（零配置）：
  1. 当前会话已挂载 `# notebooklm` MCP（如 Antigravity）-> 直接通过内置 `call_mcp_tool(ServerName="notebooklm", ToolName=..., Arguments=...)` 执行全流程；严禁误用仅面向 `t3-code` 工具的 `acp-mcp-call`。单任务配合 `schedule` 一次性定时器轮询，不为单任务派生子代理。
  2. 当前会话未挂载 `# notebooklm` MCP（如 Codex / Claude / Grok / Cursor / Pi）-> 直接在当前线程通过 `~/.agents/skills/notebooklm-t3/scripts/nlm` 独立完成全部步骤，无需跨模型切换；仅当需要多链接批量扇出隔离上下文、长篇 Deep Research 消化、或当前环境无终端时，才自动 `delegate_task` 给具备稳定工具调用能力且已挂载 MCP 的快速模型子线程。
- 合理默认与少打扰：
  - 目标产物已明确时直接执行：输出语言默认跟随当前对话语言（中文传 `zh_Hans`；`quiz`/`flashcards` 由提示词指定简体中文，遇英文源偶发英文输出时自动核验并单独重试一次）；播客默认 `deep-dive` + `default` 时长；PPT 默认下齐 `pdf` + `pptx` 并渲染可翻页交互预览；Mind Map / Quiz / Flashcards 默认自动编译交互 HTML 并内联呈现；Infographic / Audio / Video 下载后直接用 Markdown 绝对路径嵌入播放或直显。
  - 仅在用户未说明产出形态（如“放进 NotebookLM 处理一下”）时，用结构化提问工具（无则纯文本）一次问清产物类型；来源 URL 或文件路径直接在正文收集，不做假选项。

## 2. 安全护栏、状态预检与认证无感自愈

- 所有 CLI 操作必须经 `~/.agents/skills/notebooklm-t3/scripts/nlm` 执行：脚本通过 `uv` 托管 Python 运行，自动注入 `NOTEBOOKLM_DISABLE_KEEPALIVE_POKE=1` 与代理透传（含 `NO_PROXY`），按实时 `--help` 能力探测自动处理缺少 `--language` 的子命令，并在代码层硬拦截裸 `--browser-cookies`、非白名单 `--account`、`--allow-headless`、`language set` 与 `skill install`。严禁直接调 `python3 -m notebooklm` 或绕过 `nlm` 跑危险登录命令。
- 后台定时保活每 20 分钟刷新一次会话并将状态写入 `~/.local/state/notebooklm-t3/auth_status.json`（超过 45 分钟视为 `unknown`）。开始任务前可先看 `~/.agents/skills/notebooklm-t3/scripts/nlm status --json`，若状态明确为 `expired` 或 `keychain_blocked` 则提前处理，不必等 RPC 报错。
- 认证失效无感自愈（对用户透明，仅在彻底失败时开口）：一旦遇到 `AUTH_EXPIRED` / `Authentication expired or invalid` / `Token fetch failed`：
  1. 静默执行 `~/.agents/skills/notebooklm-t3/scripts/nlm recover`（内部仅调用 `auth check` 与 `auth refresh`，若本机已生成 `master_token.json` 则自动无浏览器重铸新 Cookie，绝不调用 `login`，避免串号与弹窗挂死）；若长驻 `notebooklm-mcp` 此前已缓存失效/旧客户端，当次剩余步骤自动改走 `nlm` CLI 完成。
  2. 若 `nlm recover` 返回失败或 `keychain_blocked`，按以下优先级引导用户恢复，严禁给用户拼不带 `--browser chrome` 的裸 `notebooklm login`（会触发 ~600 MB 内置 Playwright Chromium 下载）：
     - 首选（防休眠掉线）：让用户在终端前台执行一次 `~/.agents/skills/notebooklm-t3/scripts/nlm setup-auth master-token`（自动复用系统 Google Chrome 完成一次登录，生成持久 `master_token.json`，此后电脑过夜休眠醒来也能由后台保活与 `nlm recover` 静默无浏览器重铸 Cookie，不碰 macOS Keychain）。
     - 备选 A（浏览器 Cookie 绑定）：让用户在已绑定的浏览器 Profile 打开 `https://notebook.google.com/` 登录，再走前台三步绑定：`nlm setup-auth inspect --browser "<browser::profile>" --json` -> 按脱敏序号确认 -> `nlm setup-auth apply --index <k>`。
     - 备选 B（单次前台浏览器登录）：让用户在终端运行 `~/.agents/skills/notebooklm-t3/scripts/nlm login --browser chrome`（或 `notebooklm --profile default login --browser chrome`），明确复用系统 Chrome，不要试图在 T3 共享浏览器里替 MCP 完成登录。

## 3. 笔记本生命周期、用完即问与定期清理

- 笔记本解析优先级：用户指定的名称或 `/notebook/<uuid>` -> 当前线程已绑定的 `[AGY-Session]` 完整 `notebook_id`（后续一律传完整 ID 避免前缀歧义） -> 新建 `[AGY-Session] <简短主题>`。
- 增量加源：调用 `source_add` 前先查 `source_list`，跳过已存在项，只补缺源。
- 非破坏性语言：始终按单次调用传参或提示词控制语言，绝不修改全局账号语言。
- 用完即问 + 定期清理（保持 NotebookLM 账号整洁）：
  - 每次新建 `[AGY-Session]` 临时笔记本并完成最终交付后，在回复末尾用一句话反问：“产物已保存到本地，这个临时笔记本要保留继续追问，还是现在顺手帮你清掉？”
  - 当列表查到账号下累计存在 5 个及以上临时笔记本（`[AGY-Session]`）或超过 7 天未更新的旧临时笔记本时，完成主任务后顺带列出清单，反问用户是否批量清理。
  - 删除笔记本、删除来源、公开或扩大分享权限前必须经过用户确认（MCP 传 `confirm=true`，CLI 执行 `nlm delete -n <id> -y`）；注意 MCP 路径绕过 CLI 护栏，此确认规则由 Agent 严格自律执行。

## 4. 产物落盘与可视化交付

所有下载与编译产物默认保存到当前工作区的 `./notebooklm-outputs/<short-topic>/`（若当前仓库存在全目录静态扫描如纯英文字符校验，自动改写到 `~/.local/share/notebooklm-t3/outputs/<short-topic>/`）：

- 长文字稿与报告：经 `source_read` 分页（每次上限 10,000 字符，配合 `offset`）拉全原文或下载报告为 `.md`，写入本地并给绝对路径链接。
- 音视频与信息图：保存本地（Audio Overview 实为 AAC/M4A 音频流，保存为 `.m4a`），Infographic PNG、Audio Overview 与 Video Overview 在 T3 中直接用 Markdown 绝对路径内嵌直显，并附可点击路径。
- Slide Deck（PPT）：同时下载 `pdf` 与 `pptx`；若本机已装 `pdftoppm`，执行 `pdftoppm -jpeg -r 144 <pdf> <dir>/slide` 后经 `~/.agents/skills/notebooklm-t3/scripts/nlm render-slide-deck <dir> <out.html> "<标题>"` 生成可翻页预览页；未装 `pdftoppm` 时直接交付 PDF/PPTX 并提示安装 `poppler`。
- Data Table：下载 CSV 后转 Markdown 表格或给 `.csv` 链接；若遇上游 `R7cb6c` 参数漂移（`rpc_code=3` / `UNCONFIRMED_WRITE`），立即静默改走 `chat_ask` / 本地整理输出结构化 Markdown 与 CSV，不反复重试。
- 带引用问答（`chat_ask`）：直接用原生 Markdown 输出；核验来源时先把 `references[].source_id` 映射为标题，拆分多编号引用，摘录 `cited_text` 附在下方并剔除装饰性 emoji。
- Quiz 与 Flashcards：下载 JSON 后调用 `~/.agents/skills/notebooklm-t3/scripts/nlm render-study-card <in.json> <out.html>`（兼容单选/多选/填空/简答 4 种题型与 `flashcardContentBlock` 嵌套闪卡）。
- Mind Map：保存 JSON 后调用 `~/.agents/skills/notebooklm-t3/scripts/nlm render-mind-map <in.json> <out.html>` 生成可搜索、可折叠交互树图。
- 内联渲染门禁与非 T3 降级：在支持 `html_preview` + `html_render` 的 T3 Code 环境中，上述 HTML 均须先经 `html_preview` 确认 `contentHeight > 0` 且 `consoleMessages` 为空，再调 `html_render`（传 `title`、`path`、`height`）呈现给用户；在无 `html_render` 的非 T3 客户端中，直接交付生成好的独立本地 HTML 路径（可直接用浏览器打开）及 Markdown 摘要；若渲染脚本因上游 schema 变动返回非零退出码，立即退回 Markdown 交付并简述原因。
- 共享浏览器 `preview_*` 仅用于三类场景：右栏直看原站笔记本（`https://notebook.google.com/notebook/<id>`）、Audio Overview Interactive mode 实时语音插话、以及 RPC 临时异常时的手工兜底。

## 5. 漂移同步维护

仅在用户明确要求巡检、升级 `notebooklm-py` 或遇到疑似上游字段/RPC 变更时，按 [references/sync-checklist.md](./references/sync-checklist.md) 执行只读审计与回归。
