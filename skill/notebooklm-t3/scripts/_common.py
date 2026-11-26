"""notebooklm-t3 公共运行时辅助模块（零第三方依赖，Python >= 3.10）。"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
import tempfile
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SKILL_NAME = "notebooklm-t3"
DEFAULT_PROFILE = "default"
ENGINE_SPEC = "notebooklm-py[browser,mcp,markdown,impersonate,cookies,headless]>=0.8.6,<0.9"
ENGINE_MIN_VERSION = (0, 8, 6)
ENGINE_MAX_EXCLUSIVE = (0, 9, 0)
AUTH_STATUS_TTL_SECONDS = 45 * 60
SETUP_CANDIDATES_TTL_SECONDS = 10 * 60
DEFAULT_AUTH_TIMEOUT_SECONDS = 120
KEEPALIVE_LOG_MAX_LINES = 200
MARKER_FILENAME = ".notebooklm-t3-managed.json"

ALLOWED_PROXY_SCHEMES = ("http", "https", "all", "socks", "no")


def get_home() -> Path:
    """动态读取当前 HOME，方便隔离沙盒单元测试。"""
    return Path(os.environ.get("HOME") or Path.home()).expanduser()


def get_config_dir() -> Path:
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg).expanduser() if xdg else (get_home() / ".config")
    return base / SKILL_NAME


def get_state_dir() -> Path:
    xdg = os.environ.get("XDG_STATE_HOME")
    base = Path(xdg).expanduser() if xdg else (get_home() / ".local" / "state")
    return base / SKILL_NAME


def get_data_dir() -> Path:
    xdg = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg).expanduser() if xdg else (get_home() / ".local" / "share")
    return base / SKILL_NAME


def get_config_path() -> Path:
    return get_config_dir() / "config.json"


def get_manifest_path() -> Path:
    return get_config_dir() / "manifest.json"


def get_auth_status_path() -> Path:
    return get_state_dir() / "auth_status.json"


def get_setup_candidates_path() -> Path:
    return get_state_dir() / "setup_candidates.json"


def get_capabilities_cache_path() -> Path:
    return get_state_dir() / "capabilities.json"


def get_keepalive_log_path() -> Path:
    return get_state_dir() / "keepalive.log"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_utc_iso(value: str | None) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def mask_email(email: str | None) -> str:
    """将邮箱脱敏为 x***@domain，避免明文邮箱进入模型上下文。"""
    if not email or not isinstance(email, str):
        return ""
    cleaned = email.strip()
    if "@" not in cleaned:
        return cleaned[:1] + "***" if cleaned else ""
    local, domain = cleaned.split("@", 1)
    prefix = local[:1] if local else ""
    return f"{prefix}***@{domain}"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_json(obj: Any) -> str:
    canonical = json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8"
    )
    return sha256_bytes(canonical)


def resolve_symlink_target_path(path: Path) -> Path:
    """解析目标路径的真实写入位置：若目标是软链，则写入软链指向的真实文件。"""
    if path.is_symlink():
        return Path(os.path.realpath(path))
    parent_real = Path(os.path.realpath(path.parent))
    return parent_real / path.name


def atomic_write_bytes(path: Path, data: bytes, mode: int | None = None) -> Path:
    """保软链、保权限位的原子写盘，返回最终写入的真实路径。"""
    real_target = resolve_symlink_target_path(path)
    real_target.parent.mkdir(parents=True, exist_ok=True)

    preserve_mode: int | None = mode
    if preserve_mode is None and real_target.exists():
        preserve_mode = stat.S_IMODE(real_target.stat().st_mode)

    fd, tmp_name = tempfile.mkstemp(prefix=f".{real_target.name}.tmp.", dir=str(real_target.parent))
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if preserve_mode is not None:
            os.chmod(tmp_path, preserve_mode)
        os.replace(tmp_path, real_target)
    except BaseException:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return real_target


def atomic_write_json(path: Path, payload: Any, mode: int | None = None) -> Path:
    encoded = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    return atomic_write_bytes(path, encoded, mode=mode)


def load_json_file(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def load_config() -> dict[str, Any]:
    data = load_json_file(get_config_path(), default={})
    return data if isinstance(data, dict) else {}


def save_config(config: dict[str, Any]) -> Path:
    return atomic_write_json(get_config_path(), config, mode=0o600)


def read_auth_status(ttl_seconds: int = AUTH_STATUS_TTL_SECONDS) -> dict[str, Any]:
    """读取后台保活与最近一次自检状态；超过 TTL 视为 unknown。"""
    raw = load_json_file(get_auth_status_path(), default=None)
    if not isinstance(raw, dict):
        return {
            "state": "unknown",
            "ok": None,
            "stale": True,
            "checked_at": None,
            "method": None,
            "error": None,
        }
    checked_dt = parse_utc_iso(raw.get("checked_at"))
    now = datetime.now(timezone.utc)
    if checked_dt is None:
        stale = True
        age_seconds = None
    else:
        age_seconds = max(0, int((now - checked_dt).total_seconds()))
        stale = age_seconds > ttl_seconds

    state = raw.get("state") or ("ok" if raw.get("ok") else "expired")
    ok = raw.get("ok")
    if stale:
        state = "unknown"
        ok = None
    return {
        "state": state,
        "ok": ok,
        "stale": stale,
        "age_seconds": age_seconds,
        "checked_at": raw.get("checked_at"),
        "method": raw.get("method"),
        "error": raw.get("error"),
    }


def write_auth_status(
    *,
    ok: bool,
    state: str,
    method: str,
    error: str | None = None,
) -> Path:
    payload = {
        "checked_at": utc_now_iso(),
        "ok": ok,
        "state": state,
        "method": method,
        "error": error,
    }
    return atomic_write_json(get_auth_status_path(), payload, mode=0o600)


def resolve_uv_bin(config: dict[str, Any] | None = None) -> Path | None:
    cfg = config if config is not None else load_config()
    candidates: list[Path] = []
    cfg_uv = cfg.get("uv_bin")
    if isinstance(cfg_uv, str) and cfg_uv.strip():
        candidates.append(Path(cfg_uv).expanduser())
    found = shutil.which("uv")
    if found:
        candidates.append(Path(found))
    home = get_home()
    candidates.extend(
        [
            home / ".local" / "bin" / "uv",
            Path("/opt/homebrew/bin/uv"),
            Path("/usr/local/bin/uv"),
        ]
    )
    for cand in candidates:
        if cand.is_file() and os.access(cand, os.X_OK):
            return cand
    return None


def resolve_nlm_bin(config: dict[str, Any] | None = None) -> Path | None:
    cfg = config if config is not None else load_config()
    candidates: list[Path] = []
    cfg_nlm = cfg.get("nlm_bin")
    if isinstance(cfg_nlm, str) and cfg_nlm.strip():
        candidates.append(Path(cfg_nlm).expanduser())
    found = shutil.which("notebooklm")
    if found:
        candidates.append(Path(found))
    uv_bin = resolve_uv_bin(cfg)
    if uv_bin:
        try:
            proc = subprocess.run(
                [str(uv_bin), "tool", "dir", "--bin"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                candidates.append(Path(proc.stdout.strip()) / "notebooklm")
        except (OSError, subprocess.SubprocessError):
            pass
    candidates.append(get_home() / ".local" / "bin" / "notebooklm")
    for cand in candidates:
        if cand.is_file() and os.access(cand, os.X_OK):
            return cand
    return None


def get_storage_state_path(profile: str = DEFAULT_PROFILE) -> Path:
    return get_home() / ".notebooklm" / "profiles" / profile / "storage_state.json"


def get_master_token_path(profile: str = DEFAULT_PROFILE) -> Path:
    return get_home() / ".notebooklm" / "profiles" / profile / "master_token.json"


def detect_system_login_browser() -> str | None:
    """探测本机已安装的系统浏览器（优先 Google Chrome，其次 Edge），避免无故下载 ~600MB Playwright Chromium。"""
    override = os.environ.get("NOTEBOOKLM_T3_SYSTEM_BROWSER")
    if override is not None:
        val = override.strip().lower()
        return val if val in ("chrome", "msedge") else None

    chrome_paths = (
        Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
        Path.home() / "Applications" / "Google Chrome.app" / "Contents" / "MacOS" / "Google Chrome",
    )
    if any(p.is_file() for p in chrome_paths) or shutil.which("google-chrome") or shutil.which("google-chrome-stable"):
        return "chrome"

    edge_paths = (
        Path("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"),
        Path.home() / "Applications" / "Microsoft Edge.app" / "Contents" / "MacOS" / "Microsoft Edge",
    )
    if any(p.is_file() for p in edge_paths) or shutil.which("microsoft-edge") or shutil.which("microsoft-edge-stable"):
        return "msedge"

    return None


def _normalize_proxy_url(scheme: str, raw: str) -> str:
    val = raw.strip()
    if not val:
        return ""
    if "://" in val:
        return val
    if scheme == "socks":
        return f"socks5://{val}"
    return f"http://{val}"


def build_proxy_env(
    config: dict[str, Any] | None = None,
    base_env: dict[str, str] | None = None,
) -> tuple[dict[str, str], dict[str, Any]]:
    """按 config 显式值 > 当前环境变量 > 系统代理 > 直连 的优先级组装代理环境变量。

    严格白名单仅采纳 http/https/all/socks/no 五个键，避免无关环境变量污染。
    """
    cfg = config if config is not None else load_config()
    env = dict(os.environ if base_env is None else base_env)

    # 1. config.json 显式配置（最高优先级）
    explicit_proxy = cfg.get("proxy")
    explicit_no_proxy = cfg.get("no_proxy")
    if isinstance(explicit_proxy, str) and explicit_proxy.strip() and explicit_proxy.strip() != "auto":
        url = _normalize_proxy_url("http", explicit_proxy)
        env["http_proxy"] = url
        env["https_proxy"] = url
        env["HTTP_PROXY"] = url
        env["HTTPS_PROXY"] = url
        if isinstance(explicit_no_proxy, str) and explicit_no_proxy.strip():
            env["no_proxy"] = explicit_no_proxy.strip()
            env["NO_PROXY"] = explicit_no_proxy.strip()
        return env, {
            "source": "config",
            "http": url,
            "https": url,
            "no_proxy": env.get("NO_PROXY") or env.get("no_proxy") or "",
            "has_socks": url.lower().startswith(("socks5://", "socks5h://", "socks://")),
        }

    # 2. 当前调用环境变量
    env_http = env.get("http_proxy") or env.get("HTTP_PROXY") or ""
    env_https = env.get("https_proxy") or env.get("HTTPS_PROXY") or ""
    env_all = env.get("all_proxy") or env.get("ALL_PROXY") or ""
    env_no = env.get("no_proxy") or env.get("NO_PROXY") or ""
    if env_http or env_https or env_all:
        has_socks = any(
            v.lower().startswith(("socks5://", "socks5h://", "socks://"))
            for v in (env_http, env_https, env_all)
            if v
        )
        return env, {
            "source": "env",
            "http": env_http or env_all,
            "https": env_https or env_all,
            "no_proxy": env_no,
            "has_socks": has_socks,
        }

    # 3. 系统代理（macOS 走 _scproxy，launchd 下同样可用）
    raw_proxies = urllib.request.getproxies()
    filtered = {
        k.lower(): v
        for k, v in raw_proxies.items()
        if isinstance(k, str) and k.lower() in ALLOWED_PROXY_SCHEMES and isinstance(v, str) and v.strip()
    }
    sys_http = _normalize_proxy_url("http", filtered.get("http", "")) if filtered.get("http") else ""
    sys_https = (
        _normalize_proxy_url("https", filtered.get("https", "")) if filtered.get("https") else ""
    )
    sys_all = _normalize_proxy_url("all", filtered.get("all", "")) if filtered.get("all") else ""
    sys_socks = (
        _normalize_proxy_url("socks", filtered.get("socks", "")) if filtered.get("socks") else ""
    )
    sys_no = filtered.get("no", "").strip()

    chosen_http = sys_http or sys_all or sys_socks
    chosen_https = sys_https or sys_all or sys_socks
    if chosen_http or chosen_https:
        if chosen_http:
            env["http_proxy"] = chosen_http
            env["HTTP_PROXY"] = chosen_http
        if chosen_https:
            env["https_proxy"] = chosen_https
            env["HTTPS_PROXY"] = chosen_https
        if sys_no:
            env["no_proxy"] = sys_no
            env["NO_PROXY"] = sys_no
        has_socks = any(
            v.lower().startswith(("socks5://", "socks5h://", "socks://"))
            for v in (chosen_http, chosen_https, sys_socks)
            if v
        )
        return env, {
            "source": "system",
            "http": chosen_http,
            "https": chosen_https,
            "no_proxy": sys_no,
            "has_socks": has_socks,
        }

    # 4. 直连（透传可能存在的 no_proxy）
    return env, {
        "source": "direct",
        "http": "",
        "https": "",
        "no_proxy": env_no,
        "has_socks": False,
    }


def parse_semver(version_text: str) -> tuple[int, int, int] | None:
    """从 `notebooklm --version` 输出中提取主次修订三段版本号。"""
    import re

    match = re.search(r"(\d+)\.(\d+)\.(\d+)", version_text or "")
    if not match:
        return None
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)))


def is_supported_engine_version(ver: tuple[int, int, int] | None) -> bool:
    if ver is None:
        return False
    return ENGINE_MIN_VERSION <= ver < ENGINE_MAX_EXCLUSIVE
