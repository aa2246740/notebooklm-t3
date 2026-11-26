#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""notebooklm-t3 后台定时保活脚本（建议 20 分钟间隔）。

安全约束：
- 单次子进程严格 120 秒超时。
- 严禁传 --allow-headless，严禁调 login。
- keepalive.browser_fallback 默认 false，杜绝后台弹出 macOS Keychain 授权框挂死。
- 每次结果原子写入 ~/.local/state/notebooklm-t3/auth_status.json。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from _common import (  # noqa: E402
    DEFAULT_AUTH_TIMEOUT_SECONDS,
    DEFAULT_PROFILE,
    KEEPALIVE_LOG_MAX_LINES,
    build_proxy_env,
    get_keepalive_log_path,
    get_master_token_path,
    load_config,
    resolve_nlm_bin,
    utc_now_iso,
    write_auth_status,
)


def _append_log(line: str) -> None:
    log_path = get_keepalive_log_path()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(f"[{utc_now_iso()}] {line}\n")
    try:
        lines = log_path.read_text(encoding="utf-8").splitlines()
        if len(lines) > KEEPALIVE_LOG_MAX_LINES:
            log_path.write_text("\n".join(lines[-KEEPALIVE_LOG_MAX_LINES:]) + "\n", encoding="utf-8")
    except OSError:
        pass


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    cfg = load_config()
    nlm_bin = resolve_nlm_bin(cfg)

    if "--dry-run" in args:
        _, proxy_meta = build_proxy_env(cfg)
        print(
            json.dumps(
                {
                    "ok": True,
                    "dry_run": True,
                    "python": sys.version.split()[0],
                    "nlm_bin": str(nlm_bin) if nlm_bin else None,
                    "proxy_source": proxy_meta["source"],
                    "browser_fallback": bool((cfg.get("keepalive") or {}).get("browser_fallback", False)),
                },
                ensure_ascii=False,
            )
        )
        return 0

    if nlm_bin is None:
        _append_log("FAIL: notebooklm binary not found")
        write_auth_status(ok=False, state="missing_engine", method="keepalive", error="nlm_bin_not_found")
        return 127

    profile = str(cfg.get("profile") or DEFAULT_PROFILE)
    browser_fallback = bool((cfg.get("keepalive") or {}).get("browser_fallback", False))
    browser_profile = str(cfg.get("browser_profile") or "").strip()
    mt_flag = "present" if get_master_token_path(profile).is_file() else "absent"

    env, proxy_meta = build_proxy_env(cfg)
    env.pop("NOTEBOOKLM_DISABLE_KEEPALIVE_POKE", None)

    # 阶段 1：纯 RotateCookies（若存在 master_token.json，notebooklm-py 0.8.6 会自动无浏览器重铸 Cookie；绝不传 --allow-headless）
    cmd1 = [str(nlm_bin), "--profile", profile, "auth", "refresh", "--verify", "--quiet"]
    try:
        p1 = subprocess.run(
            cmd1,
            env=env,
            capture_output=True,
            text=True,
            timeout=DEFAULT_AUTH_TIMEOUT_SECONDS,
            check=False,
        )
        if p1.returncode == 0:
            _append_log(f"OK: auth refresh --verify succeeded (proxy={proxy_meta['source']}, master_token={mt_flag})")
            write_auth_status(ok=True, state="ok", method="keepalive.refresh")
            return 0
        err1 = (p1.stderr or p1.stdout or f"exit={p1.returncode}").strip()[:240]
        _append_log(f"WARN: auth refresh failed (master_token={mt_flag}; {err1})")
    except subprocess.TimeoutExpired:
        _append_log("FAIL: auth refresh timed out after 120s")
        write_auth_status(ok=False, state="network_timeout", method="keepalive.refresh", error="timeout_120s")
        return 3

    # 阶段 2：仅当用户显式开启 browser_fallback 且绑定了含 "::" 的 profile 时才尝试匹配账号刷新
    if browser_fallback and "::" in browser_profile:
        cmd2 = [
            str(nlm_bin),
            "--profile",
            profile,
            "auth",
            "refresh",
            "--browser-cookies",
            browser_profile,
            "--verify",
            "--quiet",
        ]
        try:
            p2 = subprocess.run(
                cmd2,
                env=env,
                capture_output=True,
                text=True,
                timeout=DEFAULT_AUTH_TIMEOUT_SECONDS,
                check=False,
            )
            if p2.returncode == 0:
                _append_log(f"OK: recovered via browser_fallback ({browser_profile})")
                write_auth_status(ok=True, state="ok", method="keepalive.browser_refresh")
                return 0
            err2 = (p2.stderr or p2.stdout or f"exit={p2.returncode}").strip()[:240]
            _append_log(f"FAIL: browser_fallback refresh failed ({err2})")
        except subprocess.TimeoutExpired:
            _append_log("FAIL: browser_fallback timed out after 120s (keychain_blocked); not retrying")
            write_auth_status(
                ok=False,
                state="keychain_blocked",
                method="keepalive.browser_refresh",
                error="timeout_120s_possible_keychain_prompt",
            )
            return 3

    write_auth_status(
        ok=False,
        state="expired",
        method="keepalive",
        error="auth_refresh_failed_manual_reauth_required",
    )
    _append_log("FAIL: manual re-authentication required (browser_fallback disabled or failed)")
    return 1


if __name__ == "__main__":
    sys.exit(main())
