"""nlm.py、keepalive.py 与 _common.py 的离线单元测试。"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "skill" / "notebooklm-t3" / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import _common  # noqa: E402


def _make_mock_nlm(bin_path: Path, log_path: Path) -> None:
    """构造可记录参数与模拟 inspect/login/refresh/generate --help 行为的假 notebooklm CLI。"""
    script = f"""#!/usr/bin/env python3
import json, os, sys
from pathlib import Path

LOG = Path({str(log_path)!r})
with LOG.open("a", encoding="utf-8") as f:
    f.write(json.dumps({{
        "argv": sys.argv[1:],
        "poke": os.environ.get("NOTEBOOKLM_DISABLE_KEEPALIVE_POKE"),
        "https_proxy": os.environ.get("HTTPS_PROXY"),
        "no_proxy": os.environ.get("NO_PROXY"),
    }}) + "\\n")

args = sys.argv[1:]
# strip leading --profile <p>
if len(args) >= 2 and args[0] == "--profile":
    profile = args[1]
    rest = args[2:]
else:
    profile = "default"
    rest = args

home = Path(os.environ["HOME"])
storage = home / ".notebooklm" / "profiles" / profile / "storage_state.json"

if rest == ["--version"]:
    print("notebooklm 0.8.6")
    sys.exit(0)

if rest[:3] == ["generate", "quiz", "--help"]:
    print("Usage: notebooklm generate quiz [OPTIONS] [DESCRIPTION]\\n  --quantity\\n")
    sys.exit(0)

if rest[:3] == ["generate", "audio", "--help"]:
    print("Usage: notebooklm generate audio [OPTIONS] [DESCRIPTION]\\n  --language TEXT\\n")
    sys.exit(0)

if rest[:2] == ["generate", "quiz"] or rest[:2] == ["generate", "audio"]:
    print(json.dumps({{"ok": True, "forwarded": rest}}))
    sys.exit(0)

if rest[:2] == ["auth", "check"]:
    if os.environ.get("MOCK_AUTH_VALID", "1") == "1" and storage.exists():
        print("Authentication is valid.")
        sys.exit(0)
    print("AUTH_ERROR", file=sys.stderr)
    sys.exit(1)

if rest[:2] == ["auth", "refresh"]:
    if "--allow-headless" in rest:
        print("ERROR: allow-headless must never be passed", file=sys.stderr)
        sys.exit(99)
    if "--browser-cookies" in rest:
        if os.environ.get("MOCK_BROWSER_REFRESH_OK", "0") == "1":
            print("Refreshed via browser cookies")
            sys.exit(0)
        sys.exit(1)
    if os.environ.get("MOCK_REFRESH_OK", "0") == "1":
        print("Refreshed")
        sys.exit(0)
    sys.exit(1)

if rest[:2] == ["auth", "inspect"]:
    mode = os.environ.get("MOCK_INSPECT_MODE", "two")
    if mode == "changed":
        rows = [{{"email": "other@example.com", "browser_profile": "chrome::Profile 1"}}]
    else:
        rows = [
            {{"email": "alice@example.com", "browser_profile": "chrome::Profile 1"}},
            {{"email": "bob@example.com", "browser_profile": "chrome::Profile 1"}},
        ]
    print(json.dumps({{"accounts": rows}}))
    sys.exit(0)

if rest and rest[0] == "login":
    if storage.exists():
        ans = sys.stdin.readline().strip().lower()
        if ans != "y":
            print("Aborted: profile already has saved auth; not overwriting.")
            sys.exit(1)
    storage.parent.mkdir(parents=True, exist_ok=True)
    storage.write_text(json.dumps({{"cookies": []}}), encoding="utf-8")
    print("Authentication saved")
    sys.exit(0)

