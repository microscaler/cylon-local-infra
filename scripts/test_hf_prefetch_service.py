#!/usr/bin/env python3
"""Tests for hf_prefetch_service model routing."""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

_ROLE_PY = (
    Path(__file__).resolve().parent.parent
    / "roles/hf_prefetch_service/files/hf_prefetch_service.py"
)
_spec = importlib.util.spec_from_file_location("hf_prefetch_service", _ROLE_PY)
assert _spec and _spec.loader
_hf = importlib.util.module_from_spec(_spec)
sys.modules["hf_prefetch_service"] = _hf
_spec.loader.exec_module(_hf)


class ModelEntryTests(unittest.TestCase):
    def test_nfs_model_parses_empty_sync_targets(self) -> None:
        entry = _hf.ModelEntry.parse(
            {
                "repo": "MiniMaxAI/MiniMax-M3",
                "cache_dir": "/mnt/ms02-hf-archive/hub",
                "sync_targets": [],
            }
        )
        self.assertEqual(entry.repo, "MiniMaxAI/MiniMax-M3")
        self.assertEqual(entry.effective_cache_dir(Path("/local/hub")), Path("/mnt/ms02-hf-archive/hub"))
        self.assertEqual(entry.effective_sync_targets([_hf.SyncTarget(host="169.254.1.1")]), [])

    def test_local_model_inherits_defaults(self) -> None:
        entry = _hf.ModelEntry.parse({"repo": "moonshotai/Kimi-K2.7-Code"})
        default = Path("/home/nvidia/.cache/huggingface/hub")
        peer = _hf.SyncTarget(host="169.254.1.2", name="nvidia2")
        self.assertEqual(entry.effective_cache_dir(default), default)
        self.assertEqual(entry.effective_sync_targets([peer]), [peer])


if __name__ == "__main__":
    unittest.main()
