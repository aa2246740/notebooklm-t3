#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""notebooklm-t3 跨 Provider 安装、升级、体检与 WAL 指纹卸载器。

支持模式：
  --probe [--json]            只读环境探测（脱敏浏览器候选邮箱，对 $HOME 零写入）
  --plan [--json] [opts]      预检沙盒写权限、冲突与依赖，输出完整预写变更计划（对 $HOME 零写入）
  --apply [opts]              按 WAL Manifest 幂等执行安装（支持 --non-interactive、--adopt-legacy、--link-from）
  --rollback [--json]         按 WAL Manifest 回滚中断的半途安装
  --doctor [--json]           全项健康体检（版本、socksio、代理/PAC、连通性、认证、新增 Antigravity 实例、保活）
  --update --tag <tag> [opts] 从校验后的 Release tarball 更新受管技能并清理过期文件
  --uninstall [opts]          严格按 Manifest 指纹逆向卸载，保留用户手改项与默认认证态
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import plistlib
import shutil
import socket
import stat
import subprocess
import sys
import tarfile
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILL_SRC_DEFAULT = REPO_ROOT / "skill" / "notebooklm-t3"
COMMON_DIR = SKILL_SRC_DEFAULT / "scripts"
if str(COMMON_DIR) not in sys.path:
    sys.path.insert(0, str(COMMON_DIR))

from _common import (  # noqa: E402
    DEFAULT_PROFILE,
    ENGINE_SPEC,
    MARKER_FILENAME,
    SKILL_NAME,
    atomic_write_bytes,
    atomic_write_json,
    build_proxy_env,
    get_config_dir,
    get_config_path,
    get_data_dir,
    get_home,
    get_manifest_path,
    get_state_dir,
    is_supported_engine_version,
    load_config,
    load_json_file,
    mask_email,
    parse_semver,
    read_auth_status,
    resolve_symlink_target_path,
    resolve_uv_bin,
    sha256_bytes,
    sha256_json,
    utc_now_iso,
)

GITHUB_OWNER = "aa2246740"
GITHUB_REPO = "notebooklm-t3"
LAUNCHD_LABEL = f"io.github.{GITHUB_OWNER}.{GITHUB_REPO}.keepalive"
SYSTEMD_UNIT = f"{SKILL_NAME}-keepalive"
KEEPALIVE_INTERVAL_SECONDS = 20 * 60


def get_version(src_root: Path = REPO_ROOT) -> str:
    vf = src_root / "VERSION"
    if vf.is_file():
        return vf.read_text(encoding="utf-8").strip() or "0.1.0"
    return "0.1.0"


def get_canonical_skill_dir() -> Path:
    return get_home() / ".agents" / "skills" / SKILL_NAME


def get_src_cache_dir() -> Path:
    return get_data_dir() / "src"


def _is_macos() -> bool:
    return platform.system() == "Darwin"


def _is_linux() -> bool:
    return platform.system() == "Linux"


def _is_windows() -> bool:
    return platform.system() == "Windows"


def _detect_uv_paths() -> dict[str, Any]:
    uv_bin = resolve_uv_bin()
    tool_dir: Path | None = None
    tool_bin_dir: Path | None = None
    if uv_bin:
        for flag, attr in ((["tool", "dir"], "tool"), (["tool", "dir", "--bin"], "bin")):
            try:
                p = subprocess.run(
                    [str(uv_bin), *flag],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=False,
                )
                if p.returncode == 0 and p.stdout.strip():
                    resolved = Path(p.stdout.strip()).expanduser()
                    if attr == "tool":
                        tool_dir = resolved
                    else:
                        tool_bin_dir = resolved
            except (OSError, subprocess.SubprocessError):
                pass
    return {
        "uv_bin": str(uv_bin) if uv_bin else None,
        "tool_dir": str(tool_dir) if tool_dir else None,
        "tool_bin_dir": str(tool_bin_dir) if tool_bin_dir else None,
    }


