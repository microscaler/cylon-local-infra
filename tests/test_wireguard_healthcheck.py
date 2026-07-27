from __future__ import annotations

import importlib.util
import subprocess
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


REPO_ROOT = Path(__file__).resolve().parents[1]
HEALTHCHECK_PATH = (
    REPO_ROOT / "roles" / "wireguard" / "files" / "wireguard_healthcheck.py"
)


def load_healthcheck_module():
    spec = importlib.util.spec_from_file_location(
        "wireguard_healthcheck", HEALTHCHECK_PATH
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {HEALTHCHECK_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class WireGuardHealthcheckTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module = load_healthcheck_module()

    def test_fresh_handshake_does_not_restart_interface(self) -> None:
        run = Mock(
            return_value=subprocess.CompletedProcess(
                args=[], returncode=0, stdout="peer-key\t950\n", stderr=""
            )
        )

        repaired = self.module.ensure_peer_health(
            interface="wg0",
            peer_key="peer-key",
            max_age_seconds=180,
            unit="wg-quick@wg0.service",
            now=1000,
            run=run,
        )

        self.assertFalse(repaired)
        run.assert_called_once_with(
            ["wg", "show", "wg0", "latest-handshakes"],
            check=False,
            capture_output=True,
            text=True,
        )

    def test_stale_handshake_restarts_interface(self) -> None:
        run = Mock(
            side_effect=[
                subprocess.CompletedProcess(
                    args=[], returncode=0, stdout="peer-key\t700\n", stderr=""
                ),
                subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
            ]
        )

        repaired = self.module.ensure_peer_health(
            interface="wg0",
            peer_key="peer-key",
            max_age_seconds=180,
            unit="wg-quick@wg0.service",
            now=1000,
            run=run,
        )

        self.assertTrue(repaired)
        self.assertEqual(
            run.call_args_list[1].args[0],
            ["systemctl", "restart", "wg-quick@wg0.service"],
        )

    def test_missing_interface_restarts_service(self) -> None:
        run = Mock(
            side_effect=[
                subprocess.CompletedProcess(
                    args=[], returncode=1, stdout="", stderr="Unable to access interface"
                ),
                subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
            ]
        )

        repaired = self.module.ensure_peer_health(
            interface="wg0",
            peer_key="peer-key",
            max_age_seconds=180,
            unit="wg-quick@wg0.service",
            now=1000,
            run=run,
        )

        self.assertTrue(repaired)

    def test_missing_peer_restarts_interface(self) -> None:
        run = Mock(
            side_effect=[
                subprocess.CompletedProcess(
                    args=[], returncode=0, stdout="different-peer\t950\n", stderr=""
                ),
                subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
            ]
        )

        repaired = self.module.ensure_peer_health(
            interface="wg0",
            peer_key="peer-key",
            max_age_seconds=180,
            unit="wg-quick@wg0.service",
            now=1000,
            run=run,
        )

        self.assertTrue(repaired)

    def test_restart_failure_is_reported(self) -> None:
        run = Mock(
            side_effect=[
                subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="bad"),
                subprocess.CompletedProcess(
                    args=[], returncode=1, stdout="", stderr="restart failed"
                ),
            ]
        )

        with self.assertRaisesRegex(RuntimeError, "restart failed"):
            self.module.ensure_peer_health(
                interface="wg0",
                peer_key="peer-key",
                max_age_seconds=180,
                unit="wg-quick@wg0.service",
                now=1000,
                run=run,
            )

    def test_invalid_handshake_timestamp_is_treated_as_missing(self) -> None:
        latest = self.module.latest_handshake_for_peer(
            "peer-key\tnot-a-timestamp\n", "peer-key"
        )
        self.assertIsNone(latest)

    def test_main_parses_cli_arguments_and_checks_peer(self) -> None:
        with patch.object(
            self.module, "ensure_peer_health", return_value=False
        ) as ensure:
            result = self.module.main(
                [
                    "--interface",
                    "wg0",
                    "--peer-key",
                    "peer-key",
                    "--max-age",
                    "180",
                    "--unit",
                    "wg-quick@wg0.service",
                ]
            )

        self.assertEqual(result, 0)
        ensure.assert_called_once_with(
            interface="wg0",
            peer_key="peer-key",
            max_age_seconds=180,
            unit="wg-quick@wg0.service",
        )


class WireGuardAnsibleContractTests(unittest.TestCase):
    def test_playbook_bootstraps_keys_before_configuring_peers(self) -> None:
        playbook = (REPO_ROOT / "playbooks" / "wireguard_mesh.yml").read_text()
        key_play = playbook.index("Bootstrap WireGuard keys")
        hub_play = playbook.index("Configure Hostinger WireGuard hub")
        peer_play = playbook.index("Configure ms02 WireGuard peer")
        self.assertLess(key_play, hub_play)
        self.assertLess(hub_play, peer_play)

    def test_systemd_timer_is_persistent(self) -> None:
        timer = (
            REPO_ROOT
            / "roles"
            / "wireguard"
            / "templates"
            / "wireguard-healthcheck.timer.j2"
        ).read_text()
        self.assertIn("Persistent=true", timer)
        self.assertIn("OnUnitActiveSec=", timer)

    def test_wireguard_service_is_enabled_by_role(self) -> None:
        tasks = (
            REPO_ROOT / "roles" / "wireguard" / "tasks" / "configure.yml"
        ).read_text()
        self.assertIn("enabled: true", tasks)
        self.assertIn("state: started", tasks)

    def test_check_mode_can_reuse_stored_public_key(self) -> None:
        key_tasks = (
            REPO_ROOT / "roles" / "wireguard" / "tasks" / "key.yml"
        ).read_text()
        self.assertIn("Read stored WireGuard public key", key_tasks)

    def test_hub_can_forward_between_wireguard_peers(self) -> None:
        template = (
            REPO_ROOT / "roles" / "wireguard" / "templates" / "wg0.conf.j2"
        ).read_text()
        playbook = (REPO_ROOT / "playbooks" / "wireguard_mesh.yml").read_text()
        self.assertIn("wireguard_forward_between_peers", template)
        self.assertIn("wireguard_forward_between_peers: true", playbook)


if __name__ == "__main__":
    unittest.main()
