# notebooklm-t3

[简体中文](./README.zh-CN.md)

Autonomous Google NotebookLM skill, non-interactive safety CLI wrapper, WAL-manifest installer, and interactive T3 Code visualizers across **Antigravity, Claude Code, Codex, Grok, Cursor, and Pi**.

You do not need Gemini to use this kit. NotebookLM synthesis runs on Google's servers; any T3 Code provider can orchestrate YouTube transcripts, multi-source cited Q&A, Web/Deep Research, Audio Overviews, Slide Decks (PDF + editable PPTX + inline slide viewer), Infographics, interactive horizontal Mind Maps (with smooth branch expansion, path spotlight, keyword search, and 2x PNG export), Quizzes, and Flashcards automatically.

## Launch Video

https://github.com/user-attachments/assets/e133727b-63fa-4464-a239-00203e157275

- [Download English Launch Video (1080p MP4)](https://github.com/aa2246740/notebooklm-t3/releases/download/v0.1.1/notebooklm-t3-launch-en.mp4)
- [Download Chinese Launch Video (1080p MP4)](https://github.com/aa2246740/notebooklm-t3/releases/download/v0.1.1/notebooklm-t3-launch-zh.mp4)

## Quick Start (Agent-First, Recommended)

Most T3 Code users never need to run terminal setup commands manually. Open any agent in T3 Code (Antigravity, Claude, Codex, Grok, Cursor, or Pi) and paste this single instruction (pinned to a release tag — never `curl | sh`):

```text
Please install and configure notebooklm-t3 by following https://github.com/aa2246740/notebooklm-t3/blob/v0.1.1/docs/INSTALL_FOR_AGENTS.md using release tag v0.1.1.
```

Your agent will download and SHA-256 verify the release tarball in `$TMPDIR`, probe your environment read-only, show you the exact `--plan` before touching `$HOME`, ask once for confirmation (workspace root, browser profile, keepalive), bind your NotebookLM account safely, run `--doctor`, and remind you to open a new thread.

After opening a new thread, just state your goal — no need to mention MCP, CLI flags, or rendering scripts:

- "Use NotebookLM to turn this YouTube lecture into a Chinese mind map and study quiz."
- "Gather today's top AI news and use NotebookLM to build a slide deck with inline preview plus a deep-dive podcast."
- "Run NotebookLM Fast Research on MCP vs A2A and deliver a cited briefing."

## Manual Terminal Install / Uninstall

Prerequisite: [`uv`](https://docs.astral.sh/uv/getting-started/installation/) on macOS or Linux.

```sh
# Interactive TTY wizard (or pass --probe / --plan / --apply --non-interactive)
./install.sh

# Health check (version, socksio, proxy/PAC, 3s Google reachability, auth, symlinks, MCP)
./install.sh --doctor --json

# Update from a verified release tag
./install.sh --update --tag v0.1.1

# Clean fingerprint-verified uninstall
./uninstall.sh
```

## Exact Install & Uninstall Inventory

Every mutation is recorded first in a write-ahead manifest (`~/.config/notebooklm-t3/manifest.json`) with resolved real paths, pre-existing values, and SHA-256 fingerprints.

| Component | Installed / Modified by `install.sh` | Behavior on `uninstall.sh` |
|---|---|---|
| Engine (`notebooklm-py`) | `uv tool install --with socksio "notebooklm-py[browser,mcp,markdown,impersonate,cookies]>=0.8.6,<0.9"` (if absent or `--force-engine`) | Preserved by default; uninstalled only if installed by this tool AND `--uninstall-engine` is passed. `uv` is never uninstalled. |
| Canonical Skill | Copied to `~/.agents/skills/notebooklm-t3` with `.notebooklm-t3-managed.json` marker | Removed only if managed marker is present. |
| Source Cache | Copied to `~/.local/share/notebooklm-t3/src` (used for `--update`) | Removed only if managed marker is present. |
| Provider Symlinks | Linked into detected existing providers (`~/.t3/userdata/providers/antigravity/*/config/skills`, enabled `claudeAgent` `homePath/skills`, `~/.codex/skills`, `~/.grok/skills`, `~/.pi/agent/skills`, optional `~/.gemini/config/skills`), deduped by `realpath` | Removed only if current `readlink()` matches exact recorded target; user-modified symlinks are preserved and reported. |
| Config & State | `~/.config/notebooklm-t3/{config.json,manifest.json}` (`0600`), `~/.local/state/notebooklm-t3/` | Restored or removed cleanly. |
| Antigravity MCP (optional) | Atomically merges `mcpServers.notebooklm` in `~/.t3/userdata/providers/antigravity/*/config/mcp_config.json` (preserves symlinks, permissions, and all other keys) | Fingerprint-checked: restores prior value (or removes key) if unchanged; preserves untouched if user customized it later. |
| Keepalive (optional) | 20-minute interval (`launchd` `io.github.aa2246740.notebooklm-t3.keepalive` on macOS; `systemd --user` timer on Linux), `browser_fallback: false` by default | Unloaded and removed if fingerprint matches. |
| Auth & Outputs | Never touched during uninstall by default | `~/.notebooklm/` and user `notebooklm-outputs/` are **preserved** (pass `--purge-auth` only if you want to wipe `~/.notebooklm/`). |

## Authentication & Cold-Start Matrix

| Environment | Recommended Onboarding Path |
|---|---|
| Chrome / Brave / Edge already signed in | Foreground `nlm setup-auth inspect --browser "chrome::<dir>" --json` (masked emails) -> `nlm setup-auth apply --index <k>` (binds real `account_email`; warns about macOS Keychain "Always Allow"). |
| Safari only | Reading Safari cookies requires Full Disk Access. Sign in via Chrome or run foreground Playwright login (`notebooklm login --browser chrome`, 300s timeout; verify Google connectivity before downloading ~150MB Chromium). |
| Headless Linux / WSL (no browser) | Export cookies from a signed-in machine and import via `notebooklm auth import-cookies`. |
| Linux `systemd --user` keepalive | Run `loginctl enable-linger "$USER"` if you want the 20-minute timer to run while logged out. (WSL typically lacks `systemd` by default.) |
| Windows native | Explicitly unsupported in v1 due to Chrome App-Bound Encryption blocking local cookie decryption. |

## Security & Architecture Boundaries

- **CLI guardrails vs. MCP boundary**: The `nlm` wrapper enforces hard code-level blocks on bare `--browser-cookies` (prevents wrong-account overwrite), non-interactive `--account` (prevents `click.confirm` stdin hangs), `--allow-headless`, `language set`, and `skill install`. Note that Antigravity's native MCP path bypasses `nlm`, so destructive MCP confirmations (such as `notebook_delete confirm=true`) rely on `SKILL.md` prompt discipline.
- **Unattended keepalive safety**: Background keepalive runs every 20 minutes (matching `notebooklm-py`'s 15-20 minute SIDTS guidance), enforces a 120-second subprocess timeout, never invokes `login` or `--allow-headless`, and keeps `browser_fallback` off by default so background `launchd` jobs never stall on a macOS Keychain modal.
- **Email privacy**: `--probe --json`, `nlm status`, and `nlm setup-auth inspect` mask account emails (`x***@gmail.com`) before output enters LLM context. Plaintext account mapping stays in `0600` local files.
- **Known `notebooklm-py 0.8.6` boundaries**: `generate quiz` and `generate flashcards` omit `--language` (handled automatically by `nlm` via live `--help` capability probing); `generate data-table` currently returns upstream `R7cb6c` `rpc_code=3`, which `SKILL.md` routes cleanly to `chat_ask` structured output.

## Legacy Local Migration

If you previously installed an unmanaged `~/.agents/skills/notebooklm-t3` directory or a custom macOS LaunchAgent, run:

```sh
./install.sh --apply --non-interactive --adopt-legacy \
  --bootout-legacy-label <old.launchd.label> \
  --allowed-roots "<workspace-root>" \
  --keepalive
```

## Credits

- Built on top of [`teng-lin/notebooklm-py`](https://github.com/teng-lin/notebooklm-py) (MIT License) for NotebookLM RPC, CLI, and MCP transport.
- Inspired in workflow concepts by [`WeZZard/antigravity-notebooklm`](https://github.com/WeZZard/antigravity-notebooklm). All code, renderers, installer, and documentation in this repository are written from scratch under the MIT License.