def _detect_engine(uv_info: dict[str, Any]) -> dict[str, Any]:
    tool_bin = Path(uv_info["tool_bin_dir"]) if uv_info.get("tool_bin_dir") else None
    tool_dir = Path(uv_info["tool_dir"]) if uv_info.get("tool_dir") else None

    nlm_bin: Path | None = None
    nlm_mcp_bin: Path | None = None
    if tool_bin:
        cand = tool_bin / "notebooklm"
        if cand.is_file() and os.access(cand, os.X_OK):
            nlm_bin = cand
        cand_mcp = tool_bin / "notebooklm-mcp"
        if cand_mcp.is_file() and os.access(cand_mcp, os.X_OK):
            nlm_mcp_bin = cand_mcp

    if nlm_bin is None:
        w = shutil.which("notebooklm")
        if w:
            nlm_bin = Path(w)
    if nlm_mcp_bin is None:
        w = shutil.which("notebooklm-mcp")
        if w:
            nlm_mcp_bin = Path(w)

    version_str = None
    version_tuple = None
    if nlm_bin:
        try:
            p = subprocess.run(
                [str(nlm_bin), "--version"],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
            if p.returncode == 0:
                version_str = (p.stdout or p.stderr).strip()
                version_tuple = parse_semver(version_str)
        except (OSError, subprocess.SubprocessError):
            pass

    # 在引擎专属 uv tool Python 环境中检查 socksio（不在 installer 自身解释器里查）
    engine_python: Path | None = None
    has_socksio: bool | None = None
    if tool_dir:
        cand_py = tool_dir / "notebooklm-py" / "bin" / "python"
        if cand_py.is_file() and os.access(cand_py, os.X_OK):
            engine_python = cand_py
    if engine_python is None and nlm_bin:
        try:
            first_line = nlm_bin.read_text(encoding="utf-8", errors="ignore").splitlines()[0]
            if first_line.startswith("#!"):
                shebang_py = Path(first_line[2:].strip().split()[0])
                if shebang_py.is_file() and os.access(shebang_py, os.X_OK):
                    engine_python = shebang_py
        except (OSError, IndexError):
            pass

    if engine_python:
        try:
            p = subprocess.run(
                [str(engine_python), "-c", "import socksio"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            has_socksio = p.returncode == 0
        except (OSError, subprocess.SubprocessError):
            has_socksio = False

    return {
        "installed": nlm_bin is not None,
        "nlm_bin": str(nlm_bin) if nlm_bin else None,
        "nlm_mcp_bin": str(nlm_mcp_bin) if nlm_mcp_bin else None,
        "engine_python": str(engine_python) if engine_python else None,
        "version_raw": version_str,
        "version_semver": ".".join(str(x) for x in version_tuple) if version_tuple else None,
        "version_supported": is_supported_engine_version(version_tuple),
        "has_socksio": has_socksio,
        "missing_extras": ["socksio"] if (nlm_bin is not None and has_socksio is False) else [],
    }


def _detect_browser_profile_candidates() -> list[dict[str, Any]]:
    """只读解析 Chromium 系浏览器 Local State（绝不触发 Keychain 弹窗），邮箱脱敏后返回。"""
    home = get_home()
    roots: list[tuple[str, Path]] = []
    if _is_macos():
        app_sup = home / "Library" / "Application Support"
        roots = [
            ("chrome", app_sup / "Google" / "Chrome"),
            ("brave", app_sup / "BraveSoftware" / "Brave-Browser"),
            ("edge", app_sup / "Microsoft Edge"),
            ("chromium", app_sup / "Chromium"),
        ]
    elif _is_linux():
        cfg = home / ".config"
        roots = [
            ("chrome", cfg / "google-chrome"),
            ("brave", cfg / "BraveSoftware" / "Brave-Browser"),
            ("edge", cfg / "microsoft-edge"),
            ("chromium", cfg / "chromium"),
        ]

    candidates: list[dict[str, Any]] = []
    for browser_name, root in roots:
        local_state = root / "Local State"
        if not local_state.is_file():
            continue
        data = load_json_file(local_state, default=None)
        if not isinstance(data, dict):
            continue
        info_cache = ((data.get("profile") or {}).get("info_cache")) or {}
        if not isinstance(info_cache, dict):
            continue
        for profile_dir, meta in sorted(info_cache.items()):
            if not isinstance(meta, dict):
                continue
            name = str(meta.get("name") or profile_dir)
            raw_email = str(meta.get("user_name") or "").strip()
            candidates.append(
                {
                    "browser": browser_name,
                    "profile_dir": profile_dir,
                    "browser_profile": f"{browser_name}::{profile_dir}",
                    "display_name": name,
                    "sync_email_masked": mask_email(raw_email) if raw_email else None,
                    "source": "browser_local_state_candidate",
                    "note": "Chrome sync account hint only; actual NotebookLM account is verified during foreground setup-auth.",
                }
            )
    return candidates


def _detect_pac_proxy() -> bool:
    if not _is_macos():
        return False
    try:
        p = subprocess.run(["scutil", "--proxy"], capture_output=True, text=True, timeout=5, check=False)
        if p.returncode == 0:
            for line in p.stdout.splitlines():
                if "ProxyAutoConfigEnable" in line and ": 1" in line:
                    return True
    except (OSError, subprocess.SubprocessError):
        pass
    return False


def _check_google_connectivity(env: dict[str, str], timeout: float = 3.0) -> dict[str, Any]:
    """实测 accounts.google.com 连通性（3 秒超时），用于国内网络与 Clash TUN 核验。"""
    proxy_map: dict[str, str] = {}
    https_p = env.get("HTTPS_PROXY") or env.get("https_proxy") or ""
    http_p = env.get("HTTP_PROXY") or env.get("http_proxy") or ""
    if https_p:
        proxy_map["https"] = https_p
    if http_p:
        proxy_map["http"] = http_p

    # urllib 无法直接处理 socks5 代理；检测到 socks 代理时改用引擎 python/httpx 或给出明确状态
    if https_p.lower().startswith(("socks5://", "socks5h://", "socks://")):
        return {
            "reachable": None,
            "checked_via": "socks_proxy_deferred_to_engine",
            "proxy": https_p,
        }

    opener = urllib.request.build_opener(urllib.request.ProxyHandler(proxy_map))
    req = urllib.request.Request("https://accounts.google.com/generate_204", method="HEAD")
    try:
        with opener.open(req, timeout=timeout) as resp:
            return {"reachable": True, "status": resp.status, "checked_via": "https_head"}
    except urllib.error.HTTPError as exc:
        # 204/302/404 均说明 TLS 与服务端可达
        return {"reachable": True, "status": exc.code, "checked_via": "https_head"}
    except (urllib.error.URLError, socket.timeout, OSError) as exc:
        return {"reachable": False, "error": str(exc)[:200], "checked_via": "https_head"}


def _discover_t3_and_providers() -> dict[str, Any]:
    """按 settings.json 与常规已存在目录发现 Provider 技能目录和 Antigravity MCP 配置。"""
    home = get_home()
    settings_path = home / ".t3" / "userdata" / "settings.json"
    settings = load_json_file(settings_path, default={})
    if not isinstance(settings, dict):
        settings = {}
    instances = settings.get("providerInstances") or {}
    if not isinstance(instances, dict):
        instances = {}

    consumers: list[dict[str, Any]] = []
    antigravity_mcp_configs: list[Path] = []
    antigravity_enabled = False
    cursor_enabled = False

    for inst_id, inst in sorted(instances.items()):
        if not isinstance(inst, dict) or not inst.get("enabled"):
            continue
        driver = str(inst.get("driver") or "")
        cfg = inst.get("config") if isinstance(inst.get("config"), dict) else {}

        if driver == "claudeAgent":
            raw_home = str(cfg.get("homePath") or "").strip()
            base_dir = Path(raw_home).expanduser() if raw_home else (home / ".claude")
            if base_dir.exists():
                consumers.append(
                    {
                        "consumer": f"claude:{inst_id}",
                        "provider": "claude",
                        "skills_dir": str(base_dir / "skills"),
                        "source": "t3_settings",
                    }
                )
        elif driver == "antigravity":
            antigravity_enabled = True
        elif driver == "acpRegistry" and "cursor" in inst_id.lower():
            cursor_enabled = True

    # 只要有一个启用的 antigravity 实例，或本机已存在 providers/antigravity/*/config，就扫描所有实例目录
    agy_root = home / ".t3" / "userdata" / "providers" / "antigravity"
    if agy_root.is_dir():
        for child in sorted(agy_root.iterdir()):
            cfg_dir = child / "config"
            if not cfg_dir.is_dir():
                continue
            if antigravity_enabled or not settings_path.is_file():
                consumers.append(
                    {
                        "consumer": f"antigravity:{child.name}",
                        "provider": "antigravity",
                        "skills_dir": str(cfg_dir / "skills"),
                        "source": "t3_antigravity_instance",
                    }
                )
                antigravity_mcp_configs.append(cfg_dir / "mcp_config.json")

    # 常规已存在的 Provider 根目录（绝不创建不存在的根目录）
    conventional = [
        ("codex", "codex", home / ".codex", home / ".codex" / "skills"),
        ("claude:default", "claude", home / ".claude", home / ".claude" / "skills"),
        ("grok", "grok", home / ".grok", home / ".grok" / "skills"),
        ("pi", "pi", home / ".pi" / "agent", home / ".pi" / "agent" / "skills"),
        ("gemini:optional", "gemini", home / ".gemini" / "config", home / ".gemini" / "config" / "skills"),
    ]
    existing_consumer_names = {c["consumer"] for c in consumers}
    for cname, prov, root_dir, skills_dir in conventional:
        if cname in existing_consumer_names:
            continue
        if root_dir.exists():
            consumers.append(
                {
                    "consumer": cname,
                    "provider": prov,
                    "skills_dir": str(skills_dir),
                    "source": "conventional_existing_dir",
                }
            )

    return {
        "t3_settings_exists": settings_path.is_file(),
        "antigravity_enabled": antigravity_enabled,
        "cursor_enabled": cursor_enabled,
        "consumers": consumers,
        "antigravity_mcp_configs": [str(p) for p in antigravity_mcp_configs],
    }


def _dedup_skill_targets(
    consumers: list[dict[str, Any]],
    *,
    skip_codex_symlink: bool = False,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """先按 consumer 归并再按 realpath 去重；处理 Codex 跳过与共享目标冲突。"""
    canonical_parent_real = os.path.realpath(get_canonical_skill_dir().parent)
    grouped: dict[str, list[dict[str, Any]]] = {}

    for item in consumers:
        skills_dir = Path(item["skills_dir"])
        real_dir = os.path.realpath(skills_dir)
        # 如果该 Provider 真实路径就是 ~/.agents/skills 本身，无需再建自指软链
        if real_dir == canonical_parent_real:
            continue
        grouped.setdefault(real_dir, []).append(item)

    targets: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []

    for real_dir, group in sorted(grouped.items()):
        providers = {g["provider"] for g in group}
        consumer_names = [g["consumer"] for g in group]
        if skip_codex_symlink and "codex" in providers:
            if providers == {"codex"}:
                continue
            conflicts.append(
                {
                    "code": "shared_target_conflict",
                    "real_skills_dir": real_dir,
                    "consumers": consumer_names,
                    "message": (
                        "Codex symlink skip requested, but another enabled consumer resolves to "
                        f"the exact same directory ({real_dir}). Confirm whether to link or skip."
                    ),
                }
            )
            continue

        # 以组内第一条原始路径作为展示，写入真实解析后的目标目录
        link_path = Path(real_dir) / SKILL_NAME
        targets.append(
            {
                "link_path": str(link_path),
                "real_skills_dir": real_dir,
                "consumers": consumer_names,
            }
        )

    return targets, conflicts


def _is_writable_target(path: Path) -> bool:
    """无写入副作用的写权限预检：向上查找最近已存在父目录并核验 W_OK。"""
    probe = Path(os.path.realpath(path)) if path.is_symlink() else path
    if probe.exists():
        if probe.is_dir():
            return os.access(probe, os.W_OK | os.X_OK)
        return os.access(probe, os.W_OK) and os.access(probe.parent, os.W_OK | os.X_OK)
    cur = probe.parent
    while not cur.exists() and cur != cur.parent:
        cur = cur.parent
    return cur.exists() and os.access(cur, os.W_OK | os.X_OK)


def _validate_allowed_roots(roots_str: str) -> tuple[str | None, str | None]:
    """校验 NOTEBOOKLM_MCP_ALLOWED_ROOTS：禁止单独设成 $HOME 或 ~/.notebooklm。"""
    if not roots_str.strip():
        return None, None
    home_real = os.path.realpath(get_home())
    nlm_home_real = os.path.realpath(get_home() / ".notebooklm")
    cleaned: list[str] = []
    for raw in roots_str.split(os.pathsep):
        part = raw.strip()
        if not part:
            continue
        p = Path(part).expanduser()
        rp = os.path.realpath(p)
        if rp == home_real:
            return None, f"allowed_roots cannot be HOME itself ({home_real}); use a workspace subdirectory."
        if rp == nlm_home_real or rp.startswith(nlm_home_real + os.sep):
            return None, f"allowed_roots cannot include ~/.notebooklm ({nlm_home_real})."
        cleaned.append(rp)
    return os.pathsep.join(cleaned), None


def _cmd_probe(args: argparse.Namespace) -> int:
    cfg = load_config()
    uv_info = _detect_uv_paths()
    engine = _detect_engine(uv_info)
    env, proxy_meta = build_proxy_env(cfg)
    providers = _discover_t3_and_providers()
    browsers = _detect_browser_profile_candidates()
    canonical = get_canonical_skill_dir()
    marker = load_json_file(canonical / MARKER_FILENAME, default=None)

    result = {
        "ok": True,
        "version": get_version(),
        "os": platform.system(),
        "os_supported": _is_macos() or _is_linux(),
        "windows_unsupported_note": (
            "Windows native is unsupported in v1 due to Chrome App-Bound Encryption."
            if _is_windows()
            else None
        ),
        "uv": uv_info,
        "pdftoppm_available": shutil.which("pdftoppm") is not None,
        "engine": engine,
        "proxy": {
            **proxy_meta,
            "pac_detected": _detect_pac_proxy(),
        },
        "canonical_skill": {
            "path": str(canonical),
            "exists": canonical.exists() or canonical.is_symlink(),
            "is_symlink": canonical.is_symlink(),
            "managed": isinstance(marker, dict),
            "managed_version": marker.get("version") if isinstance(marker, dict) else None,
        },
        "existing_config": {
            "exists": get_config_path().is_file(),
            "account_email_masked": mask_email(cfg.get("account_email")),
            "browser_profile": cfg.get("browser_profile"),
        },
        "auth_status": read_auth_status(),
        "providers": providers,
        "browser_candidates": browsers,
    }
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"OS: {result['os']} (supported={result['os_supported']})")
        print(f"uv: {uv_info['uv_bin'] or 'MISSING'}")
        print(f"engine: {engine['version_raw'] or 'NOT INSTALLED'} (socksio={engine['has_socksio']})")
        print(f"pdftoppm: {'yes' if result['pdftoppm_available'] else 'no (optional, for PPT preview)'}")
        print(f"proxy: {proxy_meta['source']} (socks={proxy_meta['has_socks']})")
        print(f"browser candidates: {len(browsers)}")
    return 0


def _build_plan(args: argparse.Namespace) -> dict[str, Any]:
    """构造完整预写计划（零磁盘写入），包含沙盒权限预检与冲突检查。"""
    if _is_windows():
        return {
            "status": "unsupported_os",
            "message": "Windows native is unsupported in v1 due to Chrome App-Bound Encryption.",
        }

    cfg = load_config()
    uv_info = _detect_uv_paths()
    if not uv_info.get("uv_bin"):
        return {
            "status": "missing_prerequisite",
            "prerequisite": "uv",
            "message": "uv is required. Install uv first: https://docs.astral.sh/uv/getting-started/installation/",
        }

    engine = _detect_engine(uv_info)
    effective_cfg = dict(cfg)
    if args.proxy:
        effective_cfg["proxy"] = args.proxy
    if args.no_proxy:
        effective_cfg["no_proxy"] = args.no_proxy
    env, proxy_meta = build_proxy_env(effective_cfg)

    allowed_roots_val, roots_err = _validate_allowed_roots(args.allowed_roots or "")
    if roots_err:
        return {"status": "invalid_argument", "message": roots_err}

    providers = _discover_t3_and_providers()
    symlink_targets, shared_conflicts = _dedup_skill_targets(
        providers["consumers"],
        skip_codex_symlink=bool(args.skip_codex_symlink),
    )

    canonical = get_canonical_skill_dir()
    marker_path = canonical / MARKER_FILENAME
    has_marker = marker_path.is_file()
    existing_manifest = load_json_file(get_manifest_path(), default=None)

    conflicts: list[dict[str, Any]] = list(shared_conflicts)
    warnings: list[str] = []

    # 检查 canonical 目录冲突（无 marker 且未传 --adopt-legacy）
    if (canonical.exists() or canonical.is_symlink()) and not has_marker and not args.adopt_legacy:
        conflicts.append(
            {
                "code": "unmanaged_canonical_dir",
                "target": str(canonical),
                "message": (
                    f"{canonical} already exists without {MARKER_FILENAME}. "
                    "Re-run with --adopt-legacy to back up and adopt it."
                ),
            }
        )

    # 检查各软链冲突（已存在但既不是指向 canonical 也不在现有 manifest 中）
    canonical_real = os.path.realpath(canonical)
    for st in symlink_targets:
        lp = Path(st["link_path"])
        if lp.is_symlink():
            cur_target = os.readlink(lp)
            if os.path.realpath(lp) != canonical_real and not args.adopt_legacy:
                conflicts.append(
                    {
                        "code": "unmanaged_symlink",
                        "target": str(lp),
                        "current_target": cur_target,
                        "message": f"{lp} points to '{cur_target}' instead of {canonical}.",
                    }
                )
        elif lp.exists():
            conflicts.append(
                {
                    "code": "non_symlink_exists",
                    "target": str(lp),
                    "message": f"{lp} exists as a non-symlink file/directory.",
                }
            )

    # 引擎版本与 socksio 策略（Opus R3 规则 #1）
    engine_action = "keep"
    engine_installed_by_us = False
    if not engine["installed"]:
        engine_action = "install"
        engine_installed_by_us = True
    elif not engine["version_supported"]:
        if args.force_engine:
            engine_action = "force_reinstall"
            engine_installed_by_us = False
        else:
            conflicts.append(
                {
                    "code": "engine_version_mismatch",
                    "installed_version": engine["version_raw"],
                    "required_spec": ENGINE_SPEC,
                    "message": (
                        f"Existing notebooklm-py ({engine['version_raw']}) is outside supported range "
                        f"{ENGINE_SPEC}. Pass --force-engine to reinstall (uninstall will preserve it)."
                    ),
                }
            )
    elif engine["missing_extras"]:
        if proxy_meta["has_socks"] and not args.force_engine:
            conflicts.append(
                {
                    "code": "engine_missing_extra",
                    "missing_extras": engine["missing_extras"],
                    "message": (
                        "SOCKS proxy detected, but existing notebooklm-py venv is missing 'socksio'. "
                        "Pass --force-engine to reinstall with '--with socksio'."
                    ),
                }
            )
        elif args.force_engine:
            engine_action = "force_reinstall"
            engine_installed_by_us = False
        else:
            warnings.append(
                "Existing external notebooklm-py venv is missing optional 'socksio' (only required for SOCKS5 proxies; pass --force-engine to add it)."
            )

    # 规划目标写路径用于沙盒预检（B6）
    check_write_paths: list[Path] = [
        get_config_dir(),
        get_state_dir(),
        get_src_cache_dir(),
        canonical,
    ]
    for st in symlink_targets:
        check_write_paths.append(Path(st["link_path"]))

    mcp_targets: list[Path] = []
    if not args.skip_mcp:
        for mp in providers["antigravity_mcp_configs"]:
            p = Path(mp)
            mcp_targets.append(p)
            check_write_paths.append(p)

    keepalive_target: Path | None = None
    if args.keepalive:
        if _is_macos():
            keepalive_target = get_home() / "Library" / "LaunchAgents" / f"{LAUNCHD_LABEL}.plist"
            check_write_paths.append(keepalive_target)
        elif _is_linux():
            keepalive_target = get_home() / ".config" / "systemd" / "user" / f"{SYSTEMD_UNIT}.timer"
            check_write_paths.append(keepalive_target)

    unwritable = [str(p) for p in check_write_paths if not _is_writable_target(p)]
    if unwritable:
        return {
            "status": "needs_escalation",
            "reason": "One or more target paths outside workspace are not writable in the current sandbox.",
            "targets": unwritable,
        }

    if conflicts:
        return {
            "status": "conflict",
            "conflicts": conflicts,
            "warnings": warnings,
        }

    # 构建完整 WAL 操作计划
    operations: list[dict[str, Any]] = []
    if engine_action in ("install", "force_reinstall"):
        operations.append(
            {
                "op": "engine_install",
                "action": engine_action,
                "spec": ENGINE_SPEC,
                "with_extras": ["socksio"],
                "engine_installed_by_us": engine_installed_by_us,
            }
        )

    if not args.link_from:
        operations.append(
            {
                "op": "copy_src_cache",
                "source": str(REPO_ROOT),
                "target": str(get_src_cache_dir()),
            }
        )

    if args.adopt_legacy and (canonical.exists() or canonical.is_symlink()) and not has_marker:
        operations.append(
            {
                "op": "backup_legacy_skill",
                "target": str(canonical),
            }
        )

    if args.link_from:
        operations.append(
            {
                "op": "link_canonical_skill",
                "source": str(Path(args.link_from).expanduser().resolve() / "skill" / SKILL_NAME),
                "target": str(canonical),
            }
        )
    else:
        operations.append(
            {
                "op": "copy_canonical_skill",
                "source": str(SKILL_SRC_DEFAULT),
                "target": str(canonical),
                "marker": str(marker_path),
            }
        )

    for st in symlink_targets:
        operations.append(
            {
                "op": "symlink_provider_skill",
                "link_path": st["link_path"],
                "target": str(canonical),
                "consumers": st["consumers"],
            }
        )

    operations.append(
        {
            "op": "write_config",
            "target": str(get_config_path()),
            "mode": "0600",
        }
    )

    for mp in mcp_targets:
        operations.append(
            {
                "op": "merge_antigravity_mcp",
                "target": str(resolve_symlink_target_path(mp)),
                "allowed_roots": allowed_roots_val,
            }
        )

    for legacy_label in args.bootout_legacy_label or []:
        operations.append(
            {
                "op": "bootout_legacy_launchd",
                "label": legacy_label,
            }
        )

    if args.keepalive and keepalive_target is not None:
        operations.append(
            {
                "op": "install_keepalive",
                "platform": platform.system().lower(),
                "target": str(keepalive_target),
                "interval_seconds": KEEPALIVE_INTERVAL_SECONDS,
                "browser_fallback": bool(args.keepalive_browser_fallback),
            }
        )

    if not args.skip_warmup:
        operations.append({"op": "prewarm_uv_scripts"})

    if providers["cursor_enabled"]:
        warnings.append("Cursor discovers ~/.agents/skills/notebooklm-t3 natively; no separate Cursor symlink needed.")
    if _is_linux() and args.keepalive:
        warnings.append("On Linux systemd user timers, run 'loginctl enable-linger $USER' if you want keepalive while logged out.")
    if engine["missing_extras"] and engine_action == "keep":
        pass

    return {
        "status": "ready",
        "version": get_version(),
        "uv_bin": uv_info["uv_bin"],
        "engine_missing_extra": engine["missing_extras"],
        "engine_installed_by_us": (
            existing_manifest.get("engine_installed_by_us", engine_installed_by_us)
            if isinstance(existing_manifest, dict) and engine_action == "keep"
            else engine_installed_by_us
        ),
        "allowed_roots": allowed_roots_val,
        "proxy": proxy_meta,
        "operations": operations,
        "warnings": warnings,
        "requires_new_session": True,
    }


def _cmd_plan(args: argparse.Namespace) -> int:
    plan = _build_plan(args)
    if args.json:
        print(json.dumps(plan, ensure_ascii=False, indent=2))
    else:
        print(f"Plan status: {plan['status']}")
        for op in plan.get("operations", []):
            print(f"  - {op['op']}: {op.get('target') or op.get('link_path') or op.get('spec') or ''}")
        for c in plan.get("conflicts", []):
            print(f"  CONFLICT [{c['code']}]: {c['message']}")
        for w in plan.get("warnings", []):
            print(f"  WARN: {w}")
    status = plan["status"]
    if status == "ready":
        return 0
    if status == "needs_escalation":
        return 4
    return 1


def _hash_tree(root: Path) -> dict[str, str]:
    files: dict[str, str] = {}
    for p in sorted(root.rglob("*")):
        if p.is_file() and p.name != MARKER_FILENAME and "__pycache__" not in p.parts:
            rel = str(p.relative_to(root))
            files[rel] = sha256_bytes(p.read_bytes())
    return files


def _copy_skill_tree(src: Path, dst: Path, version: str, tag: str | None = None) -> dict[str, Any]:
    """将纯净技能目录复制到目标位置，写入受管 marker，并按旧清单移除过期文件。"""
    old_marker = load_json_file(dst / MARKER_FILENAME, default=None)
    old_files = set((old_marker or {}).get("files", {}).keys()) if isinstance(old_marker, dict) else set()

    if dst.is_symlink():
        dst.unlink()
    dst.mkdir(parents=True, exist_ok=True)

    new_files: dict[str, str] = {}
    for item in sorted(src.rglob("*")):
        if "__pycache__" in item.parts or item.name == MARKER_FILENAME:
            continue
        rel = item.relative_to(src)
        target = dst / rel
        if item.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        elif item.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            data = item.read_bytes()
            mode = stat.S_IMODE(item.stat().st_mode)
            atomic_write_bytes(target, data, mode=mode)
            new_files[str(rel)] = sha256_bytes(data)

    # 清理旧版本多出来的文件
    for obsolete_rel in sorted(old_files - set(new_files.keys())):
        obs = dst / obsolete_rel
        if obs.is_file() or obs.is_symlink():
            obs.unlink(missing_ok=True)

    marker_payload = {
        "managed_by": SKILL_NAME,
        "version": version,
        "tag": tag or f"v{version}",
        "installed_at": utc_now_iso(),
        "files": new_files,
    }
    atomic_write_json(dst / MARKER_FILENAME, marker_payload, mode=0o644)
    return marker_payload


def _write_manifest(manifest: dict[str, Any]) -> None:
    atomic_write_json(get_manifest_path(), manifest, mode=0o600)


def _rollback_manifest(manifest: dict[str, Any], *, purge_auth: bool = False, uninstall_engine: bool = False) -> dict[str, Any]:
    """按 WAL Manifest 逆序安全回滚/卸载，严格执行指纹与 readlink 校验。"""
    removed: list[dict[str, Any]] = []
    restored: list[dict[str, Any]] = []
    skipped_modified: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    entries = list(manifest.get("entries") or [])
    for entry in reversed(entries):
        op = entry.get("op")
        target_str = entry.get("target") or ""
        target = Path(target_str) if target_str else None
        try:
            if op == "install_keepalive":
                plat = entry.get("platform")
                if plat == "darwin":
                    label = entry.get("label") or LAUNCHD_LABEL
                    if not os.environ.get("NOTEBOOKLM_T3_SKIP_SERVICE_CTL"):
                        subprocess.run(
                            ["launchctl", "bootout", f"gui/{os.getuid()}/{label}"],
                            capture_output=True,
                            check=False,
                        )
                    if target and target.is_file():
                        if sha256_bytes(target.read_bytes()) == entry.get("written_sha256"):
                            target.unlink(missing_ok=True)
                            removed.append({"op": op, "target": str(target)})
                        else:
                            skipped_modified.append({"op": op, "target": str(target), "reason": "fingerprint_mismatch"})
                elif plat == "linux":
                    if not os.environ.get("NOTEBOOKLM_T3_SKIP_SERVICE_CTL"):
                        subprocess.run(
                            ["systemctl", "--user", "disable", "--now", f"{SYSTEMD_UNIT}.timer"],
                            capture_output=True,
                            check=False,
                        )
                    for unit_key in ("timer_path", "service_path"):
                        up = Path(entry.get(unit_key) or "")
                        if up.is_file():
                            expected_hash = entry.get(f"{unit_key}_sha256")
                            if expected_hash and sha256_bytes(up.read_bytes()) != expected_hash:
                                skipped_modified.append({"op": op, "target": str(up), "reason": "fingerprint_mismatch"})
                            else:
                                up.unlink(missing_ok=True)
                                removed.append({"op": op, "target": str(up)})

            elif op == "merge_antigravity_mcp" and target:
                if not target.is_file():
                    continue
                cur = load_json_file(target, default=None)
                if not isinstance(cur, dict):
                    continue
                servers = cur.get("mcpServers")
                if not isinstance(servers, dict) or "notebooklm" not in servers:
                    continue
                cur_fp = sha256_json(servers["notebooklm"])
                if cur_fp != entry.get("written_sha256"):
                    skipped_modified.append(
                        {"op": op, "target": str(target), "reason": "mcp_notebooklm_entry_modified_by_user"}
                    )
                    continue
                if entry.get("existed_before"):
                    servers["notebooklm"] = entry.get("previous_value")
                    atomic_write_json(target, cur)
                    restored.append({"op": op, "target": str(target)})
                else:
                    del servers["notebooklm"]
                    if not entry.get("file_existed_before") and not servers:
                        target.unlink(missing_ok=True)
                        removed.append({"op": op, "target": str(target)})
                    else:
                        atomic_write_json(target, cur)
                        removed.append({"op": op, "target": f"{target}#mcpServers.notebooklm"})

            elif op == "symlink_provider_skill":
                lp = Path(entry.get("link_path") or "")
                if lp.is_symlink():
                    cur_link = os.readlink(lp)
                    if cur_link == entry.get("written_target"):
                        lp.unlink(missing_ok=True)
                        if entry.get("existed_before") and entry.get("previous_target"):
                            lp.symlink_to(entry["previous_target"])
                            restored.append({"op": op, "target": str(lp)})
                        else:
                            removed.append({"op": op, "target": str(lp)})
                    else:
                        skipped_modified.append(
                            {"op": op, "target": str(lp), "reason": f"symlink_repointed_to_{cur_link}"}
                        )

            elif op == "link_canonical_skill" and target:
                if target.is_symlink() and os.readlink(target) == entry.get("written_target"):
                    target.unlink(missing_ok=True)
                    removed.append({"op": op, "target": str(target)})

            elif op == "copy_canonical_skill" and target:
                if target.is_dir() and (target / MARKER_FILENAME).is_file():
                    shutil.rmtree(target)
                    removed.append({"op": op, "target": str(target)})
                elif target.exists():
                    skipped_modified.append({"op": op, "target": str(target), "reason": "missing_managed_marker"})

            elif op == "backup_legacy_skill":
                orig = Path(entry.get("target") or "")
                if entry.get("was_symlink") and entry.get("previous_target") and not orig.exists() and not orig.is_symlink():
                    orig.parent.mkdir(parents=True, exist_ok=True)
                    orig.symlink_to(entry["previous_target"])
                    restored.append({"op": op, "target": str(orig)})
                else:
                    backup_dir = Path(entry.get("backup_path") or "")
                    if backup_dir.exists() and not orig.exists() and not orig.is_symlink():
                        shutil.move(str(backup_dir), str(orig))
                        restored.append({"op": op, "target": str(orig)})

            elif op == "copy_src_cache" and target:
                if target.is_dir() and (target / MARKER_FILENAME).is_file():
                    shutil.rmtree(target)
                    removed.append({"op": op, "target": str(target)})

            elif op == "write_config" and target:
                if target.is_file():
                    if entry.get("existed_before") and entry.get("previous_value") is not None:
                        atomic_write_json(target, entry["previous_value"], mode=0o600)
                        restored.append({"op": op, "target": str(target)})
                    else:
                        target.unlink(missing_ok=True)
                        removed.append({"op": op, "target": str(target)})

            elif op == "engine_install":
                if uninstall_engine and entry.get("engine_installed_by_us"):
                    uv_bin = resolve_uv_bin()
                    if uv_bin:
                        subprocess.run(
                            [str(uv_bin), "tool", "uninstall", "notebooklm-py"],
                            capture_output=True,
                            check=False,
                        )
                        removed.append({"op": op, "target": "uv-tool:notebooklm-py"})
        except Exception as exc:  # noqa: BLE001
            errors.append({"op": op, "target": target_str, "error": str(exc)})

    if purge_auth:
        nlm_dir = get_home() / ".notebooklm"
        if nlm_dir.is_dir():
            shutil.rmtree(nlm_dir)
            removed.append({"op": "purge_auth", "target": str(nlm_dir)})

    # 清理 manifest 与空目录
    mp = get_manifest_path()
    if mp.exists():
        mp.unlink(missing_ok=True)
    for d in (get_config_dir(), get_state_dir()):
        try:
            if d.is_dir() and not any(d.iterdir()):
                d.rmdir()
        except OSError:
            pass

    return {
        "ok": len(errors) == 0,
        "removed": removed,
        "restored": restored,
        "skipped_modified": skipped_modified,
        "errors": errors,
        "preserved_defaults": [
            str(get_home() / ".notebooklm") + (" (purged)" if purge_auth else " (preserved)"),
            "uv binary (never uninstalled)",
            "user notebooklm-outputs/ directories (preserved)",
        ],
    }


def _install_launchd_plist(keepalive_launcher: Path, uv_bin: Path, skip_ctl: bool) -> dict[str, Any]:
    plist_path = get_home() / "Library" / "LaunchAgents" / f"{LAUNCHD_LABEL}.plist"
    log_dir = get_state_dir()
    log_dir.mkdir(parents=True, exist_ok=True)
    uv_dir = str(uv_bin.parent)
    plist_obj = {
        "Label": LAUNCHD_LABEL,
        "ProgramArguments": ["/bin/sh", str(keepalive_launcher)],
        "RunAtLoad": True,
        "StartInterval": KEEPALIVE_INTERVAL_SECONDS,
        "StandardOutPath": str(log_dir / "keepalive.launchd.out.log"),
        "StandardErrorPath": str(log_dir / "keepalive.launchd.err.log"),
        "EnvironmentVariables": {
            "HOME": str(get_home()),
            "PATH": f"{uv_dir}:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin",
        },
    }
    raw = plistlib.dumps(plist_obj)
    real_plist = atomic_write_bytes(plist_path, raw, mode=0o644)
    if not skip_ctl:
        uid = os.getuid()
        subprocess.run(["launchctl", "bootout", f"gui/{uid}/{LAUNCHD_LABEL}"], capture_output=True, check=False)
        subprocess.run(["launchctl", "bootstrap", f"gui/{uid}", str(real_plist)], capture_output=True, check=False)
    return {
        "target": str(real_plist),
        "label": LAUNCHD_LABEL,
        "written_sha256": sha256_bytes(raw),
    }


def _install_systemd_user_units(keepalive_launcher: Path, uv_bin: Path, skip_ctl: bool) -> dict[str, Any]:
    unit_dir = get_home() / ".config" / "systemd" / "user"
    unit_dir.mkdir(parents=True, exist_ok=True)
    service_path = unit_dir / f"{SYSTEMD_UNIT}.service"
    timer_path = unit_dir / f"{SYSTEMD_UNIT}.timer"

    service_content = (
        "[Unit]\n"
        "Description=NotebookLM-T3 Session Keepalive\n"
        "After=network-online.target\n\n"
        "[Service]\n"
        "Type=oneshot\n"
        f"Environment=PATH={uv_bin.parent}:/usr/local/bin:/usr/bin:/bin\n"
        f"ExecStart=/bin/sh {keepalive_launcher}\n"
    ).encode("utf-8")

    timer_content = (
        "[Unit]\n"
        "Description=Run NotebookLM-T3 Session Keepalive every 20 minutes\n\n"
        "[Timer]\n"
        "OnBootSec=5min\n"
        "OnUnitActiveSec=20min\n"
        "Persistent=true\n\n"
        "[Install]\n"
        "WantedBy=timers.target\n"
    ).encode("utf-8")

    real_service = atomic_write_bytes(service_path, service_content, mode=0o644)
    real_timer = atomic_write_bytes(timer_path, timer_content, mode=0o644)

    if not skip_ctl and shutil.which("systemctl"):
        subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True, check=False)
        subprocess.run(
            ["systemctl", "--user", "enable", "--now", f"{SYSTEMD_UNIT}.timer"],
            capture_output=True,
            check=False,
        )

    return {
        "target": str(real_timer),
        "timer_path": str(real_timer),
        "timer_path_sha256": sha256_bytes(timer_content),
        "service_path": str(real_service),
        "service_path_sha256": sha256_bytes(service_content),
    }


def _cmd_apply(args: argparse.Namespace) -> int:
    plan = _build_plan(args)
    if plan["status"] != "ready":
        if args.json:
            print(json.dumps(plan, ensure_ascii=False, indent=2))
        else:
            print(f"Cannot apply: {plan['status']} - {plan.get('message') or plan.get('conflicts')}", file=sys.stderr)
        return 4 if plan["status"] == "needs_escalation" else 1

    fail_after = int(os.environ.get("NOTEBOOKLM_T3_FAIL_AFTER_STEP") or "0")
    skip_ctl = bool(os.environ.get("NOTEBOOKLM_T3_SKIP_SERVICE_CTL"))
    existing_manifest = load_json_file(get_manifest_path(), default=None)
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "skill": SKILL_NAME,
        "version": get_version(),
        "updated_at": utc_now_iso(),
        "adopted_from_legacy": bool(args.adopt_legacy),
        "engine_installed_by_us": plan["engine_installed_by_us"],
        "entries": list(existing_manifest.get("entries", [])) if isinstance(existing_manifest, dict) else [],
    }
    _write_manifest(manifest)

    uv_bin = Path(plan["uv_bin"])
    cfg_before = load_config()
    env, _ = build_proxy_env(cfg_before)

    def _upsert_entry(new_entry: dict[str, Any]) -> None:
        key = (new_entry.get("op"), new_entry.get("target") or new_entry.get("link_path"))
        entries = manifest["entries"]
        for idx, e in enumerate(entries):
            if (e.get("op"), e.get("target") or e.get("link_path")) == key:
                # 保留首次安装时的 existed_before / previous_value，保证重复安装幂等不丢失原始快照
                if "existed_before" in e and e.get("status") == "applied":
                    new_entry["existed_before"] = e["existed_before"]
                    for pk in ("previous_value", "previous_target", "file_existed_before"):
                        if pk in e:
                            new_entry[pk] = e[pk]
                entries[idx] = new_entry
                _write_manifest(manifest)
                return
        entries.append(new_entry)
        _write_manifest(manifest)

    step_num = 0
    for op in plan["operations"]:
        step_num += 1
        kind = op["op"]

        if kind == "engine_install":
            entry = {
                "op": kind,
                "target": "uv-tool:notebooklm-py",
                "spec": op["spec"],
                "engine_installed_by_us": op["engine_installed_by_us"],
                "status": "pending",
            }
            _upsert_entry(entry)
            cmd = [str(uv_bin), "tool", "install"]
            if op["action"] == "force_reinstall":
                cmd.append("--force")
            cmd.extend(["--with", "socksio", op["spec"]])
            proc = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=300, check=False)
            if proc.returncode != 0:
                err = (proc.stderr or proc.stdout).strip()[:800]
                out = {"ok": False, "status": "engine_install_failed", "details": err}
                print(json.dumps(out, ensure_ascii=False, indent=2) if args.json else f"ERROR: {err}")
                return proc.returncode
            entry["status"] = "applied"
            _upsert_entry(entry)

        elif kind == "copy_src_cache":
            src = Path(op["source"])
            dst = Path(op["target"])
            entry = {
                "op": kind,
                "target": str(dst),
                "existed_before": dst.exists(),
                "status": "pending",
            }
            _upsert_entry(entry)
            if src.resolve() != dst.resolve():
                if dst.exists():
                    shutil.rmtree(dst)
                shutil.copytree(
                    src,
                    dst,
                    ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc", ".pytest_cache", "notebooklm-outputs"),
                )
            atomic_write_json(
                dst / MARKER_FILENAME,
                {"managed_by": SKILL_NAME, "version": get_version(), "installed_at": utc_now_iso()},
                mode=0o644,
            )
            entry["status"] = "applied"
            _upsert_entry(entry)

        elif kind == "backup_legacy_skill":
            orig = Path(op["target"])
            ts = utc_now_iso().replace(":", "").replace("-", "")
            backup_path = get_state_dir() / "backups" / f"legacy-{ts}"
            backup_path.parent.mkdir(parents=True, exist_ok=True)
            was_symlink = orig.is_symlink()
            prev_target = os.readlink(orig) if was_symlink else None
            entry = {
                "op": kind,
                "target": str(orig),
                "was_symlink": was_symlink,
                "previous_target": prev_target,
                "backup_path": str(backup_path),
                "status": "pending",
            }
            _upsert_entry(entry)
            if was_symlink:
                orig.unlink()
            elif orig.exists():
                shutil.move(str(orig), str(backup_path))
            entry["status"] = "applied"
            _upsert_entry(entry)

        elif kind == "copy_canonical_skill":
            src = Path(op["source"])
            dst = Path(op["target"])
            entry = {
                "op": kind,
                "target": str(dst),
                "existed_before": dst.exists(),
                "status": "pending",
            }
            _upsert_entry(entry)
            marker = _copy_skill_tree(src, dst, get_version(), tag=args.tag)
            entry["written_sha256"] = sha256_json(marker["files"])
            entry["status"] = "applied"
            _upsert_entry(entry)

        elif kind == "link_canonical_skill":
            src = Path(op["source"])
            dst = Path(op["target"])
            dst.parent.mkdir(parents=True, exist_ok=True)
            existed = dst.is_symlink() or dst.exists()
            prev = os.readlink(dst) if dst.is_symlink() else None
            entry = {
                "op": kind,
                "target": str(dst),
                "existed_before": existed,
                "previous_target": prev,
                "written_target": str(src),
                "status": "pending",
            }
            _upsert_entry(entry)
            if dst.is_symlink():
                dst.unlink()
            dst.symlink_to(src)
            entry["status"] = "applied"
            _upsert_entry(entry)

        elif kind == "symlink_provider_skill":
            lp = Path(op["link_path"])
            target_dir = op["target"]
            lp.parent.mkdir(parents=True, exist_ok=True)
            existed = lp.is_symlink()
            prev = os.readlink(lp) if existed else None
            entry = {
                "op": kind,
                "link_path": str(lp),
                "target": str(lp),
                "existed_before": existed,
                "previous_target": prev,
                "written_target": target_dir,
                "consumers": op["consumers"],
                "status": "pending",
            }
            _upsert_entry(entry)
            if lp.is_symlink():
                lp.unlink()
            lp.symlink_to(target_dir)
            entry["status"] = "applied"
            _upsert_entry(entry)

        elif kind == "write_config":
            uv_now = _detect_uv_paths()
            eng_now = _detect_engine(uv_now)
            cfg_path = get_config_path()
            existed = cfg_path.is_file()
            prev_cfg = load_json_file(cfg_path, default=None) if existed else None

            new_cfg = dict(prev_cfg) if isinstance(prev_cfg, dict) else {}
            new_cfg["profile"] = args.profile or new_cfg.get("profile") or DEFAULT_PROFILE
            new_cfg["uv_bin"] = uv_now["uv_bin"]
            new_cfg["nlm_bin"] = eng_now["nlm_bin"]
            new_cfg["nlm_mcp_bin"] = eng_now["nlm_mcp_bin"]
            if args.browser_profile:
                new_cfg["browser_profile"] = args.browser_profile
            if plan.get("allowed_roots") is not None:
                new_cfg["allowed_roots"] = plan["allowed_roots"]
            if args.proxy:
                new_cfg["proxy"] = args.proxy
            if args.no_proxy:
                new_cfg["no_proxy"] = args.no_proxy
            ka_cfg = dict(new_cfg.get("keepalive") or {})
            ka_cfg["interval_seconds"] = KEEPALIVE_INTERVAL_SECONDS
            ka_cfg["browser_fallback"] = bool(args.keepalive_browser_fallback)
            new_cfg["keepalive"] = ka_cfg

            entry = {
                "op": kind,
                "target": str(resolve_symlink_target_path(cfg_path)),
                "existed_before": existed,
                "previous_value": prev_cfg,
                "written_sha256": sha256_json(new_cfg),
                "status": "pending",
            }
            _upsert_entry(entry)
            atomic_write_json(cfg_path, new_cfg, mode=0o600)
            entry["status"] = "applied"
            _upsert_entry(entry)

        elif kind == "merge_antigravity_mcp":
            mcp_path = Path(op["target"])
            file_existed = mcp_path.is_file()
            cur_doc = load_json_file(mcp_path, default={}) if file_existed else {}
            if not isinstance(cur_doc, dict):
                cur_doc = {}
            servers = cur_doc.get("mcpServers")
            if not isinstance(servers, dict):
                servers = {}
                cur_doc["mcpServers"] = servers

            key_existed = "notebooklm" in servers
            prev_val = servers.get("notebooklm")

            cur_cfg = load_config()
            mcp_bin = cur_cfg.get("nlm_mcp_bin") or shutil.which("notebooklm-mcp") or "notebooklm-mcp"
            mcp_env: dict[str, str] = {
                "NOTEBOOKLM_PROFILE": str(cur_cfg.get("profile") or DEFAULT_PROFILE),
                "NOTEBOOKLM_DISABLE_KEEPALIVE_POKE": "1",
            }
            if cur_cfg.get("allowed_roots"):
                mcp_env["NOTEBOOKLM_MCP_ALLOWED_ROOTS"] = str(cur_cfg["allowed_roots"])
            if cur_cfg.get("proxy") and cur_cfg["proxy"] != "auto":
                mcp_env["HTTP_PROXY"] = str(cur_cfg["proxy"])
                mcp_env["HTTPS_PROXY"] = str(cur_cfg["proxy"])
            if cur_cfg.get("no_proxy"):
                mcp_env["NO_PROXY"] = str(cur_cfg["no_proxy"])

            new_entry_val = {
                "command": str(mcp_bin),
                "args": ["--profile", str(cur_cfg.get("profile") or DEFAULT_PROFILE)],
                "env": mcp_env,
            }
            entry = {
                "op": kind,
                "target": str(resolve_symlink_target_path(mcp_path)),
                "file_existed_before": file_existed,
                "existed_before": key_existed,
                "previous_value": prev_val,
                "written_sha256": sha256_json(new_entry_val),
                "status": "pending",
            }
            _upsert_entry(entry)
            servers["notebooklm"] = new_entry_val
            atomic_write_json(mcp_path, cur_doc)
            entry["status"] = "applied"
            _upsert_entry(entry)

        elif kind == "bootout_legacy_launchd":
            label = op["label"]
            if _is_macos() and label:
                if not skip_ctl:
                    subprocess.run(
                        ["launchctl", "bootout", f"gui/{os.getuid()}/{label}"],
                        capture_output=True,
                        check=False,
                    )
                old_plist = get_home() / "Library" / "LaunchAgents" / f"{label}.plist"
                if old_plist.is_file():
                    old_plist.unlink(missing_ok=True)

        elif kind == "install_keepalive":
            launcher = get_canonical_skill_dir() / "scripts" / "keepalive"
            entry = {
                "op": kind,
                "platform": op["platform"],
                "target": op["target"],
                "label": LAUNCHD_LABEL if op["platform"] == "darwin" else SYSTEMD_UNIT,
                "status": "pending",
            }
            _upsert_entry(entry)
            if op["platform"] == "darwin":
                meta = _install_launchd_plist(launcher, uv_bin, skip_ctl)
            else:
                meta = _install_systemd_user_units(launcher, uv_bin, skip_ctl)
            entry.update(meta)
            entry["status"] = "applied"
            _upsert_entry(entry)

        elif kind == "prewarm_uv_scripts":
            nlm_launcher = get_canonical_skill_dir() / "scripts" / "nlm"
            ka_launcher = get_canonical_skill_dir() / "scripts" / "keepalive"
            if nlm_launcher.is_file():
                subprocess.run(["/bin/sh", str(nlm_launcher), "--self-check"], env=env, capture_output=True, timeout=120, check=False)
            if ka_launcher.is_file():
                subprocess.run(["/bin/sh", str(ka_launcher), "--dry-run"], env=env, capture_output=True, timeout=120, check=False)

        if fail_after > 0 and step_num >= fail_after:
            print(f"INJECTED FAILURE after step {step_num} ({kind})", file=sys.stderr)
            return 99

    result = {
        "ok": True,
        "status": "installed",
        "version": get_version(),
        "manifest": str(get_manifest_path()),
        "config": str(get_config_path()),
        "canonical_skill": str(get_canonical_skill_dir()),
        "requires_new_session": True,
        "warnings": plan.get("warnings", []),
    }
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"Installed notebooklm-t3 v{result['version']} -> {result['canonical_skill']}")
        print("Open a new T3 Code thread to load the installed skill and MCP server.")
    return 0


