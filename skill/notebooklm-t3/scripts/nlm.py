#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""notebooklm-t3 非交互安全 CLI 包装器。

职责：
1. 默认注入 NOTEBOOKLM_DISABLE_KEEPALIVE_POKE=1，避免多进程轮询争抢 RotateCookies。
2. 统一代理透传（config > env > 系统代理 > 直连），同时透传 NO_PROXY。
3. 硬拦截高危参数：非前台白名单下的 --account、裸 --browser-cookies、language set、
   skill install 与 --allow-headless。
4. 按实时 --help 能力探测自动兼容缺少 --language 的 generate 子命令（如 0.8.6 的 quiz/flashcards）。
5. 提供无人值守安全的 check / recover（严禁调 login）与前台三步白名单 setup-auth。
6. 托管运行本地确定性 HTML 渲染器（render-study-card / render-mind-map / render-slide-deck）。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from _common import (  # noqa: E402
    DEFAULT_AUTH_TIMEOUT_SECONDS,
    DEFAULT_PROFILE,
    SETUP_CANDIDATES_TTL_SECONDS,
    atomic_write_json,
    build_proxy_env,
    detect_system_login_browser,
    get_capabilities_cache_path,
    get_config_path,
    get_home,
    get_master_token_path,
    get_setup_candidates_path,
    get_storage_state_path,
    load_config,
    load_json_file,
    mask_email,
    parse_utc_iso,
    read_auth_status,
    resolve_nlm_bin,
    save_config,
    utc_now_iso,
    write_auth_status,
)

GLOBAL_OPTS_WITH_VAL = {"-p", "--profile", "--storage", "--backend"}
GENERATE_OPTS_WITH_VAL = {
    "-n",
    "--notebook",
    "--prompt-file",
    "--quantity",
    "--difficulty",
    "-s",
    "--source",
    "--timeout",
    "--interval",
    "--retry",
    "--format",
    "--length",
    "--style",
    "--orientation",
    "--detail",
    "--append",
    "-p",
    "--profile",
    "--storage",
    "--backend",
}


def _eprint(msg: str) -> None:
    print(msg, file=sys.stderr)


def _require_nlm_bin(config: dict[str, Any]) -> Path:
    nlm_bin = resolve_nlm_bin(config)
    if nlm_bin is None:
        _eprint("ERROR: notebooklm CLI not found. Run notebooklm-t3 installer or --doctor first.")
        sys.exit(127)
    return nlm_bin


def _extract_subcommands(args: list[str]) -> tuple[str, str]:
    sub1 = ""
    sub2 = ""
    skip_next = False
    for arg in args:
        if skip_next:
            skip_next = False
            continue
        if arg in GLOBAL_OPTS_WITH_VAL:
            skip_next = True
            continue
        if arg.startswith("-"):
            continue
        if not sub1:
            sub1 = arg
        elif not sub2:
            sub2 = arg
            break
    return sub1, sub2


def _extract_json_payload(stdout: str) -> Any:
    """从可能混有前置警告文字的 CLI 输出中解析 JSON 对象或数组。"""
    text = stdout.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    for idx, ch in enumerate(text):
        if ch in "{[":
            try:
                return json.loads(text[idx:])
            except json.JSONDecodeError:
                continue
    return None


def _parse_inspect_accounts(payload: Any, fallback_browser: str) -> list[dict[str, str]]:
    """兼容 notebooklm auth inspect --json 的多种列表包装结构。"""
    rows: list[Any] = []
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict):
        for key in ("accounts", "items", "profiles", "results"):
            if isinstance(payload.get(key), list):
                rows = payload[key]
                break
        if not rows and isinstance(payload.get("email"), str):
            rows = [payload]

    accounts: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        email = str(row.get("email") or row.get("account") or "").strip()
        if not email:
            continue
        bp = str(
            row.get("browser_profile")
            or row.get("profile")
            or row.get("browser")
            or fallback_browser
        ).strip()
        if "::" not in bp and "::" in fallback_browser:
            bp = fallback_browser
        key = (email.lower(), bp)
        if key in seen:
            continue
        seen.add(key)
        accounts.append({"email": email, "browser_profile": bp})
    return accounts


def _cmd_self_check() -> int:
    """前台预热 uv 托管 Python 环境，零网络调用。"""
    cfg = load_config()
    nlm_bin = resolve_nlm_bin(cfg)
    print(
        json.dumps(
            {
                "ok": True,
                "python": sys.version.split()[0],
                "config_exists": get_config_path().exists(),
                "nlm_bin": str(nlm_bin) if nlm_bin else None,
            },
            ensure_ascii=False,
        )
    )
    return 0