print(json.dumps({{"passthrough": rest}}))
"""
    bin_path.write_text(script, encoding="utf-8")
    bin_path.chmod(0o755)


class TestCommonAndNlm(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / "home"
        self.home.mkdir()
        self.mock_bin = Path(self.tmp.name) / "mock_notebooklm"
        self.mock_log = Path(self.tmp.name) / "mock.jsonl"
        _make_mock_nlm(self.mock_bin, self.mock_log)

        self.env = os.environ.copy()
        self.env["HOME"] = str(self.home)
        self.env.pop("XDG_CONFIG_HOME", None)
        self.env.pop("XDG_STATE_HOME", None)
        self.env.pop("XDG_DATA_HOME", None)
        for k in ("http_proxy", "https_proxy", "all_proxy", "no_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"):
            self.env.pop(k, None)

        cfg_dir = self.home / ".config" / "notebooklm-t3"
        cfg_dir.mkdir(parents=True)
        (cfg_dir / "config.json").write_text(
            json.dumps(
                {
                    "profile": "default",
                    "nlm_bin": str(self.mock_bin),
                    "browser_profile": "chrome::Profile 1",
                    "account_email": "alice@example.com",
                    "keepalive": {"interval_seconds": 1200, "browser_fallback": False},
                }
            ),
            encoding="utf-8",
        )
        storage = self.home / ".notebooklm" / "profiles" / "default" / "storage_state.json"
        storage.parent.mkdir(parents=True)
        storage.write_text('{"cookies": []}', encoding="utf-8")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _run_nlm(self, *args: str, extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        env = dict(self.env)
        if extra_env:
            env.update(extra_env)
        return subprocess.run(
            [sys.executable, str(SCRIPTS_DIR / "nlm.py"), *args],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

    def _run_keepalive(self, *args: str, extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        env = dict(self.env)
        if extra_env:
            env.update(extra_env)
        return subprocess.run(
            [sys.executable, str(SCRIPTS_DIR / "keepalive.py"), *args],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_mask_email(self) -> None:
        self.assertEqual(_common.mask_email("carol.dev@example.com"), "c***@example.com")
        self.assertEqual(_common.mask_email("a@b.co"), "a***@b.co")
        self.assertEqual(_common.mask_email(""), "")

    def test_proxy_priority_and_no_proxy_and_allowlist(self) -> None:
        env, meta = _common.build_proxy_env(
            {"proxy": "http://127.0.0.1:8888", "no_proxy": "localhost,127.0.0.1"},
            base_env={"HTTPS_PROXY": "http://127.0.0.1:9999", "NODE_USE_ENV_PROXY": "1"},
        )
        self.assertEqual(meta["source"], "config")
        self.assertEqual(env["HTTPS_PROXY"], "http://127.0.0.1:8888")
        self.assertEqual(env["NO_PROXY"], "localhost,127.0.0.1")

        env2, meta2 = _common.build_proxy_env(
            {},
            base_env={"HTTPS_PROXY": "socks5://127.0.0.1:1080", "NO_PROXY": ".local"},
        )
        self.assertEqual(meta2["source"], "env")
        self.assertTrue(meta2["has_socks"])
        self.assertEqual(meta2["no_proxy"], ".local")

    def test_atomic_write_preserves_symlink_and_mode(self) -> None:
        real = self.home / "real_target.json"
        real.write_text('{"old": 1}', encoding="utf-8")
        real.chmod(0o600)
        link = self.home / "link_target.json"
        link.symlink_to(real)

        written_path = _common.atomic_write_json(link, {"new": 2})
        self.assertEqual(written_path, real.resolve())
        self.assertTrue(link.is_symlink())
        self.assertEqual(json.loads(real.read_text(encoding="utf-8")), {"new": 2})
        self.assertEqual(stat.S_IMODE(real.stat().st_mode), 0o600)

    def test_auth_status_staleness_ttl(self) -> None:
        old_home = os.environ.get("HOME")
        os.environ["HOME"] = str(self.home)
        try:
            st_path = _common.get_auth_status_path()
            stale_ts = (datetime.now(timezone.utc) - timedelta(minutes=50)).strftime("%Y-%m-%dT%H:%M:%SZ")
            _common.atomic_write_json(st_path, {"checked_at": stale_ts, "ok": True, "state": "ok", "method": "test"})
            res = _common.read_auth_status()
            self.assertEqual(res["state"], "unknown")
            self.assertTrue(res["stale"])

            _common.write_auth_status(ok=True, state="ok", method="test")
            res2 = _common.read_auth_status()
            self.assertEqual(res2["state"], "ok")
            self.assertFalse(res2["stale"])
        finally:
            if old_home is None:
                os.environ.pop("HOME", None)
            else:
                os.environ["HOME"] = old_home

    def test_guardrails_block_dangerous_commands(self) -> None:
        cases = [
            ("login", "--browser-cookies", "chrome"),
            ("login", "--browser-cookies=chrome"),
            ("login", "--browser-cookies", "chrome::Profile 1", "--account", "a@b.com"),
            ("auth", "refresh", "--allow-headless"),
            ("language", "set", "zh_Hans"),
            ("-v", "language", "set", "zh_Hans"),
            ("--quiet", "skill", "install"),
        ]
        for args in cases:
            proc = self._run_nlm(*args)
            self.assertEqual(proc.returncode, 2, f"Expected exit 2 for {args}, got {proc.returncode}: {proc.stderr}")
        self.assertFalse(self.mock_log.exists(), "Blocked commands must never invoke notebooklm binary")

    def test_status_masks_account_email(self) -> None:
        proc = self._run_nlm("status", "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertNotIn("alice@example.com", proc.stdout)
        data = json.loads(proc.stdout)
        self.assertEqual(data["account_email_masked"], "a***@example.com")

    def test_dynamic_language_capability_probing(self) -> None:
        p1 = self._run_nlm("generate", "quiz", "-n", "nb1", "--language", "zh_Hans", "focus on agents")
        self.assertEqual(p1.returncode, 0, p1.stderr)
        out1 = json.loads(p1.stdout)
        self.assertNotIn("--language", out1["forwarded"])
        self.assertTrue(any("简体中文" in tok for tok in out1["forwarded"]))

        p2 = self._run_nlm("generate", "audio", "-n", "nb1", "--language", "zh_Hans", "podcast")
        self.assertEqual(p2.returncode, 0, p2.stderr)
        out2 = json.loads(p2.stdout)
        self.assertIn("--language", out2["forwarded"])

    def test_recover_never_calls_login(self) -> None:
        proc = self._run_nlm(
            "recover",
            extra_env={"MOCK_AUTH_VALID": "0", "MOCK_REFRESH_OK": "0", "MOCK_BROWSER_REFRESH_OK": "1"},
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        calls = [json.loads(line)["argv"] for line in self.mock_log.read_text(encoding="utf-8").splitlines()]
        self.assertFalse(any("login" in c for c in calls), f"recover must never call login: {calls}")
        self.assertTrue(any("--browser-cookies" in c and "refresh" in c for c in calls))

    def test_setup_auth_three_step_flow_and_safety(self) -> None:
        # 1. inspect -> masked emails, 0600 candidate cache
        p_ins = self._run_nlm("setup-auth", "inspect", "--browser", "chrome::Profile 1", "--json")
        self.assertEqual(p_ins.returncode, 0, p_ins.stderr)
        self.assertNotIn("alice@example.com", p_ins.stdout)
        self.assertNotIn("bob@example.com", p_ins.stdout)
        ins_data = json.loads(p_ins.stdout)
        self.assertEqual(len(ins_data["candidates"]), 2)
        self.assertEqual(ins_data["candidates"][1]["masked_email"], "b***@example.com")

        cand_path = self.home / ".local" / "state" / "notebooklm-t3" / "setup_candidates.json"
        self.assertEqual(stat.S_IMODE(cand_path.stat().st_mode), 0o600)

        # 2. apply without --confirm-overwrite when storage exists -> cleanly rejected, cache preserved
        p_rej = self._run_nlm("setup-auth", "apply", "--index", "1", "--json")
        self.assertNotEqual(p_rej.returncode, 0)
        self.assertEqual(json.loads(p_rej.stdout)["error"], "overwrite_confirmation_required")
        self.assertTrue(cand_path.exists(), "Candidate cache must be preserved on overwrite rejection")

        # 3. apply when live accounts changed -> rejected cleanly
        p_chg = self._run_nlm(
            "setup-auth", "apply", "--index", "1", "--confirm-overwrite", "--json",
            extra_env={"MOCK_INSPECT_MODE": "changed"},
        )
        self.assertNotEqual(p_chg.returncode, 0)
        self.assertEqual(json.loads(p_chg.stdout)["error"], "accounts_changed")

        # 4. apply when 10-minute TTL expired -> rejected cleanly, cache preserved
        cached_doc = json.loads(cand_path.read_text(encoding="utf-8"))
        orig_ts = cached_doc["inspect_at"]
        cached_doc["inspect_at"] = (datetime.now(timezone.utc) - timedelta(minutes=11)).strftime("%Y-%m-%dT%H:%M:%SZ")
        cand_path.write_text(json.dumps(cached_doc), encoding="utf-8")
        p_exp = self._run_nlm("setup-auth", "apply", "--index", "1", "--confirm-overwrite", "--json")
        self.assertEqual(p_exp.returncode, 1)
        self.assertEqual(json.loads(p_exp.stdout)["error"], "candidates_expired")
        self.assertTrue(cand_path.exists(), "Expired candidate cache stays intact until re-inspect")
        cached_doc["inspect_at"] = orig_ts
        cand_path.write_text(json.dumps(cached_doc), encoding="utf-8")

        # 5. apply with --confirm-overwrite -> succeeds, updates config, deletes cache, masks output
        p_ok = self._run_nlm("setup-auth", "apply", "--index", "1", "--confirm-overwrite", "--json")
        self.assertEqual(p_ok.returncode, 0, p_ok.stderr)
        self.assertNotIn("bob@example.com", p_ok.stdout)
        self.assertEqual(json.loads(p_ok.stdout)["account_email_masked"], "b***@example.com")
        self.assertFalse(cand_path.exists(), "Candidate cache must be deleted after successful apply")
        saved_cfg = json.loads((self.home / ".config" / "notebooklm-t3" / "config.json").read_text(encoding="utf-8"))
        self.assertEqual(saved_cfg["account_email"], "bob@example.com")

    def test_keepalive_default_no_browser_fallback_and_no_allow_headless(self) -> None:
        proc = self._run_keepalive(extra_env={"MOCK_REFRESH_OK": "0", "MOCK_BROWSER_REFRESH_OK": "1"})
        self.assertEqual(proc.returncode, 1)
        calls = [json.loads(line)["argv"] for line in self.mock_log.read_text(encoding="utf-8").splitlines()]
        self.assertFalse(any("--browser-cookies" in c for c in calls))
        self.assertFalse(any("--allow-headless" in c for c in calls))
        status_doc = json.loads(
            (self.home / ".local" / "state" / "notebooklm-t3" / "auth_status.json").read_text(encoding="utf-8")
        )
        self.assertEqual(status_doc["state"], "expired")


if __name__ == "__main__":
    unittest.main()
