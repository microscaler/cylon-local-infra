#!/usr/bin/env python3
"""Tests for cargo_cleanup.py"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from cargo_cleanup import find_cargo_roots, human_size, resolve_cargo


class FindCargoRootsTests(unittest.TestCase):
    def test_deduplicates_workspace_members(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "ws" / "crates" / "foo").mkdir(parents=True)
            (root / "solo").mkdir()
            (root / "ws" / "Cargo.toml").write_text(
                '[workspace]\nmembers = ["crates/foo"]\n', encoding="utf-8"
            )
            (root / "ws" / "crates" / "foo" / "Cargo.toml").write_text(
                '[package]\nname = "foo"\nversion = "0.1.0"\n',
                encoding="utf-8",
            )
            (root / "solo" / "Cargo.toml").write_text(
                '[package]\nname = "solo"\nversion = "0.1.0"\n', encoding="utf-8"
            )

            roots = find_cargo_roots(root)
            rel = sorted(p.relative_to(root).as_posix() for p in roots)
            self.assertEqual(rel, ["solo", "ws"])

    def test_empty_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(find_cargo_roots(Path(tmp)), [])


class ResolveCargoTests(unittest.TestCase):
    def test_explicit_path_required_executable(self) -> None:
        with tempfile.NamedTemporaryFile(suffix="cargo") as fh:
            path = Path(fh.name)
            path.chmod(0o755)
            self.assertEqual(resolve_cargo(str(path)), path)


class HumanSizeTests(unittest.TestCase):
    def test_gib(self) -> None:
        self.assertIn("GiB", human_size(2 * 1024**3))


if __name__ == "__main__":
    unittest.main()
