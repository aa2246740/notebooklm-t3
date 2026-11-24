# Agent-First Installation Guide (`notebooklm-t3`)

This runbook is written for AI coding agents (Antigravity, Claude Code, Codex, Grok, Cursor, Pi) inside T3 Code or standalone terminal sessions. Follow every step strictly; never execute `curl | sh` and never block on interactive `stdin`.

## Prerequisites

- Operating system: macOS or Linux. (Windows native is unsupported in v1 due to Chrome App-Bound Encryption.)
- Single required tool: `uv` (`https://docs.astral.sh/uv/getting-started/installation/`). If `uv` is missing, stop and ask the user before installing `uv`.
- Sandbox note: if you are running in a restricted sandbox (such as Codex default `workspace-write`), downloading the release tarball and applying changes to `$HOME` require network and `$HOME` write authorization. Never mutate `$HOME` before `--plan` succeeds.

## Step 1: Download and Verify Release Tarball in `$TMPDIR` (Zero `$HOME` Writes)

Use the exact pinned release tag requested by the user (for example `TAG=v0.1.0`):

```sh
TAG="v0.1.0"
WORK="${TMPDIR:-/tmp}/notebooklm-t3-${TAG}"
rm -rf "$WORK" && mkdir -p "$WORK"
cd "$WORK"

curl -fsSLO "https://github.com/aa2246740/notebooklm-t3/releases/download/${TAG}/notebooklm-t3-${TAG}.tar.gz"
curl -fsSLO "https://github.com/aa2246740/notebooklm-t3/releases/download/${TAG}/SHA256SUMS"

if command -v shasum >/dev/null 2>&1; then
  shasum -a 256 -c SHA256SUMS
else
  sha256sum -c SHA256SUMS
fi

tar -xzf "notebooklm-t3-${TAG}.tar.gz"
cd "notebooklm-t3-${TAG}"
```

If a local checkout already exists instead of a release tarball, run Steps 2-6 directly inside that checkout directory.

## Step 2: Read-Only Environment Probe

```sh
./install.sh --probe --json
```

Inspect the returned JSON:

- `uv.uv_bin`: confirm `uv` is present.
- `engine`: check if `notebooklm-py` is already installed, in `[0.8.6, 0.9)`, and whether `socksio` is present.
- `providers`: check detected T3 Antigravity instances, enabled Claude instances (including custom `homePath`), Codex, Grok, Pi, and optional `~/.gemini/config`.
- `browser_candidates`: list of Chromium browser profiles with **masked** sync emails (`x***@gmail.com`). These are candidates only; actual Google account cookies are verified in Step 5.
- If T3's `t3_project_list` tool is available in your session, call it once to collect workspace root candidates for `NOTEBOOKLM_MCP_ALLOWED_ROOTS` (never pass `$HOME` itself or `~/.notebooklm`).

## Step 3: Preflight Plan (Zero Writes)

Run `--plan --json` with the candidate options:

```sh
./install.sh --plan --json \
  --allowed-roots "<workspace-root>" \
  --keepalive
```

Handle non-`ready` outcomes cleanly before doing anything else:

- `"status": "needs_escalation"`: stop immediately and ask the user for write escalation to the exact `targets` listed in JSON.
- `"status": "conflict"`: review `conflicts` (`unmanaged_canonical_dir`, `unmanaged_symlink`, `non_symlink_exists`, `engine_version_mismatch`, `engine_missing_extra`, `shared_target_conflict`). Ask the user whether to pass `--adopt-legacy` / `--force-engine` rather than overriding silently.

## Step 4: One-Turn User Confirmation and Apply

Present the `--plan --json` summary concisely to the user and collect all decisions in **one single turn** (use a structured multi-choice tool such as `ask_question` if available; otherwise ask in plain text):

1. Approve the planned file/symlink/MCP changes.
2. Pick the workspace root(s) for `NOTEBOOKLM_MCP_ALLOWED_ROOTS` (if Antigravity is present).
3. Choose whether to enable the 20-minute background keepalive (note: `browser_fallback` defaults to `false` to prevent unattended macOS Keychain dialogs).
4. Pick the candidate browser profile (if importing cookies from Chrome/Brave/Edge) or choose browser login.

Once the user confirms, run `--apply` (include `--browser-profile` if the user chose a Chromium profile):

```sh
./install.sh --apply --non-interactive --json \
  --allowed-roots "<approved-workspace-root>" \
  --browser-profile "<selected-browser-profile>" \
  --keepalive \
  --tag "${TAG}"
```

If `--apply` fails halfway, run `./install.sh --rollback --json` to cleanly undo partial writes via the WAL manifest.

## Step 5: Foreground Authentication Setup (If Not Already Authenticated)

Follow the user's browser environment:

- **Chrome / Brave / Edge already signed in to `https://notebook.google.com/`**:
  1. Tell the user in advance: on macOS, the next step may pop up a Keychain prompt for `Chrome Safe Storage`; clicking **Always Allow** avoids repeated prompts.
  2. Run foreground inspect (returns masked account indices; plaintext email stays in `0600` local cache):
     ```sh
     ~/.agents/skills/notebooklm-t3/scripts/nlm setup-auth inspect --browser "chrome::Profile 1" --json
     ```
  3. If more than one account is returned (or `overwrite_confirmation_may_be_needed` is true), ask the user to confirm the account index (and overwrite if applicable).
  4. Apply binding:
     ```sh
     ~/.agents/skills/notebooklm-t3/scripts/nlm setup-auth apply --index 0 --json
     # Add --confirm-overwrite ONLY after explicit user confirmation
     ```
- **Safari only**: reading Safari cookies requires Full Disk Access. Recommend signing in via Chrome or running foreground Playwright login (`notebooklm login --browser chrome`, as a background task with 300s timeout; ensure network reaches Google first before downloading ~150MB bundled Chromium).
- **Headless / Linux server / WSL without desktop browser**: guide the user to export cookies on another machine and import via `notebooklm auth import-cookies`.

## Step 6: Health Check and New-Session Handoff

```sh
./install.sh --doctor --json
```

Verify `"ok": true`, `"connectivity.reachable": true` (or valid SOCKS + `socksio`), and `"auth_live_ok": true`.

Because `--doctor --json` sets `"requires_new_session": true` (newly installed skills and Antigravity MCP servers load on session start), finish by telling the user clearly:

> Installation and verification succeeded. Please open a new T3 Code thread to load `notebooklm-t3` (and Antigravity MCP), then simply state what you want NotebookLM to produce.
