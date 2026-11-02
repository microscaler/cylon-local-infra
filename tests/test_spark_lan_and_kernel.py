from __future__ import annotations

import unittest
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
SPARKS = REPO_ROOT / "inventory" / "group_vars" / "sparks.yml"
NVIDIA1 = REPO_ROOT / "inventory" / "host_vars" / "nvidia1.yml"
NVIDIA2 = REPO_ROOT / "inventory" / "host_vars" / "nvidia2.yml"
NVIDIA3 = REPO_ROOT / "inventory" / "host_vars" / "nvidia3.yml"
NVIDIA4 = REPO_ROOT / "inventory" / "host_vars" / "nvidia4.yml"
MS02 = REPO_ROOT / "inventory" / "host_vars" / "ms02.yml"
LAN_TASKS = REPO_ROOT / "roles" / "spark_lan_address" / "tasks" / "main.yml"
LAN_DEFAULTS = REPO_ROOT / "roles" / "spark_lan_address" / "defaults" / "main.yml"
LAN_PLAYBOOK = REPO_ROOT / "playbooks" / "spark_lan_address.yml"
FLEET_KERNEL = "7.0.0-1024-nvidia"


class SparkLanAndKernelTests(unittest.TestCase):
    def test_fleet_kernel_pin_is_the_7_0_image_every_spark_can_install(self) -> None:
        data = yaml.safe_load(SPARKS.read_text())
        self.assertEqual(data["spark_kernel_pin"], FLEET_KERNEL)
        self.assertTrue(data["spark_kernel_apt_hold"])
        for host_vars in (NVIDIA3, NVIDIA4):
            self.assertNotIn("spark_provision_kernel: false", host_vars.read_text())

    def test_nvidia1_and_nvidia2_lan_addresses(self) -> None:
        self.assertEqual(yaml.safe_load(NVIDIA1.read_text())["cylon_node_lan_ip"], "192.168.1.110")
        self.assertEqual(yaml.safe_load(NVIDIA2.read_text())["cylon_node_lan_ip"], "192.168.1.120")

    def test_hf_archive_exports_follow_the_new_leader_addresses(self) -> None:
        text = MS02.read_text()
        data = yaml.safe_load(text)
        self.assertEqual(data["hf_archive_ssh_from"], "192.168.1.110")
        cidrs = []
        for export in data["nfs_server_exports"]:
            if export["path"] == "/srv/hf-archive":
                cidrs = [client["cidr"] for client in export["clients"]]
        self.assertEqual(
            cidrs,
            [
                "192.168.1.110/32",
                "192.168.1.120/32",
                "192.168.1.130/32",
                "192.168.1.140/32",
            ],
        )
        self.assertNotIn("192.168.1.104/32", text)
        self.assertNotIn("192.168.1.229/32", text)

    def test_lan_role_sets_nm_manual_address_from_inventory(self) -> None:
        defaults = yaml.safe_load(LAN_DEFAULTS.read_text())
        tasks = LAN_TASKS.read_text()
        playbook = LAN_PLAYBOOK.read_text()
        self.assertEqual(defaults["spark_lan_gateway"], "192.168.1.1")
        self.assertEqual(defaults["spark_lan_prefix"], 24)
        self.assertIn("cylon_node_lan_ip", tasks)
        self.assertIn("ipv4.method", tasks)
        self.assertIn("manual", tasks)
        self.assertIn("nmcli", tasks)
        self.assertIn("connection", tasks)
        self.assertIn("async:", tasks)
        self.assertIn("spark_lan_address", playbook)
