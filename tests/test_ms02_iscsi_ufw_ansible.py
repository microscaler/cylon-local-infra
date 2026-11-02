from __future__ import annotations

import unittest
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULTS = REPO_ROOT / "roles" / "firewall" / "defaults" / "main.yml"
TASKS = REPO_ROOT / "roles" / "firewall" / "tasks" / "main.yml"
MS02 = REPO_ROOT / "inventory" / "host_vars" / "ms02.yml"


class Ms02IscsiUfwAnsibleTests(unittest.TestCase):
    def test_defaults_declare_bridge_cidr_knobs(self) -> None:
        data = yaml.safe_load(DEFAULTS.read_text())
        self.assertEqual(data["firewall_trusted_bridge_cidr"], "")
        self.assertEqual(data["firewall_trusted_bridge_tcp_ports"], [])

    def test_role_applies_bridge_tcp_allows(self) -> None:
        text = TASKS.read_text()
        self.assertIn("firewall_trusted_bridge_cidr", text)
        self.assertIn("firewall_trusted_bridge_tcp_ports", text)

    def test_ms02_opens_iscsi_from_multipass_bridge(self) -> None:
        data = yaml.safe_load(MS02.read_text())
        self.assertEqual(data["firewall_trusted_bridge_cidr"], "10.177.76.0/24")
        self.assertIn(3260, data["firewall_trusted_bridge_tcp_ports"])


if __name__ == "__main__":
    unittest.main()