def _cmd_status(args: list[str]) -> int:
    as_json = "--json" in args
    cfg = load_config()
    _, proxy_meta = build_proxy_env(cfg)
    auth_status = read_auth_status()
    nlm_bin = resolve_nlm_bin(cfg)
    profile = str(cfg.get("profile") or DEFAULT_PROFILE)
    data = {
        "profile": profile,
        "account_email_masked": mask_email(cfg.get("account_email")),
        "browser_profile": cfg.get("browser_profile"),
        "nlm_bin": str(nlm_bin) if nlm_bin else None,
        "keepalive_browser_fallback": bool(
            (cfg.get("keepalive") or {}).get("browser_fallback", False)
        ),
        "proxy_source": proxy_meta["source"],
        "proxy_has_socks": proxy_meta["has_socks"],
        "auth_status": auth_status,
        "storage_exists": get_storage_state_path(profile).exists(),
        "master_token_present": get_master_token_path(profile).is_file(),
    }
    if as_json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        print(f"profile: {data['profile']}")
        print(f"account: {data['account_email_masked'] or '(unbound)'}")
        print(f"browser_profile: {data['browser_profile'] or '(none)'}")
        print(f"master_token: {'present' if data['master_token_present'] else 'absent'}")
        print(f"auth_status: {auth_status['state']} (stale={auth_status['stale']})")
        print(f"proxy_source: {proxy_meta['source']}")
    return 0


def _cmd_check(extra_args: list[str]) -> int:
    cfg = load_config()
    nlm_bin = _require_nlm_bin(cfg)
    profile = str(cfg.get("profile") or DEFAULT_PROFILE)
    env, _ = build_proxy_env(cfg)
    env["NOTEBOOKLM_DISABLE_KEEPALIVE_POKE"] = "1"
    cmd = [str(nlm_bin), "--profile", profile, "auth", "check", "--test", "--passive", *extra_args]
    try:
        proc = subprocess.run(cmd, env=env, timeout=60, check=False)
    except subprocess.TimeoutExpired:
        write_auth_status(ok=False, state="network_timeout", method="nlm.check", error="timeout")
        _eprint("[nlm] auth check timed out after 60s.")
        return 3
    if proc.returncode == 0:
        write_auth_status(ok=True, state="ok", method="nlm.check")
    else:
        write_auth_status(ok=False, state="expired", method="nlm.check", error=f"exit={proc.returncode}")
    return proc.returncode


