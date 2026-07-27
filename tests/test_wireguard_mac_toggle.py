from __future__ import annotations

import importlib.util
import subprocess
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


REPO_ROOT = Path(__file__).resolve().parents[1]
TOGGLE_PATH = (
    REPO_ROOT
    / "roles"
    / "wireguard_mac"
    / "files"
    / "microscaler_wireguard.py"
)


def load_toggle_module():
    spec = importlib.util.spec_from_file_location(
        "microscaler_wireguard", TOGGLE_PATH
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {TOGGLE_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def completed(returncode: int = 0, stdout: str = "", stderr: str = ""):
    return subprocess.CompletedProcess(
        args=[], returncode=returncode, stdout=stdout, stderr=stderr
    )


class MacWireGuardToggleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module = load_toggle_module()

    def test_up_disables_lan_route_before_starting_wireguard(self) -> None:
        run = Mock(
            side_effect=[
                completed(),
                completed(),
                completed(),
                completed(stdout="utun8\n"),
            ]
        )

        self.module.activate(run=run, require_root=False)

        commands = [call.args[0] for call in run.call_args_list]
        self.assertEqual(
            commands[:3],
            [
                [
                    "/bin/launchctl",
                    "bootout",
                    "system",
                    self.module.LAN_ROUTE_PLIST,
                ],
                [
                    "/sbin/route",
                    "-n",
                    "delete",
                    "-net",
                    self.module.METALLB_CIDR,
                    self.module.LAN_GATEWAY,
                ],
                [
                    self.module.WG_QUICK,
                    "up",
                    self.module.WIREGUARD_CONFIG,
                ],
            ],
        )

    def test_failed_up_restores_lan_route_daemon(self) -> None:
        run = Mock(
            side_effect=[
                completed(),
                completed(),
                completed(returncode=1, stderr="wg failed"),
                completed(),
                completed(),
                completed(),
            ]
        )

        with self.assertRaisesRegex(RuntimeError, "wg failed"):
            self.module.activate(run=run, require_root=False)

        commands = [call.args[0] for call in run.call_args_list]
        self.assertIn(
            [
                "/bin/launchctl",
                "bootstrap",
                "system",
                self.module.LAN_ROUTE_PLIST,
            ],
            commands,
        )
        self.assertIn(
            [
                "/bin/launchctl",
                "kickstart",
                "-k",
                f"system/{self.module.LAN_ROUTE_LABEL}",
            ],
            commands,
        )

    def test_down_restores_lan_route_even_if_wireguard_is_already_down(self) -> None:
        run = Mock(
            side_effect=[
                completed(returncode=1, stderr="not active"),
                completed(),
                completed(),
                completed(),
            ]
        )

        self.module.deactivate(run=run, require_root=False)

        commands = [call.args[0] for call in run.call_args_list]
        self.assertEqual(
            commands[0],
            [
                self.module.WG_QUICK,
                "down",
                self.module.WIREGUARD_CONFIG,
            ],
        )
        self.assertIn(
            [
                "/sbin/route",
                "-n",
                "add",
                "-net",
                self.module.METALLB_CIDR,
                self.module.LAN_GATEWAY,
            ],
            commands,
        )

    def test_status_reports_wireguard_interfaces(self) -> None:
        run = Mock(return_value=completed(stdout="utun8\n"))
        output = self.module.status(run=run)
        self.assertEqual(output, "utun8")

    def test_activation_requires_root_by_default(self) -> None:
        with patch.object(self.module.os, "geteuid", return_value=502):
            with self.assertRaisesRegex(PermissionError, "run with sudo"):
                self.module.activate()

    def test_main_dispatches_status(self) -> None:
        with patch.object(self.module, "status", return_value="utun8"):
            result = self.module.main(["status"])
        self.assertEqual(result, 0)


class MacWireGuardAnsibleContractTests(unittest.TestCase):
    def test_mesh_bootstraps_mac_key_before_hub_configuration(self) -> None:
        playbook = (REPO_ROOT / "playbooks" / "wireguard_mesh.yml").read_text()
        mac_key = playbook.index("Bootstrap Mac WireGuard key")
        hub = playbook.index("Configure Hostinger WireGuard hub")
        self.assertLess(mac_key, hub)
        self.assertIn("Configure on-demand Mac WireGuard peer", playbook)

    def test_hub_declares_mac_peer(self) -> None:
        playbook = (REPO_ROOT / "playbooks" / "wireguard_mesh.yml").read_text()
        self.assertIn("name: picolino", playbook)
        self.assertIn("wireguard_mac_public_key", playbook)

    def test_check_mode_can_reuse_stored_mac_public_key(self) -> None:
        tasks = (
            REPO_ROOT / "roles" / "wireguard_mac" / "tasks" / "main.yml"
        ).read_text()
        self.assertIn("Read stored Mac WireGuard public key", tasks)

    def test_mac_config_is_split_tunnel_only(self) -> None:
        template = (
            REPO_ROOT
            / "roles"
            / "wireguard_mac"
            / "templates"
            / "ms02-away.conf.j2"
        ).read_text()
        self.assertIn("10.77.0.0/24", template)
        self.assertIn("192.168.1.189/32", template)
        self.assertIn("10.177.76.0/24", template)
        self.assertNotIn("0.0.0.0/0", template)

    def test_ms02_allows_vpn_services_and_nfs_export(self) -> None:
        host_vars = (
            REPO_ROOT / "inventory" / "host_vars" / "ms02.yml"
        ).read_text()
        self.assertIn('firewall_trusted_vpn_cidr: "10.77.0.3/32"', host_vars)
        self.assertIn("cidr: 10.77.0.3/32", host_vars)
        self.assertIn("firewall_trusted_vpn_tcp_ports:", host_vars)

    def test_ms02_routes_vpn_peer_to_metallb_bridge(self) -> None:
        host_vars = (
            REPO_ROOT / "inventory" / "host_vars" / "ms02.yml"
        ).read_text()
        route_tasks = (
            REPO_ROOT / "roles" / "metallb_lan_route" / "tasks" / "main.yml"
        ).read_text()
        self.assertIn("metallb_lan_route_additional_source_cidrs", host_vars)
        self.assertIn("metallb_lan_route_additional_source_cidrs", route_tasks)

    def test_mac_role_installs_route_daemon_without_shell(self) -> None:
        tasks = (
            REPO_ROOT / "roles" / "wireguard_mac" / "tasks" / "main.yml"
        ).read_text()
        plist = (
            REPO_ROOT
            / "roles"
            / "wireguard_mac"
            / "templates"
            / "com.microscaler.metallb-lan-route.plist.j2"
        ).read_text()
        self.assertIn("Install Mac LAN route LaunchDaemon", tasks)
        self.assertIn("<string>/sbin/route</string>", plist)
        self.assertNotIn("/bin/bash", plist)


if __name__ == "__main__":
    unittest.main()