def _cmd_rollback(args: argparse.Namespace) -> int:
    manifest = load_json_file(get_manifest_path(), default=None)
    if not isinstance(manifest, dict):
        out = {"ok": True, "message": "No manifest found; nothing to roll back."}
        print(json.dumps(out, ensure_ascii=False, indent=2) if args.json else out["message"])
        return 0
    report = _rollback_manifest(manifest, purge_auth=False, uninstall_engine=False)
    print(json.dumps(report, ensure_ascii=False, indent=2) if args.json else f"Rollback ok={report['ok']}")
    return 0 if report["ok"] else 1


def _cmd_uninstall(args: argparse.Namespace) -> int:
    manifest = load_json_file(get_manifest_path(), default=None)
    if not isinstance(manifest, dict):
        out = {"ok": True, "message": "No manifest found; notebooklm-t3 is not installed via this installer."}
        print(json.dumps(out, ensure_ascii=False, indent=2) if args.json else out["message"])
        return 0
    report = _rollback_manifest(
        manifest,
        purge_auth=bool(args.purge_auth),
        uninstall_engine=bool(args.uninstall_engine),
    )
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(
            f"Uninstall ok={report['ok']}: removed={len(report['removed'])}, "
            f"restored={len(report['restored'])}, skipped_modified={len(report['skipped_modified'])}"
        )
        for item in report["skipped_modified"]:
            print(f"  PRESERVED (modified): {item['target']} ({item['reason']})")
    return 0 if report["ok"] else 1


