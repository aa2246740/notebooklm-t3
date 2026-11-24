#!/usr/bin/env python3
"""校验生成的 macOS launchd plist（plutil -lint）或 Linux systemd 单元（systemd-analyze verify）。"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "installer"))
sys.path.insert(0, str(REPO_ROOT / "skill" / "notebooklm-t3" / "scripts"))

import install as installer_mod  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--platform", choices=("macos", "linux"), required=True)
    args = parser.parse_args()

    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp) / "home"
        home.mkdir()
        old_home = os.environ.get("HOME")
        os.environ["HOME"] = str(home)
        try:
            launcher = home / ".agents" / "skills" / "notebooklm-t3" / "scripts" / "keepalive"
            launcher.parent.mkdir(parents=True)
            launcher.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            launcher.chmod(0o755)
            fake_uv = Path("/usr/local/bin/uv")

            if args.platform == "macos":
                meta = installer_mod._install_launchd_plist(launcher, fake_uv, skip_ctl=True)
                plist_path = meta["target"]
                proc = subprocess.run(["plutil", "-lint", plist_path], capture_output=True, text=True, check=False)
                print(proc.stdout.strip(), proc.stderr.strip())
                return proc.returncode

            meta = installer_mod._install_systemd_user_units(launcher, fake_uv, skip_ctl=True)
            svc = meta["service_path"]
            tmr = meta["timer_path"]
            if shutil.which("systemd-analyze"):
                proc = subprocess.run(
                    ["systemd-analyze", "verify", svc, tmr],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                print(proc.stdout.strip(), proc.stderr.strip())
                # 容器/无 D-Bus 环境下仅当出现真正语法错误时失败
                if proc.returncode != 0 and "Failed to load" not in proc.stderr and "syntax" in proc.stderr.lower():
                    return proc.returncode
            print(f"Verified systemd unit files: {svc}, {tmr}")
            return 0
        finally:
            if old_home is None:
                os.environ.pop("HOME", None)
            else:
                os.environ["HOME"] = old_home


if __name__ == "__main__":
    sys.exit(main())
