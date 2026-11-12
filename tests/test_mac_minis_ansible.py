"""Guard rails for the Mac mini dev-machine Ansible (playbooks/mac_dev.yml)."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
INVENTORY = REPO_ROOT / "inventory" / "hosts.yml"
GROUP_VARS = REPO_ROOT / "inventory" / "group_vars"
HOST_VARS = REPO_ROOT / "inventory" / "host_vars"
PLAYBOOKS = REPO_ROOT / "playbooks"
ROLES = REPO_ROOT / "roles"
MAC_DEV = PLAYBOOKS / "mac_dev.yml"

MAC_DEV_ROLES = [
    "mac_base",
    "homebrew",
    "shell_env",
    "rust",
    "golang",
    "python",
    "node",
    "docker_desktop",
    "claude",
    "hermes",
    "tailscale",
    "dev_workspace",
]
MINIS = ["mini1", "mini2", "mini3"]
LINUX_GROUPS = [
    "sparks",
    "cylon_nodes",
    "dev_hosts",
    "tunnel_hubs",
    "wireguard_linux_nodes",
    "wireguard_mesh",
]
# Version-ish defaults must be exact pins, never a moving channel.
FLOATING = re.compile(r"^(latest|stable|main|master|head|nightly|lts|current|\*)$", re.I)


def load(path: Path):
    return yaml.safe_load(path.read_text())


def groups_tree() -> dict:
    return load(INVENTORY)["all"]["children"]


def members(group: str, tree: dict | None = None) -> set[str]:
    tree = tree or groups_tree()
    node = tree.get(group) or {}
    hosts = set((node.get("hosts") or {}).keys())
    for child in (node.get("children") or {}).keys():
        hosts |= members(child, tree)
    return hosts


def plays(path: Path) -> list[dict]:
    data = load(path) or []
    return [p for p in data if isinstance(p, dict) and "hosts" in p]


def iter_tasks(tasks):
    for task in tasks or []:
        if not isinstance(task, dict):
            continue
        yield task
        for key in ("block", "rescue", "always"):
            yield from iter_tasks(task.get(key))


def role_task_files(role: str) -> list[Path]:
    return sorted((ROLES / role / "tasks").glob("*.yml"))


class InventoryTests(unittest.TestCase):
    def test_minis_are_their_own_group_over_ssh(self) -> None:
        tree = groups_tree()
        group = tree["mac_minis"]
        self.assertEqual(set(group["hosts"]), set(MINIS))
        self.assertEqual(group["vars"]["ansible_connection"], "ssh")
        self.assertEqual(group["vars"]["ansible_user"], "casibbald")
        self.assertTrue(group["vars"]["ansible_python_interpreter"].startswith("/"))

    def test_minis_are_in_no_other_group(self) -> None:
        tree = groups_tree()
        for group in tree:
            if group == "mac_minis":
                continue
            self.assertFalse(
                members(group, tree) & set(MINIS),
                f"mac minis leaked into group {group}",
            )

    def test_picolino_is_not_a_mini(self) -> None:
        self.assertIn("picolino", members("macs"))
        self.assertNotIn("picolino", members("mac_minis"))

    def test_host_vars_are_placeholders_on_the_spark_lan(self) -> None:
        roles = set()
        for mini in MINIS:
            data = load(HOST_VARS / f"{mini}.yml")
            self.assertRegex(data["ansible_host"], r"^192\.168\.1\.\d{1,3}$")
            roles.add(data["mac_mini_role"])
            self.assertIn("PLACEHOLDER", (HOST_VARS / f"{mini}.yml").read_text())
        self.assertEqual(roles, {"speed", "feature", "training"})

    def test_minis_are_added_to_picolino_etc_hosts(self) -> None:
        text = (ROLES / "mac_workstation" / "defaults" / "main.yml").read_text()
        data = yaml.safe_load(text)
        self.assertIn("mac_workstation_etc_hosts_mac_mini_lines", data["mac_workstation_etc_hosts_lines"])
        self.assertIn("groups.get('mac_minis'", data["mac_workstation_etc_hosts_mac_mini_lines"])
        self.assertIn("192.168.1.189 ms02", data["mac_workstation_etc_hosts_static_lines"])

    def test_hermes_endpoint_comes_from_the_ms02_vars(self) -> None:
        data = load(GROUP_VARS / "mac_minis.yml")
        self.assertIn("hostvars['ms02']['hermes_agent_openai_base_url']", data["hermes_agent_openai_base_url"])
        self.assertIn("hostvars['ms02']['hermes_agent_openai_model_id']", data["hermes_agent_openai_model_id"])
        dev = load(GROUP_VARS / "dev_hosts.yml")
        self.assertIn("hermes_agent_openai_base_url", dev)


class PlaybookTargetingTests(unittest.TestCase):
    def test_no_other_playbook_targets_the_minis(self) -> None:
        for path in sorted(PLAYBOOKS.glob("*.yml")):
            if path == MAC_DEV:
                continue
            for play in plays(path):
                hosts = str(play["hosts"])
                self.assertNotIn("mac_minis", hosts, path.name)
                self.assertNotIn(hosts.strip(), ("all", "*"), f"{path.name} targets every host")
                for mini in MINIS:
                    self.assertNotRegex(hosts, rf"\b{mini}\b", path.name)

    def test_linux_plays_never_resolve_to_a_mini(self) -> None:
        tree = groups_tree()
        for group in LINUX_GROUPS:
            self.assertFalse(members(group, tree) & set(MINIS), group)

    def test_mac_dev_only_targets_mac_minis(self) -> None:
        for play in plays(MAC_DEV):
            self.assertEqual(play["hosts"], "mac_minis")

    def test_macs_never_get_mac_dev_roles(self) -> None:
        for path in sorted(PLAYBOOKS.glob("*.yml")):
            if path == MAC_DEV:
                continue
            text = path.read_text()
            for role in MAC_DEV_ROLES:
                self.assertNotRegex(
                    text,
                    rf"(role:|name:|-)\s*{role}\s*$",
                    f"{path.name} pulls in mac_dev role {role}",
                )

    def test_mac_dev_runs_every_role_in_order_with_tags(self) -> None:
        provision = [p for p in plays(MAC_DEV) if p.get("roles")][0]
        names = [r["role"] for r in provision["roles"]]
        self.assertEqual(names, ["mac_base", "shell_env", "homebrew", *MAC_DEV_ROLES[3:]])
        for entry in provision["roles"]:
            self.assertIn("mac_dev", entry["tags"])
            self.assertIn(entry["role"], entry["tags"])

    def test_mac_dev_asserts_darwin_and_not_local(self) -> None:
        text = MAC_DEV.read_text()
        self.assertIn("ansible_facts['system'] == 'Darwin'", text)
        self.assertIn("inventory_hostname not in groups.get('macs', [])", text)


class RoleConventionTests(unittest.TestCase):
    def test_every_role_has_defaults_tasks_readme(self) -> None:
        for role in MAC_DEV_ROLES:
            for rel in ("defaults/main.yml", "tasks/main.yml", "README.md"):
                self.assertTrue((ROLES / role / rel).is_file(), f"{role}/{rel}")

    def test_every_task_is_tagged(self) -> None:
        for role in MAC_DEV_ROLES:
            for path in role_task_files(role):
                for task in iter_tasks(load(path)):
                    self.assertIn("tags", task, f"{role}/{path.name}: {task.get('name')}")

    def test_every_role_asserts_darwin(self) -> None:
        for role in MAC_DEV_ROLES:
            text = (ROLES / role / "tasks" / "main.yml").read_text()
            self.assertIn('ansible_facts["system"] == "Darwin"', text, role)

    def test_become_is_per_task_only(self) -> None:
        for play in plays(MAC_DEV):
            self.assertNotIn("become", play)
        for role in MAC_DEV_ROLES:
            for path in role_task_files(role):
                for task in iter_tasks(load(path)):
                    if "block" in task:
                        self.assertNotIn("become", task, f"{role}: block-level become")

    def test_no_floating_package_states(self) -> None:
        for role in MAC_DEV_ROLES:
            for path in role_task_files(role):
                self.assertNotIn("state: latest", path.read_text(), f"{role}/{path.name}")

    def test_versions_are_pinned(self) -> None:
        expected = {
            "homebrew": ["homebrew_version"],
            "rust": ["rust_rustup_version", "rust_default_toolchain"],
            "golang": ["golang_gvm_commit", "golang_default_version"],
            "python": ["python_pyenv_version", "python_global_version", "python_uv_version"],
            "node": ["node_fnm_version", "node_default_version"],
            "claude": ["claude_version"],
            "hermes": ["hermes_commit", "hermes_release_tag"],
        }
        for role, keys in expected.items():
            defaults = load(ROLES / role / "defaults" / "main.yml")
            for key in keys:
                value = str(defaults[key])
                self.assertNotRegex(value, FLOATING, f"{role}.{key}")
                self.assertRegex(value, r"\d", f"{role}.{key} must name an exact version")
        for role in MAC_DEV_ROLES:
            defaults = load(ROLES / role / "defaults" / "main.yml") or {}
            for key, value in defaults.items():
                if key.endswith(("_version", "_commit")):
                    self.assertNotRegex(str(value), FLOATING, f"{role}.{key}")
        self.assertRegex(load(ROLES / "golang" / "defaults" / "main.yml")["golang_gvm_commit"], r"^[0-9a-f]{40}$")
        self.assertRegex(load(ROLES / "hermes" / "defaults" / "main.yml")["hermes_commit"], r"^[0-9a-f]{40}$")
        for tool in load(ROLES / "rust" / "defaults" / "main.yml")["rust_cargo_tools"]:
            self.assertRegex(tool["version"], r"^\d+\.\d+\.\d+$", tool["name"])
        self.assertIn("cargo-nextest", [t["name"] for t in load(ROLES / "rust" / "defaults" / "main.yml")["rust_cargo_tools"]])

    def test_brewfile_lists_the_required_cli_tools(self) -> None:
        brewfile = (ROLES / "homebrew" / "files" / "Brewfile").read_text()
        entries = set(re.findall(r'^(?:brew|cask) "([^"]+)"', brewfile, re.M))
        required = {
            "git", "gh", "just", "jq", "ripgrep", "fd", "cmake", "pkgconf", "openssl@3",
            "protobuf", "libpq", "sccache", "kubernetes-cli", "kubectx", "helm", "flux",
            "sops", "age", "tilt",
        }
        self.assertFalse(required - entries, f"missing from Brewfile: {required - entries}")

    def test_secrets_only_come_from_the_runtime_environment(self) -> None:
        ts = load(ROLES / "tailscale" / "defaults" / "main.yml")
        self.assertIn("lookup('ansible.builtin.env', 'TAILSCALE_AUTH_KEY')", ts["tailscale_auth_key"])
        hm = load(ROLES / "hermes" / "defaults" / "main.yml")
        self.assertIn("lookup('ansible.builtin.env', 'HERMES_OPENAI_API_KEY')", hm["hermes_agent_openai_api_key"])
        for role in ("tailscale", "hermes"):
            text = (ROLES / role / "tasks" / "main.yml").read_text()
            self.assertIn("no_log: true", text, role)

    def test_docker_licence_is_never_accepted(self) -> None:
        for role in MAC_DEV_ROLES:
            for path in (ROLES / role).rglob("*"):
                if path.is_file():
                    self.assertNotIn("--accept-license", path.read_text(), str(path))
                    self.assertNotIn("--agree-to-license", path.read_text(), str(path))


class ZshrcRuleTests(unittest.TestCase):
    ZSHRC = re.compile(r"\.zshrc(?!\.d)")

    def test_only_shell_env_touches_zshrc(self) -> None:
        for role in MAC_DEV_ROLES:
            if role == "shell_env":
                continue
            for path in (ROLES / role).rglob("*"):
                if path.is_file():
                    self.assertIsNone(
                        self.ZSHRC.search(path.read_text()),
                        f"{path.relative_to(REPO_ROOT)} references ~/.zshrc",
                    )

    def test_shell_env_writes_exactly_one_line(self) -> None:
        defaults = load(ROLES / "shell_env" / "defaults" / "main.yml")
        line = defaults["shell_env_include_line"]
        self.assertNotIn("\n", line.strip())
        self.assertIn("~/.zshrc.d/*.zsh", line)
        tasks = load(ROLES / "shell_env" / "tasks" / "main.yml")
        zshrc_tasks = [
            t for t in iter_tasks(tasks)
            if "shell_env_zshrc" in str(t.get("ansible.builtin.lineinfile", {}).get("path", ""))
            or "shell_env_zshrc" in str(t.get("ansible.builtin.copy", {}).get("dest", ""))
            or "shell_env_zshrc" in str(t.get("ansible.builtin.template", {}).get("dest", ""))
            or "shell_env_zshrc" in str(t.get("ansible.builtin.blockinfile", {}).get("path", ""))
        ]
        self.assertEqual(len(zshrc_tasks), 2)  # existing file / fresh file — same single line
        for task in zshrc_tasks:
            module = task["ansible.builtin.lineinfile"]
            self.assertEqual(module["line"], "{{ shell_env_include_line }}")

    def test_drop_ins_live_in_zshrc_d(self) -> None:
        for role in MAC_DEV_ROLES:
            for path in role_task_files(role):
                for task in iter_tasks(load(path)):
                    tmpl = task.get("ansible.builtin.template") or {}
                    dest = str(tmpl.get("dest", ""))
                    if dest.endswith(".zsh"):
                        self.assertTrue(
                            "/.zshrc.d/" in dest or dest.startswith("{{ shell_env_dropin_dir }}/"),
                            f"{role}: {dest}",
                        )

    def test_third_party_installers_cannot_edit_rc_files(self) -> None:
        for role in ("claude", "hermes", "golang"):
            text = (ROLES / role / "tasks" / "main.yml").read_text()
            self.assertIn("cylon-rc-guard", text, role)
        self.assertIn("--no-modify-path", (ROLES / "rust" / "tasks" / "main.yml").read_text())
        self.assertIn("GVM_NO_UPDATE_PROFILE", (ROLES / "golang" / "tasks" / "main.yml").read_text())

    def test_repo_toolchain_pins_win(self) -> None:
        for path in (ROLES / "rust" / "templates").glob("*"):
            text = "\n".join(
                line for line in path.read_text().splitlines() if not line.lstrip().startswith("#")
            )
            self.assertNotIn("RUSTUP_TOOLCHAIN", text, path.name)
        cargo_cfg = (ROLES / "rust" / "templates" / "cargo-config.toml.j2").read_text()
        self.assertNotRegex(cargo_cfg, r"^\[patch", "no ms02-style ancestor [patch] overrides")
        self.assertNotRegex(cargo_cfg, re.compile(r"^\[patch", re.M))
        self.assertNotIn("/.cargo/config.toml", (ROLES / "dev_workspace" / "defaults" / "main.yml").read_text())


if __name__ == "__main__":
    unittest.main()
