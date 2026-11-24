"""installer/install.py 的离线沙盒、WAL 故障注入回滚、指纹卸载、Antigravity 实例与更新测试。"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import stat
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
INSTALL_PY = REPO_ROOT / "installer" / "install.py"


def _make_fake_uv_and_engine(bin_dir: Path, tool_dir: Path, *, with_socksio: bool = True, version: str = "0.8.6") -> None:
    bin_dir.mkdir(parents=True, exist_ok=True)
    venv_bin = tool_dir / "notebooklm-py" / "bin"
    venv_bin.mkdir(parents=True, exist_ok=True)

    # fake engine python
    fake_py = venv_bin / "python"
    fake_py.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        f"HAS_SOCKS = {with_socksio!r}\n"
        "if len(sys.argv) >= 3 and sys.argv[1] == '-c' and 'socksio' in sys.argv[2]:\n"
        "    sys.exit(0 if HAS_SOCKS else 1)\n"
        "sys.exit(0)\n",
        encoding="utf-8",
    )
    fake_py.chmod(0o755)

    # fake notebooklm & notebooklm-mcp
    fake_nlm = bin_dir / "notebooklm"
    fake_nlm.write_text(
        f"#!/usr/bin/env python3\nimport sys\n"
        f"if '--version' in sys.argv: print('notebooklm {version}'); sys.exit(0)\n"
        "print('Authentication is valid.')\n",
        encoding="utf-8",
    )
    fake_nlm.chmod(0o755)

    fake_mcp = bin_dir / "notebooklm-mcp"
    fake_mcp.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fake_mcp.chmod(0o755)

    # fake uv
    fake_uv = bin_dir / "uv"
    fake_uv.write_text(
        f"#!/usr/bin/env python3\nimport sys\n"
        "args = sys.argv[1:]\n"
        f"if args == ['tool', 'dir']: print({str(tool_dir)!r}); sys.exit(0)\n"
        f"if args == ['tool', 'dir', '--bin']: print({str(bin_dir)!r}); sys.exit(0)\n"
        "if args[:2] == ['tool', 'install']: print('installed'); sys.exit(0)\n"
        "if args[:2] == ['tool', 'uninstall']: print('uninstalled'); sys.exit(0)\n"
        "sys.exit(0)\n",
        encoding="utf-8",
    )
    fake_uv.chmod(0o755)


class TestInstallerWalSandbox(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.home = self.root / "home"
        self.home.mkdir()
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.bin_dir = self.root / "fake_bin"
        self.tool_dir = self.root / "fake_uv_tools"
        _make_fake_uv_and_engine(self.bin_dir, self.tool_dir, with_socksio=True)

        # 构造干净陌生机器的 T3 目录（标准 Antigravity 实例无 ~/.gemini/config 软链！）
        t3_user = self.home / ".t3" / "userdata"
        agy_cfg = t3_user / "providers" / "antigravity" / "deadbeef1234" / "config"
        (agy_cfg / "skills").mkdir(parents=True)

        # 用软链模拟用户 dotfiles 管理的 mcp_config.json，含已有其他 MCP
        dotfiles_mcp = self.home / "dotfiles_mcp_config.json"
        dotfiles_mcp.write_text(json.dumps({"mcpServers": {"other_srv": {"command": "echo"}}}), encoding="utf-8")
        dotfiles_mcp.chmod(0o600)
        (agy_cfg / "mcp_config.json").symlink_to(dotfiles_mcp)

        # 启用自定义 homePath 的 claudeAgent + antigravity
        claude_custom = self.home / ".claude_custom"
        claude_custom.mkdir()
        (self.home / ".codex").mkdir()
        (self.home / ".grok").mkdir()

        (t3_user / "settings.json").write_text(
            json.dumps(
                {
                    "providerInstances": {
                        "antigravity": {"driver": "antigravity", "enabled": True},
                        "claude-alt": {
                            "driver": "claudeAgent",
                            "enabled": True,
                            "config": {"homePath": str(claude_custom)},
                        },
                    }
                }
            ),
            encoding="utf-8",
        )

        # 模拟 Chrome Local State 供 --probe 脱敏读取
        if platform.system() == "Darwin":
            chrome_dir = self.home / "Library" / "Application Support" / "Google" / "Chrome"
        else:
            chrome_dir = self.home / ".config" / "google-chrome"
        chrome_dir.mkdir(parents=True)
        (chrome_dir / "Local State").write_text(
            json.dumps(
                {
                    "profile": {
                        "info_cache": {
                            "Default": {"name": "Work", "user_name": "secret.user@gmail.com"},
                            "Profile 2": {"name": "Personal", "user_name": "second@example.com"},
                        }
                    }
                }
            ),
            encoding="utf-8",
        )

        # 预置 ~/.notebooklm 以验证卸载默认保留
        nlm_store = self.home / ".notebooklm" / "profiles" / "default"
        nlm_store.mkdir(parents=True)
        (nlm_store / "storage_state.json").write_text('{"cookies": ["keep"]}', encoding="utf-8")

        self.env = os.environ.copy()
        self.env["HOME"] = str(self.home)
        self.env["PATH"] = f"{self.bin_dir}:{self.env.get('PATH', '')}"
        self.env["NOTEBOOKLM_T3_SKIP_SERVICE_CTL"] = "1"
        for k in ("XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_DATA_HOME", "http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"):
            self.env.pop(k, None)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _run_install(self, *args: str, extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        env = dict(self.env)
        if extra_env:
            env.update(extra_env)
        return subprocess.run(
            [sys.executable, str(INSTALL_PY), *args],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_probe_masks_emails_and_zero_home_writes(self) -> None:
        proc = self._run_install("--probe", "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertNotIn("secret.user@gmail.com", proc.stdout)
        self.assertNotIn("second@example.com", proc.stdout)
        data = json.loads(proc.stdout)
        self.assertEqual(len(data["browser_candidates"]), 2)
        self.assertEqual(data["browser_candidates"][0]["sync_email_masked"], "s***@gmail.com")
        self.assertFalse((self.home / ".config" / "notebooklm-t3").exists())
        self.assertFalse((self.home / ".agents").exists())

    def test_plan_rejects_home_allowed_roots_and_detects_shared_codex_conflict(self) -> None:
        # allowed_roots = $HOME 必须拒绝
        p_home = self._run_install("--plan", "--json", "--allowed-roots", str(self.home))
        self.assertNotEqual(p_home.returncode, 0)
        self.assertEqual(json.loads(p_home.stdout)["status"], "invalid_argument")

        # 模拟 ~/.gemini/config/skills -> ~/.codex/skills 共享目录与 --skip-codex-symlink 冲突
        gemini_cfg = self.home / ".gemini" / "config"
        gemini_cfg.mkdir(parents=True)
        (self.home / ".codex" / "skills").mkdir(parents=True)
        (gemini_cfg / "skills").symlink_to(self.home / ".codex" / "skills")
        p_conf = self._run_install("--plan", "--json", "--skip-codex-symlink", "--allowed-roots", str(self.workspace))
        self.assertEqual(p_conf.returncode, 1)
        doc = json.loads(p_conf.stdout)
        self.assertEqual(doc["status"], "conflict")
        self.assertEqual(doc["conflicts"][0]["code"], "shared_target_conflict")

    def test_plan_sandbox_needs_escalation_zero_writes(self) -> None:
        if os.geteuid() == 0:
            self.skipTest("root bypasses POSIX write permission bits")
        cfg_parent = self.home / ".config"
        cfg_parent.mkdir(exist_ok=True)
        cfg_parent.chmod(0o500)
        try:
            proc = self._run_install("--plan", "--json", "--allowed-roots", str(self.workspace))
            self.assertEqual(proc.returncode, 4)
            doc = json.loads(proc.stdout)
            self.assertEqual(doc["status"], "needs_escalation")
            self.assertTrue(len(doc["targets"]) >= 1)
            self.assertFalse((self.home / ".agents").exists())
        finally:
            cfg_parent.chmod(0o700)

    def test_full_apply_antigravity_symlink_preserve_and_fingerprint_uninstall(self) -> None:
        p_apply = self._run_install(
            "--apply",
            "--non-interactive",
            "--json",
            "--allowed-roots",
            str(self.workspace),
            "--keepalive",
            "--skip-warmup",
        )
        self.assertEqual(p_apply.returncode, 0, p_apply.stderr)
        res = json.loads(p_apply.stdout)
        self.assertTrue(res["ok"])
        self.assertTrue(res["requires_new_session"])

        # 验证 Antigravity 实例自身 skills 软链 + 自定义 Claude + Codex + Grok 全部落位
        canonical = self.home / ".agents" / "skills" / "notebooklm-t3"
        self.assertTrue((canonical / ".notebooklm-t3-managed.json").is_file())
        agy_link = self.home / ".t3" / "userdata" / "providers" / "antigravity" / "deadbeef1234" / "config" / "skills" / "notebooklm-t3"
        self.assertTrue(agy_link.is_symlink())
        self.assertEqual(os.readlink(agy_link), str(canonical))
        self.assertTrue((self.home / ".claude_custom" / "skills" / "notebooklm-t3").is_symlink())
        self.assertTrue((self.home / ".codex" / "skills" / "notebooklm-t3").is_symlink())
        self.assertTrue((self.home / ".grok" / "skills" / "notebooklm-t3").is_symlink())

        # 验证 mcp_config.json 仍是软链、保留了 other_srv、注入了 notebooklm
        mcp_link = self.home / ".t3" / "userdata" / "providers" / "antigravity" / "deadbeef1234" / "config" / "mcp_config.json"
        self.assertTrue(mcp_link.is_symlink(), "atomic write must preserve mcp_config.json symlink")
        mcp_doc = json.loads((self.home / "dotfiles_mcp_config.json").read_text(encoding="utf-8"))
        self.assertIn("other_srv", mcp_doc["mcpServers"])
        self.assertIn("notebooklm", mcp_doc["mcpServers"])
        self.assertEqual(mcp_doc["mcpServers"]["notebooklm"]["env"]["NOTEBOOKLM_DISABLE_KEEPALIVE_POKE"], "1")

        # 验证 config.json 为 0600 权限
        cfg_file = self.home / ".config" / "notebooklm-t3" / "config.json"
        self.assertEqual(stat.S_IMODE(cfg_file.stat().st_mode), 0o600)

        # 验证离线 --doctor 全部健康
        p_doc = self._run_install("--doctor", "--offline", "--json")
        self.assertEqual(p_doc.returncode, 0, p_doc.stdout + p_doc.stderr)
        doc_res = json.loads(p_doc.stdout)
        self.assertTrue(doc_res["ok"])
        self.assertEqual(doc_res["missing_antigravity_skills"], [])
        self.assertEqual(doc_res["missing_antigravity_mcp"], [])

        # 用户修改了 mcpServers.notebooklm 后卸载：必须保留用户手改的配置，且保留 ~/.notebooklm
        mcp_doc["mcpServers"]["notebooklm"]["env"]["CUSTOM_USER_FLAG"] = "1"
        (self.home / "dotfiles_mcp_config.json").write_text(json.dumps(mcp_doc), encoding="utf-8")

        p_un = self._run_install("--uninstall", "--json")
        self.assertEqual(p_un.returncode, 0, p_un.stderr)
        un_res = json.loads(p_un.stdout)
        self.assertTrue(un_res["ok"])
        self.assertTrue(any(s["reason"] == "mcp_notebooklm_entry_modified_by_user" for s in un_res["skipped_modified"]))
        self.assertFalse(canonical.exists())
        self.assertFalse(agy_link.exists())
        self.assertTrue((self.home / ".notebooklm" / "profiles" / "default" / "storage_state.json").is_file())

    def test_fault_injection_mid_install_and_wal_rollback(self) -> None:
        p_fail = self._run_install(
            "--apply",
            "--non-interactive",
            "--json",
            "--allowed-roots",
            str(self.workspace),
            "--skip-warmup",
            extra_env={"NOTEBOOKLM_T3_FAIL_AFTER_STEP": "3"},
        )
        self.assertEqual(p_fail.returncode, 99)
        self.assertTrue((self.home / ".config" / "notebooklm-t3" / "manifest.json").is_file())

        p_rb = self._run_install("--rollback", "--json")
        self.assertEqual(p_rb.returncode, 0, p_rb.stderr)
        self.assertTrue(json.loads(p_rb.stdout)["ok"])
        self.assertFalse((self.home / ".agents" / "skills" / "notebooklm-t3").exists())
        self.assertFalse((self.home / ".config" / "notebooklm-t3" / "manifest.json").exists())

    def test_adopt_legacy_and_tarball_update_removes_obsolete_files(self) -> None:
        # 预置无 marker 的旧版 skill 目录与旧 plist
        canonical = self.home / ".agents" / "skills" / "notebooklm-t3"
        canonical.mkdir(parents=True)
        (canonical / "SKILL.md").write_text("legacy skill", encoding="utf-8")
        if platform.system() == "Darwin":
            la = self.home / "Library" / "LaunchAgents"
            la.mkdir(parents=True)
            (la / "com.legacy.notebooklm.plist").write_text("old", encoding="utf-8")

        # 不传 --adopt-legacy 时必须报 conflict 且零修改
        p_c = self._run_install("--plan", "--json", "--allowed-roots", str(self.workspace))
        self.assertEqual(p_c.returncode, 1)
        self.assertEqual(json.loads(p_c.stdout)["status"], "conflict")

        # 传 --adopt-legacy 接管并清理旧 plist
        p_a = self._run_install(
            "--apply",
            "--non-interactive",
            "--json",
            "--adopt-legacy",
            "--bootout-legacy-label",
            "com.legacy.notebooklm",
            "--allowed-roots",
            str(self.workspace),
            "--keepalive",
            "--skip-warmup",
        )
        self.assertEqual(p_a.returncode, 0, p_a.stderr)
        if platform.system() == "Darwin":
            self.assertFalse((self.home / "Library" / "LaunchAgents" / "com.legacy.notebooklm.plist").exists())

        # 在已安装的目录里塞入一个旧版文件记录到 marker，然后通过离线 tarball --update 验证自动清理过期文件
        marker_path = canonical / ".notebooklm-t3-managed.json"
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
        obsolete = canonical / "obsolete.txt"
        obsolete.write_text("old", encoding="utf-8")
        marker["files"]["obsolete.txt"] = "deadbeef"
        marker_path.write_text(json.dumps(marker), encoding="utf-8")

        # 用真实 build_release.py 构建 v0.2.0 tarball + SHA256SUMS，验证 --update 同时刷新 skill、src 缓存和 manifest
        release_dir = self.root / "dist"
        p_build = subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "installer" / "build_release.py"),
                "--tag",
                "v0.2.0",
                "--out-dir",
                str(release_dir),
                "--allow-version-mismatch",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(p_build.returncode, 0, p_build.stderr)
        tb_path = release_dir / "notebooklm-t3-v0.2.0.tar.gz"
        cs_path = release_dir / "SHA256SUMS"
        self.assertTrue(tb_path.is_file())
        self.assertTrue(cs_path.is_file())

        p_up = self._run_install("--update", "--tag", "v0.2.0", "--tarball", str(tb_path), "--checksums", str(cs_path), "--json")
        self.assertEqual(p_up.returncode, 0, p_up.stderr)
        up_res = json.loads(p_up.stdout)
        self.assertTrue(up_res["src_cache_updated"])
        self.assertFalse(obsolete.exists(), "--update must remove files no longer in new release")
        src_marker = json.loads((self.home / ".local" / "share" / "notebooklm-t3" / "src" / ".notebooklm-t3-managed.json").read_text(encoding="utf-8"))
        self.assertEqual(src_marker["tag"], "v0.2.0")


if __name__ == "__main__":
    unittest.main()