def _cmd_doctor(args: argparse.Namespace) -> int:
    cfg = load_config()
    uv_info = _detect_uv_paths()
    engine = _detect_engine(uv_info)
    env, proxy_meta = build_proxy_env(cfg)
    providers = _discover_t3_and_providers()
    canonical = get_canonical_skill_dir()
    canonical_real = os.path.realpath(canonical)

    issues: list[str] = []
    warnings: list[str] = []

    if not uv_info.get("uv_bin"):
        issues.append("uv is missing")
    if not engine["installed"]:
        issues.append("notebooklm-py is not installed")
    elif not engine["version_supported"]:
        issues.append(f"notebooklm-py version {engine['version_raw']} outside supported range {ENGINE_SPEC}")

    pac_detected = _detect_pac_proxy()
    if pac_detected:
        warnings.append("macOS PAC auto-proxy detected; Python getproxies() cannot evaluate PAC scripts. Set explicit proxy in config.json if needed.")
    if proxy_meta["has_socks"] and engine["has_socksio"] is False:
        issues.append("SOCKS proxy is active but engine venv is missing 'socksio' (re-run installer with --force-engine).")

    connectivity = (
        {"reachable": None, "checked_via": "skipped_offline"}
        if args.offline
        else _check_google_connectivity(env, timeout=3.0)
    )
    if connectivity.get("reachable") is False:
        issues.append(f"Cannot reach accounts.google.com within 3s: {connectivity.get('error')}")

    # 检查现有 Antigravity 实例目录是否漏挂 Skill 或 MCP（含安装后新增实例）
    missing_agy_skills: list[str] = []
    missing_agy_mcp: list[str] = []
    for mp in providers["antigravity_mcp_configs"]:
        mcp_p = Path(mp)
        cfg_dir = mcp_p.parent
        sk_link = cfg_dir / "skills" / SKILL_NAME
        if not sk_link.exists() or os.path.realpath(sk_link) != canonical_real:
            missing_agy_skills.append(str(sk_link))
        mcp_doc = load_json_file(mcp_p, default={})
        if not isinstance(mcp_doc, dict) or "notebooklm" not in (mcp_doc.get("mcpServers") or {}):
            missing_agy_mcp.append(str(mcp_p))

    if missing_agy_skills:
        warnings.append(f"Antigravity instance(s) missing skill symlink: {missing_agy_skills}")
    if missing_agy_mcp:
        warnings.append(f"Antigravity instance(s) missing mcpServers.notebooklm: {missing_agy_mcp}")

    auth_live_ok: bool | None = None
    if not args.offline and engine["nlm_bin"]:
        profile = str(cfg.get("profile") or DEFAULT_PROFILE)
        chk_env = dict(env)
        chk_env["NOTEBOOKLM_DISABLE_KEEPALIVE_POKE"] = "1"
        try:
            p = subprocess.run(
                [engine["nlm_bin"], "--profile", profile, "auth", "check", "--test", "--passive"],
                env=chk_env,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            auth_live_ok = p.returncode == 0
            if not auth_live_ok:
                warnings.append("NotebookLM authentication is not currently valid; run 'nlm setup-auth' or 'nlm recover'.")
        except subprocess.TimeoutExpired:
            auth_live_ok = False
            warnings.append("auth check timed out after 30s.")

    pdftoppm_ok = shutil.which("pdftoppm") is not None
    if not pdftoppm_ok:
        warnings.append("pdftoppm (poppler) not found; PPT inline slide preview requires poppler.")

    result = {
        "ok": len(issues) == 0,
        "version": get_version(),
        "uv": uv_info,
        "engine": engine,
        "proxy": {**proxy_meta, "pac_detected": pac_detected},
        "connectivity": connectivity,
        "auth_status": read_auth_status(),
        "auth_live_ok": auth_live_ok,
        "canonical_skill_exists": canonical.exists(),
        "missing_antigravity_skills": missing_agy_skills,
        "missing_antigravity_mcp": missing_agy_mcp,
        "pdftoppm_available": pdftoppm_ok,
        "account_email_masked": mask_email(cfg.get("account_email")),
        "requires_new_session": True,
        "issues": issues,
        "warnings": warnings,
    }
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"Doctor ok={result['ok']} (issues={len(issues)}, warnings={len(warnings)})")
        for x in issues:
            print(f"  ISSUE: {x}")
        for w in warnings:
            print(f"  WARN: {w}")
    return 0 if result["ok"] else 1