def _cmd_recover(extra_args: list[str]) -> int:
    """无人值守安全自愈：只调用 auth check / auth refresh，绝不调用 login。"""
    cfg = load_config()
    nlm_bin = _require_nlm_bin(cfg)
    profile = str(cfg.get("profile") or DEFAULT_PROFILE)
    browser_profile = str(cfg.get("browser_profile") or "").strip()
    env, _ = build_proxy_env(cfg)

    # 1. 快速被动健康检查
    check_env = dict(env)
    check_env["NOTEBOOKLM_DISABLE_KEEPALIVE_POKE"] = "1"
    _eprint("[nlm] Checking current profile auth...")
    try:
        check_proc = subprocess.run(
            [str(nlm_bin), "--profile", profile, "auth", "check", "--test", "--passive"],
            env=check_env,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if check_proc.returncode == 0:
            write_auth_status(ok=True, state="ok", method="nlm.recover.check")
            _eprint("[nlm] OK: profile auth is already valid.")
            return 0
    except subprocess.TimeoutExpired:
        _eprint("[nlm] WARN: passive check timed out; attempting auth refresh.")

    # 2. 尝试无浏览器 RotateCookies 刷新（绝不传 --allow-headless）
    refresh_env = dict(env)
    refresh_env.pop("NOTEBOOKLM_DISABLE_KEEPALIVE_POKE", None)
    _eprint("[nlm] Attempting auth refresh --verify (no browser)...")
    try:
        r1 = subprocess.run(
            [str(nlm_bin), "--profile", profile, "auth", "refresh", "--verify", *extra_args],
            env=refresh_env,
            timeout=DEFAULT_AUTH_TIMEOUT_SECONDS,
            check=False,
        )
        if r1.returncode == 0:
            write_auth_status(ok=True, state="ok", method="nlm.recover.refresh")
            _eprint("[nlm] OK: auth refresh --verify succeeded.")
            return 0
    except subprocess.TimeoutExpired:
        write_auth_status(
            ok=False, state="network_timeout", method="nlm.recover.refresh", error="timeout_120s"
        )
        _eprint("[nlm] FAIL: auth refresh timed out after 120s.")
        return 3

    # 3. 仅在明确绑定含 "::" 的 browser_profile 时，用 auth refresh --browser-cookies 按绑定账号同步
    if "::" in browser_profile:
        _eprint(f"[nlm] Trying matched-account cookie refresh from {browser_profile}...")
        try:
            r2 = subprocess.run(
                [
                    str(nlm_bin),
                    "--profile",
                    profile,
                    "auth",
                    "refresh",
                    "--browser-cookies",
                    browser_profile,
                    "--verify",
                    *extra_args,
                ],
                env=refresh_env,
                timeout=DEFAULT_AUTH_TIMEOUT_SECONDS,
                check=False,
            )
            if r2.returncode == 0:
                write_auth_status(ok=True, state="ok", method="nlm.recover.browser_refresh")
                _eprint(f"[nlm] OK: recovered via {browser_profile}.")
                return 0
        except subprocess.TimeoutExpired:
            write_auth_status(
                ok=False,
                state="keychain_blocked",
                method="nlm.recover.browser_refresh",
                error="timeout_120s_possible_keychain_prompt",
            )
            _eprint(
                "[nlm] FAIL: browser cookie refresh timed out after 120s "
                "(likely blocked on macOS Keychain dialog). Not retrying."
            )
            return 3

    write_auth_status(ok=False, state="expired", method="nlm.recover", error="manual_reauth_required")
    _eprint(
        "[nlm] FAIL: automatic recovery did not succeed. For sleep-proof auth, run in terminal: "
        "nlm setup-auth master-token (or re-bind Chrome cookies via nlm setup-auth inspect --browser <browser::profile>, "
        "or foreground login with: nlm login --browser chrome)."
    )
    return 1


def _resolve_saved_account_email(cfg: dict[str, Any], profile: str) -> str:
    """从 0600 本地配置或 profile account.json 解析明文邮箱（仅用于本机命令传参，绝不打印）。"""
    email = str(cfg.get("account_email") or "").strip()
    if email:
        return email
    acct_file = get_home() / ".notebooklm" / "profiles" / profile / "account.json"
    acct_doc = load_json_file(acct_file, default=None)
    if isinstance(acct_doc, dict):
        cand = str(acct_doc.get("email") or acct_doc.get("account_email") or "").strip()
        if cand:
            return cand
    return ""


def _engine_has_gpsoauth(nlm_bin: Path) -> bool:
    """检查 notebooklm-py 专属 Python 环境是否已装 gpsoauth（[headless] extra）。"""
    try:
        first_line = nlm_bin.read_text(encoding="utf-8", errors="ignore").splitlines()[0]
    except (OSError, IndexError):
        return True
    if not first_line.startswith("#!"):
        return True
    shebang = first_line[2:].strip().split()
    if not shebang:
        return True
    py_bin = Path(shebang[0])
    if not py_bin.is_file() or py_bin.name == "env":
        return True
    try:
        proc = subprocess.run(
            [str(py_bin), "-c", "import gpsoauth"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        return proc.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return True


def _cmd_setup_auth(args: list[str]) -> int:
    """前台白名单账号绑定：inspect/apply（浏览器 Cookie）或 master-token（长期持久令牌）。"""
    if not args or args[0] in ("-h", "--help"):
        print(
            "Usage:\n"
            "  nlm setup-auth inspect --browser <chrome::Profile N> [--json]\n"
            "  nlm setup-auth apply --index <N> [--confirm-overwrite] [--json]\n"
            "  nlm setup-auth master-token [--browser chrome|msedge|chromium] [--cdp-url <url>] [--force] [--json]"
        )
        return 0

    sub = args[0]
    rest = args[1:]
    as_json = "--json" in rest
    cfg = load_config()
    nlm_bin = _require_nlm_bin(cfg)
    profile = str(cfg.get("profile") or DEFAULT_PROFILE)
    env, _ = build_proxy_env(cfg)

    if sub == "inspect":
        browser_spec = ""
        i = 0
        while i < len(rest):
            if rest[i] == "--browser" and i + 1 < len(rest):
                browser_spec = rest[i + 1]
                i += 2
            elif rest[i].startswith("--browser="):
                browser_spec = rest[i].split("=", 1)[1]
                i += 1
            else:
                i += 1
        if not browser_spec:
            _eprint("ERROR: setup-auth inspect requires --browser <browser::profile> (e.g. 'chrome::Profile 1').")
            return 2

        cmd = [str(nlm_bin), "--profile", profile, "auth", "inspect", "--browser", browser_spec, "--json"]
        try:
            proc = subprocess.run(
                cmd,
                env=env,
                capture_output=True,
                text=True,
                timeout=DEFAULT_AUTH_TIMEOUT_SECONDS,
                check=False,
            )
        except subprocess.TimeoutExpired:
            write_auth_status(
                ok=False,
                state="keychain_blocked",
                method="nlm.setup_auth.inspect",
                error="timeout_120s_possible_keychain_prompt",
            )
            out = {"ok": False, "error": "keychain_blocked", "message": "Timed out waiting for browser cookie access (120s)."}
            print(json.dumps(out, ensure_ascii=False, indent=2) if as_json else out["message"])
            return 3

        if proc.returncode != 0:
            err_msg = (proc.stderr or proc.stdout or "").strip()
            out = {"ok": False, "error": "inspect_failed", "exit_code": proc.returncode, "details": err_msg[:800]}
            print(json.dumps(out, ensure_ascii=False, indent=2) if as_json else f"ERROR: {err_msg}")
            return proc.returncode

        parsed = _extract_json_payload(proc.stdout)
        accounts = _parse_inspect_accounts(parsed, browser_spec)
        if not accounts:
            out = {
                "ok": False,
                "error": "no_accounts_found",
                "browser": browser_spec,
                "message": "No signed-in Google accounts found in that browser profile.",
            }
            print(json.dumps(out, ensure_ascii=False, indent=2) if as_json else out["message"])
            return 1

        # 缓存明文映射到 0600 短期状态文件，绝不向模型上下文输出明文邮箱
        cache_payload = {
            "inspect_at": utc_now_iso(),
            "browser_spec": browser_spec,
            "candidates": [
                {"index": idx, "email": item["email"], "browser_profile": item["browser_profile"]}
                for idx, item in enumerate(accounts)
            ],
        }
        atomic_write_json(get_setup_candidates_path(), cache_payload, mode=0o600)

        storage_exists = get_storage_state_path(profile).exists()
        existing_email = str(cfg.get("account_email") or "").strip()
        masked_list = [
            {
                "index": idx,
                "masked_email": mask_email(item["email"]),
                "browser_profile": item["browser_profile"],
            }
            for idx, item in enumerate(accounts)
        ]
        result = {
            "ok": True,
            "browser": browser_spec,
            "ttl_seconds": SETUP_CANDIDATES_TTL_SECONDS,
            "existing_storage": storage_exists,
            "existing_account_masked": mask_email(existing_email) if existing_email else None,
            "overwrite_confirmation_may_be_needed": storage_exists,
            "candidates": masked_list,
        }
        if as_json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(f"Found {len(masked_list)} account(s) in {browser_spec}:")
            for item in masked_list:
                print(f"  [{item['index']}] {item['masked_email']} ({item['browser_profile']})")
        return 0

    if sub == "apply":
        index_val: int | None = None
        confirm_overwrite = False
        i = 0
        while i < len(rest):
            if rest[i] == "--index" and i + 1 < len(rest):
                try:
                    index_val = int(rest[i + 1])
                except ValueError:
                    _eprint("ERROR: --index must be an integer.")
                    return 2
                i += 2
            elif rest[i].startswith("--index="):
                try:
                    index_val = int(rest[i].split("=", 1)[1])
                except ValueError:
                    _eprint("ERROR: --index must be an integer.")
                    return 2
                i += 1
            elif rest[i] == "--confirm-overwrite":
                confirm_overwrite = True
                i += 1
            else:
                i += 1

        if index_val is None:
            _eprint("ERROR: setup-auth apply requires --index <N>.")
            return 2

        cand_path = get_setup_candidates_path()
        cached = load_json_file(cand_path, default=None)
        if not isinstance(cached, dict) or not isinstance(cached.get("candidates"), list):
            msg = "No cached setup candidates. Run 'nlm setup-auth inspect --browser <spec> --json' first."
            print(json.dumps({"ok": False, "error": "missing_candidates", "message": msg}, ensure_ascii=False) if as_json else f"ERROR: {msg}")
            return 1

        # 检查 10 分钟 TTL
        inspect_dt = parse_utc_iso(cached.get("inspect_at"))
        if inspect_dt is None or (datetime.now(timezone.utc) - inspect_dt).total_seconds() > SETUP_CANDIDATES_TTL_SECONDS:
            msg = "Cached setup candidates expired (>10m). Re-run 'nlm setup-auth inspect'."
            print(json.dumps({"ok": False, "error": "candidates_expired", "message": msg}, ensure_ascii=False) if as_json else f"ERROR: {msg}")
            return 1

        candidates: list[dict[str, Any]] = cached["candidates"]
        if index_val < 0 or index_val >= len(candidates):
            msg = f"Index {index_val} out of range (0..{len(candidates) - 1})."
            print(json.dumps({"ok": False, "error": "index_out_of_range", "message": msg}, ensure_ascii=False) if as_json else f"ERROR: {msg}")
            return 2

        selected = candidates[index_val]
        browser_spec = str(cached.get("browser_spec") or selected["browser_profile"])

        # apply 前重新 inspect 比对邮箱集合，防止两次调用之间账号变动导致序号错位
        try:
            re_proc = subprocess.run(
                [str(nlm_bin), "--profile", profile, "auth", "inspect", "--browser", browser_spec, "--json"],
                env=env,
                capture_output=True,
                text=True,
                timeout=DEFAULT_AUTH_TIMEOUT_SECONDS,
                check=False,
            )
        except subprocess.TimeoutExpired:
            write_auth_status(
                ok=False,
                state="keychain_blocked",
                method="nlm.setup_auth.reinspect",
                error="timeout_120s_possible_keychain_prompt",
            )
            msg = "Timed out re-inspecting browser profile (120s, likely blocked on Keychain)."
            print(json.dumps({"ok": False, "error": "keychain_blocked", "message": msg}, ensure_ascii=False) if as_json else f"ERROR: {msg}")
            return 3

        if re_proc.returncode != 0:
            msg = "Failed to re-inspect browser profile before apply."
            print(json.dumps({"ok": False, "error": "reinspect_failed", "message": msg}, ensure_ascii=False) if as_json else f"ERROR: {msg}")
            return re_proc.returncode

        live_accounts = _parse_inspect_accounts(_extract_json_payload(re_proc.stdout), browser_spec)
        cached_emails = {str(c["email"]).lower() for c in candidates}
        live_emails = {a["email"].lower() for a in live_accounts}
        if cached_emails != live_emails:
            msg = "Browser account set changed since inspect. Re-run 'nlm setup-auth inspect' before applying."
            print(json.dumps({"ok": False, "error": "accounts_changed", "message": msg}, ensure_ascii=False) if as_json else f"ERROR: {msg}")
            return 1

        target_email = str(selected["email"])
        target_bp = str(selected["browser_profile"])

        # 前台白名单路径：调用 login --browser-cookies <bp> --account <email>
        # 显式向 stdin 传 "y\n" 或 "n\n"，确保 click.confirm 绝不挂死
        login_cmd = [
            str(nlm_bin),
            "--profile",
            profile,
            "login",
            "--browser-cookies",
            target_bp,
            "--account",
            target_email,
        ]
        stdin_answer = "y\n" if confirm_overwrite else "n\n"
        try:
            login_proc = subprocess.run(
                login_cmd,
                env=env,
                input=stdin_answer,
                capture_output=True,
                text=True,
                timeout=DEFAULT_AUTH_TIMEOUT_SECONDS,
                check=False,
            )
        except subprocess.TimeoutExpired:
            write_auth_status(
                ok=False,
                state="keychain_blocked",
                method="nlm.setup_auth.apply",
                error="timeout_120s_possible_keychain_prompt",
            )
            msg = "Foreground browser-cookie login timed out after 120s (likely blocked on Keychain)."
            print(json.dumps({"ok": False, "error": "keychain_blocked", "message": msg}, ensure_ascii=False) if as_json else f"ERROR: {msg}")
            return 3

        if login_proc.returncode != 0:
            # 失败时保留 setup_candidates.json，免得用户拒绝覆盖后还要重新扫描
            combined = ((login_proc.stdout or "") + "\n" + (login_proc.stderr or "")).strip()
            needs_confirm = "Aborted:" in combined or "Overwrite" in combined
            err_code = "overwrite_confirmation_required" if (needs_confirm and not confirm_overwrite) else "login_failed"
            out = {
                "ok": False,
                "error": err_code,
                "exit_code": login_proc.returncode,
                "message": (
                    "Target profile already holds another account; re-run with --confirm-overwrite after user confirms."
                    if err_code == "overwrite_confirmation_required"
                    else "Browser cookie login failed."
                ),
            }
            print(json.dumps(out, ensure_ascii=False, indent=2) if as_json else f"ERROR: {out['message']}")
            return login_proc.returncode

        # 验证 storage_state.json 落盘且被动校验通过
        storage_path = get_storage_state_path(profile)
        verify_env = dict(env)
        verify_env["NOTEBOOKLM_DISABLE_KEEPALIVE_POKE"] = "1"
        try:
            verify_proc = subprocess.run(
                [str(nlm_bin), "--profile", profile, "auth", "check", "--test", "--passive"],
                env=verify_env,
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
        except subprocess.TimeoutExpired:
            write_auth_status(
                ok=False,
                state="network_timeout",
                method="nlm.setup_auth.verify",
                error="timeout_60s",
            )
            out = {
                "ok": False,
                "error": "verify_timeout",
                "message": "Post-login auth check timed out after 60s.",
            }
            print(json.dumps(out, ensure_ascii=False, indent=2) if as_json else f"ERROR: {out['message']}")
            return 3
        if not storage_path.exists() or verify_proc.returncode != 0:
            write_auth_status(ok=False, state="expired", method="nlm.setup_auth.verify", error="verify_failed")
            out = {"ok": False, "error": "verify_failed", "message": "Imported cookies did not pass auth check."}
            print(json.dumps(out, ensure_ascii=False, indent=2) if as_json else f"ERROR: {out['message']}")
            return 1

        # 绑定写入 0600 config.json，并清理临时候选文件
        cfg["profile"] = profile
        cfg["browser_profile"] = target_bp
        cfg["account_email"] = target_email
        save_config(cfg)
        write_auth_status(ok=True, state="ok", method="nlm.setup_auth.apply")
        try:
            cand_path.unlink(missing_ok=True)
        except OSError:
            pass

        result = {
            "ok": True,
            "profile": profile,
            "browser_profile": target_bp,
            "account_email_masked": mask_email(target_email),
            "storage_path": str(storage_path),
        }
        if as_json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(f"OK: bound profile '{profile}' -> {result['account_email_masked']} ({target_bp})")
        return 0

    if sub == "master-token":
        browser_choice = ""
        cdp_url = ""
        explicit_email = ""
        force_overwrite = False
        i = 0
        while i < len(rest):
            tok = rest[i]
            if tok == "--browser" and i + 1 < len(rest):
                browser_choice = rest[i + 1].strip()
                i += 2
            elif tok.startswith("--browser="):
                browser_choice = tok.split("=", 1)[1].strip()
                i += 1
            elif tok == "--cdp-url" and i + 1 < len(rest):
                cdp_url = rest[i + 1].strip()
                i += 2
            elif tok.startswith("--cdp-url="):
                cdp_url = tok.split("=", 1)[1].strip()
                i += 1
            elif tok == "--email" and i + 1 < len(rest):
                explicit_email = rest[i + 1].strip()
                i += 2
            elif tok.startswith("--email="):
                explicit_email = tok.split("=", 1)[1].strip()
                i += 1
            elif tok == "--force":
                force_overwrite = True
                i += 1
            else:
                i += 1

        if not _engine_has_gpsoauth(nlm_bin):
            msg = (
                "Engine venv is missing 'gpsoauth' (notebooklm-py[headless] extra). "
                "Re-run installer with --force-engine to install it."
            )
            out = {"ok": False, "error": "missing_headless_extra", "message": msg}
            print(json.dumps(out, ensure_ascii=False, indent=2) if as_json else f"ERROR: {msg}")
            return 2

        target_email = explicit_email or _resolve_saved_account_email(cfg, profile)
        if not target_email:
            msg = (
                "No bound account email found in config.json or profile metadata. "
                "Pass --email <your-google-email> in terminal, or bind via 'nlm setup-auth inspect' first."
            )
            out = {"ok": False, "error": "missing_account_email", "message": msg}
            print(json.dumps(out, ensure_ascii=False, indent=2) if as_json else f"ERROR: {msg}")
            return 2

        chosen_browser = browser_choice or detect_system_login_browser() or "chrome"
        login_cmd = [
            str(nlm_bin),
            "--profile",
            profile,
            "login",
            "--master-token",
            "--account",
            target_email,
        ]
        if cdp_url:
            login_cmd.extend(["--cdp-url", cdp_url])
        else:
            login_cmd.extend(["--browser", chosen_browser])
        if force_overwrite:
            login_cmd.append("--force")

        _eprint(
            f"[nlm] Bootstrapping durable master token for {mask_email(target_email)} "
            f"(browser={chosen_browser if not cdp_url else 'cdp'}, timeout=300s)..."
        )
        try:
            proc = subprocess.run(
                login_cmd,
                env=env,
                stdout=sys.stderr if as_json else None,
                timeout=320,
                check=False,
            )
        except subprocess.TimeoutExpired:
            write_auth_status(
                ok=False,
                state="network_timeout",
                method="nlm.setup_auth.master_token",
                error="timeout_320s",
            )
            msg = "Master-token login timed out after 320s."
            print(json.dumps({"ok": False, "error": "timeout", "message": msg}, ensure_ascii=False) if as_json else f"ERROR: {msg}")
            return 3

        if proc.returncode != 0:
            msg = f"Master-token login failed (exit {proc.returncode})."
            out = {"ok": False, "error": "master_token_login_failed", "exit_code": proc.returncode, "message": msg}
            print(json.dumps(out, ensure_ascii=False, indent=2) if as_json else f"ERROR: {msg}")
            return proc.returncode

        mt_path = get_master_token_path(profile)
        verify_env = dict(env)
        verify_env["NOTEBOOKLM_DISABLE_KEEPALIVE_POKE"] = "1"
        verify_proc = subprocess.run(
            [str(nlm_bin), "--profile", profile, "auth", "check", "--test", "--passive"],
            env=verify_env,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        if not mt_path.is_file() or verify_proc.returncode != 0:
            write_auth_status(
                ok=False,
                state="expired",
                method="nlm.setup_auth.master_token",
                error="verify_failed",
            )
            msg = "Master token file missing or post-login auth check failed."
            print(json.dumps({"ok": False, "error": "verify_failed", "message": msg}, ensure_ascii=False) if as_json else f"ERROR: {msg}")
            return 1

        cfg["profile"] = profile
        cfg["account_email"] = target_email
        save_config(cfg)
        write_auth_status(ok=True, state="ok", method="nlm.setup_auth.master_token")
        result = {
            "ok": True,
            "profile": profile,
            "account_email_masked": mask_email(target_email),
            "master_token_present": True,
            "browser": chosen_browser if not cdp_url else "cdp",
        }
        if as_json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(
                f"OK: durable master token saved for '{profile}' ({result['account_email_masked']}). "
                "Keepalive and nlm recover can now re-mint cookies silently even after sleep."
            )
        return 0

    _eprint(f"ERROR: unknown setup-auth subcommand '{sub}'. Expected 'inspect', 'apply', or 'master-token'.")
    return 2


def _supports_generate_language(nlm_bin: Path, subcommand: str, env: dict[str, str]) -> bool:
    """按 --help 实时能力探测 generate <subcommand> 是否支持 --language，结果按二进制指纹缓存。"""
    try:
        st = nlm_bin.stat()
        fingerprint = f"{nlm_bin}:{st.st_mtime_ns}:{st.st_size}"
    except OSError:
        fingerprint = str(nlm_bin)

    cache_path = get_capabilities_cache_path()
    cache = load_json_file(cache_path, default={})
    if not isinstance(cache, dict) or cache.get("fingerprint") != fingerprint:
        cache = {"fingerprint": fingerprint, "generate_language": {}}

    gen_map = cache.get("generate_language")
    if isinstance(gen_map, dict) and subcommand in gen_map:
        return bool(gen_map[subcommand])

    try:
        proc = subprocess.run(
            [str(nlm_bin), "generate", subcommand, "--help"],
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        supported = "--language" in (proc.stdout + proc.stderr)
    except (OSError, subprocess.SubprocessError):
        supported = True

    if not isinstance(cache.get("generate_language"), dict):
        cache["generate_language"] = {}
    cache["generate_language"][subcommand] = supported
    try:
        atomic_write_json(cache_path, cache, mode=0o600)
    except OSError:
        pass
    return supported


def _rewrite_unsupported_language(args: list[str], sub2: str) -> list[str]:
    """当 generate <sub2> 不支持 --language 时，剥离该参数并转为提示词语言约束。"""
    cleaned: list[str] = []
    stripped_lang = ""
    has_prompt_file = False
    opt_takes_val = False
    desc_idx = -1
    seen_sub2 = False

    i = 0
    while i < len(args):
        arg = args[i]
        if opt_takes_val:
            cleaned.append(arg)
            opt_takes_val = False
            i += 1
            continue

        if arg == "--language":
            if i + 1 < len(args):
                stripped_lang = args[i + 1]
                i += 2
                continue
            i += 1
            continue
        if arg.startswith("--language="):
            stripped_lang = arg.split("=", 1)[1]
            i += 1
            continue

        if arg == "--prompt-file" or arg.startswith("--prompt-file="):
            has_prompt_file = True

        cleaned.append(arg)
        if arg in GENERATE_OPTS_WITH_VAL:
            opt_takes_val = True
            i += 1
            continue

        if not seen_sub2:
            if arg == sub2:
                seen_sub2 = True
        elif not arg.startswith("-"):
            desc_idx = len(cleaned) - 1
        i += 1

    if stripped_lang:
        _eprint(
            f"[nlm] Note: 'generate {sub2}' does not support --language ({stripped_lang}); "
            "steering output language via prompt instructions."
        )
        lang_low = stripped_lang.lower()
        if lang_low in ("zh", "zh_hans", "zh-cn", "zh_cn"):
            hint = "请全部使用简体中文生成（题干、选项、提示、答案与解析或正反面均为简体中文）。"
        elif lang_low in ("zh_hant", "zh-tw", "zh_tw"):
            hint = "請全部使用繁體中文生成。"
        elif lang_low in ("ja", "ja_jp"):
            hint = "すべて日本語で生成してください。"
        else:
            hint = f"Generate all content in language: {stripped_lang}. "

        if not has_prompt_file:
            if desc_idx >= 0:
                cleaned[desc_idx] = hint + cleaned[desc_idx]
            else:
                cleaned.append(hint)
    return cleaned


def _run_renderer(module_name: str, args: list[str]) -> int:
    renderer = SCRIPT_DIR / f"{module_name}.py"
    if not renderer.is_file():
        _eprint(f"ERROR: renderer not found: {renderer}")
        return 127
    proc = subprocess.run([sys.executable, str(renderer), *args], check=False)
    return proc.returncode


def _check_guardrails(args: list[str], sub1: str, sub2: str) -> int | None:
    """检查 CLI 硬护栏；命中返回退出码 2，放行返回 None。"""
    for i, arg in enumerate(args):
        next_arg = args[i + 1] if i + 1 < len(args) else ""

        if arg == "--account" or arg.startswith("--account="):
            _eprint(
                "BLOCKED by nlm: '--account' triggers blocking interactive click.confirm in non-interactive shells.\n"
                "Use foreground onboarding: nlm setup-auth inspect --browser <chrome::Profile N>"
            )
            return 2

        if arg == "--allow-headless":
            _eprint("BLOCKED by nlm: '--allow-headless' is disabled in non-interactive CLI wrapper.")
            return 2

        if arg == "--browser-cookies":
            if not next_arg or "::" not in next_arg:
                _eprint(
                    f"BLOCKED by nlm: bare '--browser-cookies {next_arg or '<empty>'}' overwrites profile with default browser account.\n"
                    "Specify explicit '<browser>::<profile>' or run: nlm recover"
                )
                return 2
        elif arg.startswith("--browser-cookies="):
            val = arg.split("=", 1)[1]
            if "::" not in val:
                _eprint(
                    f"BLOCKED by nlm: bare '--browser-cookies={val}' overwrites profile with default browser account.\n"
                    "Specify explicit '<browser>::<profile>' or run: nlm recover"
                )
                return 2

    if sub1 == "language" and sub2 == "set":
        _eprint(
            "BLOCKED by nlm: 'language set' mutates global account language. "
            "Pass --language zh_Hans per generate call instead."
        )
        return 2

    if sub1 == "skill" and sub2 == "install":
        _eprint("BLOCKED by nlm: 'skill install' conflicts with managed notebooklm-t3 skill.")
        return 2

    return None


def _inject_default_login_browser(args: list[str], sub1: str) -> list[str]:
    """当执行 `nlm login` 且未显式指定 --browser / --browser-cookies 时，自动复用本机 Chrome/Edge 避免下载 ~600MB 内置 Chromium。"""
    if sub1 != "login":
        return args
    skip_flags = {
        "-h",
        "--help",
        "--browser",
        "--browser-cookies",
        "--master-token-refresh",
        "--oauth-token",
        "--cdp-url",
    }
    for tok in args:
        if tok in skip_flags or any(tok.startswith(f + "=") for f in skip_flags if f.startswith("--")):
            return args
    sys_browser = detect_system_login_browser()
    if not sys_browser:
        return args
    _eprint(
        f"[nlm] Auto-selected system browser (--browser {sys_browser}) "
        "to avoid downloading ~600MB bundled Playwright Chromium."
    )
    return [*args, "--browser", sys_browser]


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        _eprint("Usage: nlm <check|recover|status|setup-auth|render-*|notebooklm-args...>")
        return 2

    cmd = args[0]
    if cmd == "--self-check":
        return _cmd_self_check()
    if cmd == "status":
        return _cmd_status(args[1:])
    if cmd == "check":
        return _cmd_check(args[1:])
    if cmd == "recover":
        return _cmd_recover(args[1:])
    if cmd == "setup-auth":
        return _cmd_setup_auth(args[1:])
    if cmd == "render-study-card":
        return _run_renderer("render_study_card", args[1:])
    if cmd == "render-mind-map":
        return _run_renderer("render_mind_map", args[1:])
    if cmd == "render-slide-deck":
        return _run_renderer("render_slide_deck", args[1:])

    sub1, sub2 = _extract_subcommands(args)
    blocked_rc = _check_guardrails(args, sub1, sub2)
    if blocked_rc is not None:
        return blocked_rc

    cfg = load_config()
    nlm_bin = _require_nlm_bin(cfg)
    profile = str(cfg.get("profile") or DEFAULT_PROFILE)
    env, _ = build_proxy_env(cfg)
    env.setdefault("NOTEBOOKLM_DISABLE_KEEPALIVE_POKE", "1")

    forwarded = _inject_default_login_browser(list(args), sub1)
    has_lang_flag = any(a == "--language" or a.startswith("--language=") for a in forwarded)
    if sub1 == "generate" and sub2 and has_lang_flag:
        if not _supports_generate_language(nlm_bin, sub2, env):
            forwarded = _rewrite_unsupported_language(forwarded, sub2)

    final_cmd = [str(nlm_bin), "--profile", profile, *forwarded]
    proc = subprocess.run(final_cmd, env=env, check=False)
    return proc.returncode


if __name__ == "__main__":
    sys.exit(main())
