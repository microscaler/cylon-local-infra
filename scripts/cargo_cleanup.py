#!/usr/bin/env python3
"""
cargo-cleanup — reclaim disk from Rust ``target/`` trees under a workspace root.

Runs ``cargo clean`` once per top-level Cargo manifest (workspace roots deduped).
Uses an explicit ``cargo`` binary path so cron/non-login shells work on ms02.

Usage:
    cargo-cleanup                    # ~/Workspace, dry-run off
    cargo-cleanup --dry-run
    cargo-cleanup --root ~/Workspace/microscaler/cylon
    cargo-cleanup --cargo /home/casibbald/.cargo/bin/cargo
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path


DEFAULT_CARGO = "/home/casibbald/.cargo/bin/cargo"
DEFAULT_ROOT = Path.home() / "Workspace"


def resolve_cargo(explicit: str | None) -> Path:
    if explicit:
        path = Path(explicit)
        if path.is_file() and os.access(path, os.X_OK):
            return path
        sys.exit(f"cargo-cleanup: not executable: {path}")

    for candidate in (
        Path(DEFAULT_CARGO),
        Path.home() / ".cargo" / "bin" / "cargo",
    ):
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate

    found = shutil.which("cargo")
    if found:
        return Path(found)

    sys.exit(
        "cargo-cleanup: cargo not found — pass --cargo or install rustup "
        f"(expected {DEFAULT_CARGO})"
    )


def find_cargo_roots(root: Path) -> list[Path]:
    """Return manifest directories, dropping nested members of workspaces."""
    if not root.is_dir():
        return []

    manifest_dirs = sorted({p.parent for p in root.rglob("Cargo.toml")})
    roots: list[Path] = []
    for directory in manifest_dirs:
        if any(directory != other and other in directory.parents for other in manifest_dirs):
            continue
        roots.append(directory)
    return sorted(roots)


def target_bytes(root: Path) -> int:
    target = root / "target"
    if not target.is_dir():
        return 0
    total = 0
    for dirpath, _dirnames, filenames in os.walk(target):
        for name in filenames:
            try:
                total += (Path(dirpath) / name).stat().st_size
            except OSError:
                pass
    return total


def human_size(num: int) -> str:
    step = 1024.0
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if num < step:
            return f"{num:.1f}{unit}"
        num /= step  # type: ignore[assignment]
    return f"{num:.1f}PiB"


def run_clean(cargo: Path, crate_root: Path, dry_run: bool) -> tuple[int, str]:
    before = target_bytes(crate_root)
    cmd = [str(cargo), "clean", "--manifest-path", str(crate_root / "Cargo.toml")]
    if dry_run:
        return before, " ".join(cmd)

    proc = subprocess.run(cmd, cwd=crate_root, capture_output=True, text=True)
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(err or f"exit {proc.returncode}")
    after = target_bytes(crate_root)
    return before - after, "ok"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run cargo clean on each top-level Rust workspace under ROOT.",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=DEFAULT_ROOT,
        help=f"Search root (default: {DEFAULT_ROOT})",
    )
    parser.add_argument(
        "--cargo",
        default=None,
        help=f"Path to cargo binary (default: {DEFAULT_CARGO} or ~/.cargo/bin/cargo)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print planned cleans without running cargo",
    )
    args = parser.parse_args(argv)

    root = args.root.expanduser().resolve()
    cargo = resolve_cargo(args.cargo)
    roots = find_cargo_roots(root)

    if not roots:
        print(f"cargo-cleanup: no Cargo.toml under {root}", file=sys.stderr)
        return 0

    print(f"cargo: {cargo}")
    print(f"root:  {root}")
    print(f"found {len(roots)} crate/workspace root(s)")
    if args.dry_run:
        print("mode:  dry-run")

    total_before = sum(target_bytes(r) for r in roots)
    total_freed = 0
    errors = 0

    for crate_root in roots:
        before = target_bytes(crate_root)
        label = crate_root.relative_to(root) if root in crate_root.parents else crate_root
        try:
            freed, detail = run_clean(cargo, crate_root, args.dry_run)
            total_freed += freed if not args.dry_run else 0
            if args.dry_run:
                print(f"  would clean {label} ({human_size(before)}) → {detail}")
            elif before > 0:
                print(f"  cleaned {label} freed {human_size(freed)}")
            else:
                print(f"  skipped {label} (no target/)")
        except RuntimeError as exc:
            errors += 1
            print(f"  ERROR {label}: {exc}", file=sys.stderr)

    print(
        f"done: {len(roots)} root(s), "
        f"target before {human_size(total_before)}"
        + (f", freed {human_size(total_freed)}" if not args.dry_run else "")
        + (f", {errors} error(s)" if errors else "")
    )
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
