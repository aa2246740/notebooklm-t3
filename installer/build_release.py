#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""构建与 README、INSTALL_FOR_AGENTS 与 install.py --update 严格对齐的 Release 发布包。

产物：
  <out_dir>/notebooklm-t3-<TAG>.tar.gz  （顶层目录名严格为 notebooklm-t3-<TAG>/）
  <out_dir>/SHA256SUMS
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import tarfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
INCLUDE_PATHS = (
    "VERSION",
    "LICENSE",
    "README.md",
    "README.zh-CN.md",
    "install.sh",
    "uninstall.sh",
    "installer",
    "docs",
    "skill",
)
SKIP_PARTS = {"__pycache__", ".pytest_cache", ".git", "notebooklm-outputs"}


def build_release(
    tag: str,
    out_dir: Path,
    repo_root: Path = REPO_ROOT,
    *,
    allow_version_mismatch: bool = False,
) -> tuple[Path, Path]:
    if not tag.startswith("v"):
        raise ValueError(f"Release tag must start with 'v' (got {tag!r})")
    version_file = repo_root / "VERSION"
    if version_file.is_file() and not allow_version_mismatch:
        declared = version_file.read_text(encoding="utf-8").strip()
        if declared != tag[1:]:
            raise ValueError(f"VERSION ({declared!r}) does not match tag ({tag!r}); update VERSION first.")
    out_dir.mkdir(parents=True, exist_ok=True)
    prefix = f"notebooklm-t3-{tag}"
    tarball_path = out_dir / f"{prefix}.tar.gz"
    checksums_path = out_dir / "SHA256SUMS"

    def _filter(info: tarfile.TarInfo) -> tarfile.TarInfo | None:
        parts = set(Path(info.name).parts)
        if parts & SKIP_PARTS or info.name.endswith((".pyc", ".pyo", ".DS_Store")):
            return None
        info.uid = 0
        info.gid = 0
        info.uname = ""
        info.gname = ""
        return info

    with tarfile.open(tarball_path, "w:gz") as tf:
        for rel in INCLUDE_PATHS:
            src = repo_root / rel
            if not src.exists():
                raise FileNotFoundError(f"Required release path missing: {src}")
            tf.add(src, arcname=f"{prefix}/{rel}", filter=_filter)

    digest = hashlib.sha256(tarball_path.read_bytes()).hexdigest()
    checksums_path.write_text(f"{digest}  {tarball_path.name}\n", encoding="utf-8")
    return tarball_path, checksums_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build notebooklm-t3 release tarball and SHA256SUMS")
    parser.add_argument("--tag", required=True, help="Release tag (e.g. v0.1.0)")
    parser.add_argument("--out-dir", default=str(REPO_ROOT / "dist"), help="Output directory")
    parser.add_argument(
        "--allow-version-mismatch",
        action="store_true",
        help="Skip VERSION vs tag check (for offline test packaging)",
    )
    args = parser.parse_args(argv)

    tb, cs = build_release(
        args.tag,
        Path(args.out_dir).expanduser().resolve(),
        allow_version_mismatch=args.allow_version_mismatch,
    )
    print(f"Built release archive: {tb}")
    print(f"Built checksums file:  {cs}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
