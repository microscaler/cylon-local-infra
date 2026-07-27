from __future__ import annotations

import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


class SunshineRemoteDesktopAnsibleTests(unittest.TestCase):
    def test_role_installs_pinned_official_sunshine_package(self) -> None:
        defaults = (
            REPO_ROOT / "roles" / "sunshine_remote_desktop" / "defaults" / "main.yml"
        ).read_text()
        tasks = (
            REPO_ROOT / "roles" / "sunshine_remote_desktop" / "tasks" / "main.yml"
        ).read_text()
        self.assertIn("v2026.516.143833", defaults)
        self.assertIn("sunshine-debian-trixie-amd64.deb", defaults)
        self.assertIn("sha256:", defaults)
        self.assertIn("Install pinned Sunshine package", tasks)

    def test_role_builds_headless_xorg_xfce_session_without_autologin(self) -> None:
        tasks = (
            REPO_ROOT / "roles" / "sunshine_remote_desktop" / "tasks" / "main.yml"
        ).read_text()
        xorg = (
            REPO_ROOT
            / "roles"
            / "sunshine_remote_desktop"
            / "templates"
            / "xorg-dummy.conf.j2"
        ).read_text()
        desktop_unit = (
            REPO_ROOT
            / "roles"
            / "sunshine_remote_desktop"
            / "templates"
            / "sunshine-virtual-desktop.service.j2"
        ).read_text()
        self.assertIn("xserver-xorg-video-dummy", tasks)
        self.assertIn('Driver "dummy"', xorg)
        self.assertIn("xfce4-session", desktop_unit)
        self.assertNotIn("AutomaticLogin", tasks)

    def test_xorg_accepts_sunshine_virtual_input_devices(self) -> None:
        tasks = (
            REPO_ROOT / "roles" / "sunshine_remote_desktop" / "tasks" / "main.yml"
        ).read_text()
        xorg = (
            REPO_ROOT
            / "roles"
            / "sunshine_remote_desktop"
            / "templates"
            / "xorg-dummy.conf.j2"
        ).read_text()
        self.assertIn("xserver-xorg-input-libinput", tasks)
        self.assertNotIn('Option "AutoAddDevices" "false"', xorg)

    def test_sunshine_mouse_speed_is_halved(self) -> None:
        defaults = (
            REPO_ROOT / "roles" / "sunshine_remote_desktop" / "defaults" / "main.yml"
        ).read_text()
        xorg = (
            REPO_ROOT
            / "roles"
            / "sunshine_remote_desktop"
            / "templates"
            / "xorg-dummy.conf.j2"
        ).read_text()
        self.assertIn("sunshine_remote_desktop_mouse_accel_speed: -0.5", defaults)
        self.assertIn('MatchProduct "Mouse passthrough"', xorg)
        self.assertIn('Option "AccelSpeed"', xorg)

    def test_services_restart_and_start_at_boot(self) -> None:
        sunshine_unit = (
            REPO_ROOT
            / "roles"
            / "sunshine_remote_desktop"
            / "templates"
            / "sunshine-headless.service.j2"
        ).read_text()
        tasks = (
            REPO_ROOT / "roles" / "sunshine_remote_desktop" / "tasks" / "main.yml"
        ).read_text()
        self.assertIn("Restart=on-failure", sunshine_unit)
        self.assertIn("enabled: true", tasks)
        self.assertIn("state: started", tasks)

    def test_services_do_not_embed_shell_scripts(self) -> None:
        for name in (
            "sunshine-virtual-xorg.service.j2",
            "sunshine-virtual-desktop.service.j2",
            "sunshine-headless.service.j2",
        ):
            unit = (
                REPO_ROOT
                / "roles"
                / "sunshine_remote_desktop"
                / "templates"
                / name
            ).read_text()
            self.assertNotIn("/bin/bash", unit)
            self.assertNotIn("/bin/sh", unit)

    def test_ms02_enables_sunshine_and_vpn_only_ports(self) -> None:
        host_vars = (
            REPO_ROOT / "inventory" / "host_vars" / "ms02.yml"
        ).read_text()
        self.assertIn("sunshine_remote_desktop_enabled: true", host_vars)
        self.assertIn("47990", host_vars)
        self.assertIn("47998:48000", host_vars)
        self.assertIn("WireGuard Sunshine", host_vars)

    def test_dev_hosts_playbook_includes_sunshine_role(self) -> None:
        playbook = (REPO_ROOT / "playbooks" / "dev_hosts.yml").read_text()
        self.assertIn("role: sunshine_remote_desktop", playbook)

    def test_sunshine_uses_x11_capture_and_intel_vaapi(self) -> None:
        config = (
            REPO_ROOT
            / "roles"
            / "sunshine_remote_desktop"
            / "templates"
            / "sunshine.conf.j2"
        ).read_text()
        defaults = (
            REPO_ROOT / "roles" / "sunshine_remote_desktop" / "defaults" / "main.yml"
        ).read_text()
        self.assertIn("capture = x11", config)
        self.assertIn("encoder = vaapi", config)
        self.assertIn("sunshine_remote_desktop_dri_device", config)
        self.assertIn("/dev/dri/renderD128", defaults)


if __name__ == "__main__":
    unittest.main()