def _verify_sha256(tarball: Path, checksums_file: Path) -> bool:
    expected: str | None = None
    for line in checksums_file.read_text(encoding="utf-8").splitlines():
        parts = line.strip().split()
        if len(parts) >= 2 and Path(parts[-1].lstrip("*")).name == tarball.name:
            expected = parts[0].lower()
            break
    if not expected:
        return False
    actual = hashlib.sha256(tarball.read_bytes()).hexdigest().lower()
    return actual == expected


def _safe_extract_tarball(tf: tarfile.TarFile, dest: Path) -> None:
    """兼容 Python 3.10.0-3.10.11 的安全 tarball 解压（防路径穿越）。"""
    try:
        tf.extractall(dest, filter="data")
        return
    except TypeError:
        pass
    dest_real = os.path.realpath(dest)
    for member in tf.getmembers():
        if member.issym() or member.islnk():
            raise ValueError(f"Refusing link entry in tarball on legacy Python: {member.name}")
        target_real = os.path.realpath(dest / member.name)
        if target_real != dest_real and not target_real.startswith(dest_real + os.sep):
            raise ValueError(f"Refusing path traversal entry in tarball: {member.name}")
    tf.extractall(dest)


def _cmd_update(args: argparse.Namespace) -> int:
    if not args.tag:
        print("ERROR: --update requires --tag <release-tag> (e.g. v0.1.0).", file=sys.stderr)
        return 2
    canonical = get_canonical_skill_dir()
    if not (canonical / MARKER_FILENAME).is_file():
        print(f"ERROR: {canonical} is not a managed installation.", file=sys.stderr)
        return 1

    with tempfile.TemporaryDirectory(prefix="notebooklm-t3-update-") as tmp:
        tmp_dir = Path(tmp)
        if args.tarball and args.checksums:
            tb_path = Path(args.tarball).expanduser().resolve()
            cs_path = Path(args.checksums).expanduser().resolve()
        else:
            env, _ = build_proxy_env(load_config())
            base_url = f"https://github.com/{GITHUB_OWNER}/{GITHUB_REPO}/releases/download/{args.tag}"
            tb_path = tmp_dir / f"{GITHUB_REPO}-{args.tag}.tar.gz"
            cs_path = tmp_dir / "SHA256SUMS"
            for url, dst in ((f"{base_url}/{tb_path.name}", tb_path), (f"{base_url}/SHA256SUMS", cs_path)):
                proc = subprocess.run(["curl", "-fsSL", "-o", str(dst), url], env=env, check=False)
                if proc.returncode != 0:
                    print(f"ERROR: failed to download {url}", file=sys.stderr)
                    return proc.returncode

        if not _verify_sha256(tb_path, cs_path):
            print("ERROR: SHA-256 checksum verification failed.", file=sys.stderr)
            return 1

        extract_dir = tmp_dir / "extracted"
        extract_dir.mkdir()
        with tarfile.open(tb_path, "r:gz") as tf:
            _safe_extract_tarball(tf, extract_dir)

        # 定位解压后的包根目录与 skill/notebooklm-t3
        skill_dirs = list(extract_dir.glob("**/skill/notebooklm-t3"))
        if not skill_dirs:
            print("ERROR: skill/notebooklm-t3 not found in release tarball.", file=sys.stderr)
            return 1
        new_skill_src = skill_dirs[0]
        extracted_repo_root = new_skill_src.parent.parent
        new_ver = get_version(extracted_repo_root)
        marker = _copy_skill_tree(new_skill_src, canonical, new_ver, tag=args.tag)

        # 同步刷新 ~/.local/share/notebooklm-t3/src 缓存，保证 src/install.sh --doctor 版本一致
        src_cache = get_src_cache_dir()
        if src_cache.exists() and (src_cache / MARKER_FILENAME).is_file():
            shutil.rmtree(src_cache)
            shutil.copytree(
                extracted_repo_root,
                src_cache,
                ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc", ".pytest_cache", "notebooklm-outputs"),
            )
            atomic_write_json(
                src_cache / MARKER_FILENAME,
                {"managed_by": SKILL_NAME, "version": new_ver, "tag": args.tag, "installed_at": utc_now_iso()},
                mode=0o644,
            )

        # 同步刷新 manifest 版本与已写入技能指纹
        manifest = load_json_file(get_manifest_path(), default=None)
        if isinstance(manifest, dict):
            manifest["version"] = new_ver
            manifest["updated_at"] = utc_now_iso()
            for entry in manifest.get("entries", []):
                if entry.get("op") == "copy_canonical_skill":
                    entry["written_sha256"] = sha256_json(marker["files"])
            _write_manifest(manifest)

    out = {
        "ok": True,
        "updated_to": args.tag,
        "version": new_ver,
        "canonical_skill": str(canonical),
        "src_cache_updated": (get_src_cache_dir() / MARKER_FILENAME).is_file(),
    }
    print(json.dumps(out, ensure_ascii=False, indent=2) if args.json else f"Updated to {args.tag} (v{new_ver})")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="install.sh", description="notebooklm-t3 installer & doctor")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--probe", action="store_true", help="Read-only environment probe")
    mode.add_argument("--plan", action="store_true", help="Preflight sandbox/conflicts and print WAL plan")
    mode.add_argument("--apply", action="store_true", help="Apply installation using WAL manifest")
    mode.add_argument("--rollback", action="store_true", help="Roll back an interrupted install via WAL manifest")
    mode.add_argument("--doctor", action="store_true", help="Run health checks")
    mode.add_argument("--update", action="store_true", help="Update managed skill from verified release tarball")
    mode.add_argument("--uninstall", action="store_true", help="Uninstall using fingerprint-checked WAL manifest")

    p.add_argument("--non-interactive", action="store_true", help="Never prompt on stdin")
    p.add_argument("--json", action="store_true", help="Output structured JSON")
    p.add_argument("--profile", default=DEFAULT_PROFILE, help="NotebookLM profile name (default: default)")
    p.add_argument("--browser-profile", default="", help="Explicit browser::profile (e.g. 'chrome::Profile 1')")
    p.add_argument("--allowed-roots", default="", help="Workspace roots for MCP NOTEBOOKLM_MCP_ALLOWED_ROOTS")
    p.add_argument("--proxy", default="", help="Optional explicit proxy URL (default: auto)")
    p.add_argument("--no-proxy", default="", help="Optional explicit NO_PROXY value")
    p.add_argument("--keepalive", action="store_true", help="Install 20-minute background keepalive service")
    p.add_argument(
        "--keepalive-browser-fallback",
        action="store_true",
        help="Opt-in: allow background keepalive to read browser cookies (default: off to avoid Keychain popups)",
    )
    p.add_argument("--skip-mcp", action="store_true", help="Skip Antigravity MCP registration")
    p.add_argument("--skip-codex-symlink", action="store_true", help="Skip ~/.codex/skills symlink if Codex loads ~/.agents/skills natively")
    p.add_argument("--skip-warmup", action="store_true", help="Skip foreground uv script pre-warming")
    p.add_argument("--force-engine", action="store_true", help="Force reinstall notebooklm-py with pinned range and socksio")
    p.add_argument("--adopt-legacy", action="store_true", help="Back up and adopt existing unmanaged ~/.agents/skills/notebooklm-t3")
    p.add_argument(
        "--bootout-legacy-label",
        action="append",
        default=[],
        help="macOS LaunchAgent label to bootout before installing new keepalive",
    )
    p.add_argument("--link-from", default="", help="Maintainer dev mode: symlink canonical skill to local repo")
    p.add_argument("--tag", default="", help="Release tag for --apply metadata or --update")
    p.add_argument("--tarball", default="", help="Local release tarball path for offline --update")
    p.add_argument("--checksums", default="", help="Local SHA256SUMS path for offline --update")
    p.add_argument("--offline", action="store_true", help="Skip network checks in --doctor")
    p.add_argument("--purge-auth", action="store_true", help="With --uninstall: also remove ~/.notebooklm/")
    p.add_argument(
        "--uninstall-engine",
        action="store_true",
        help="With --uninstall: also uninstall notebooklm-py (only if installed by this tool)",
    )
    return p


