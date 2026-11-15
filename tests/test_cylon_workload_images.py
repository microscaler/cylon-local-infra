from __future__ import annotations

import unittest
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
ROLE = REPO_ROOT / "roles" / "cylon_workload_images"


def tasks() -> list[dict]:
    """build.yml's tasks, blocks flattened (block, rescue and always)."""
    out: list[dict] = []

    def walk(items: list[dict]) -> None:
        for t in items:
            out.append(t)
            for key in ("block", "rescue", "always"):
                walk(t.get(key, []))

    walk(yaml.safe_load((ROLE / "tasks" / "build.yml").read_text()))
    return out


def named(fragment: str) -> dict:
    hits = [t for t in tasks() if fragment in t.get("name", "")]
    if len(hits) != 1:
        raise AssertionError(f"{len(hits)} tasks named like {fragment!r}")
    return hits[0]


class CylonWorkloadImagePushTests(unittest.TestCase):
    """The zot push is chunked, verified, and fails loudly (15 Nov 2026: a single-request push of the 5.7 GiB
    torch layer always hit zot's 60 s request timeout and the playbook only warned)."""

    def test_no_single_request_ctr_push(self) -> None:
        for t in tasks():
            argv = (t.get("ansible.builtin.command") or {}).get("argv", [])
            self.assertFalse("push" in argv and "ctr" in argv, f"ctr push in {t.get('name')}")

    def test_push_is_chunked_with_regctl(self) -> None:
        cfg = named("regctl config for zot")["ansible.builtin.command"]["argv"]
        self.assertIn("--blob-chunk", cfg)
        self.assertIn("--blob-max", cfg)
        self.assertEqual(cfg[cfg.index("--tls") + 1], "disabled")
        push = named("push (chunked)")["ansible.builtin.command"]["argv"]
        self.assertEqual(push[1:3], ["image", "import"])

    def test_regctl_download_is_checksummed(self) -> None:
        get = named(": regctl {{")["ansible.builtin.get_url"]
        self.assertTrue(get["checksum"].startswith("sha256:"))
        defaults = yaml.safe_load((ROLE / "defaults" / "main.yml").read_text())
        for arch, digest in defaults["cylon_regctl_sha256"].items():
            self.assertRegex(digest, r"^[0-9a-f]{64}$", arch)

    def test_push_failures_are_not_swallowed(self) -> None:
        for t in tasks():
            name = t.get("name", "")
            if "zot" in name or "push" in name or "regctl" in name or "export" in name:
                self.assertNotIn("failed_when", t, name)
                self.assertNotIn("ignore_errors", t, name)

    def test_manifest_is_read_back_from_zot(self) -> None:
        uri = named("manifest read back")["ansible.builtin.uri"]
        self.assertEqual(uri["method"], "GET")
        self.assertEqual(uri["status_code"], 200)
        self.assertIn("/manifests/{{ img_tag }}", uri["url"])


if __name__ == "__main__":
    unittest.main()