def _run_interactive_wizard(args: argparse.Namespace) -> int:
    """终端用户的交互式向导（仅在 TTY 下运行，绝不在非 TTY Agent Shell 中阻塞）。"""
    print(f"notebooklm-t3 v{get_version()} interactive setup")
    uv_info = _detect_uv_paths()
    if not uv_info.get("uv_bin"):
        print("uv is not installed. Please install uv first: https://docs.astral.sh/uv/getting-started/installation/")
        return 1
    roots = input("Workspace root(s) for MCP file access (leave empty to skip, do not use $HOME): ").strip()
    ka = input("Enable 20-minute background keepalive? [Y/n]: ").strip().lower()
    args.allowed_roots = roots
    args.keepalive = ka in ("", "y", "yes")
    args.non_interactive = True
    plan = _build_plan(args)
    print(json.dumps(plan, ensure_ascii=False, indent=2))
    if plan["status"] != "ready":
        return 1
    ok = input("Proceed with this plan? [y/N]: ").strip().lower()
    if ok not in ("y", "yes"):
        print("Aborted.")
        return 1
    return _cmd_apply(args)


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.probe:
        return _cmd_probe(args)
    if args.plan:
        return _cmd_plan(args)
    if args.apply:
        return _cmd_apply(args)
    if args.rollback:
        return _cmd_rollback(args)
    if args.doctor:
        return _cmd_doctor(args)
    if args.update:
        return _cmd_update(args)
    if args.uninstall:
        return _cmd_uninstall(args)

    # 未指定模式：TTY 进入交互向导，非 TTY 立刻非零报错绝不卡死
    if sys.stdin.isatty() and not args.non_interactive:
        return _run_interactive_wizard(args)

    print(
        "ERROR: non-interactive shell requires an explicit mode (--probe, --plan, --apply, --doctor, --update, --uninstall).",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
