from __future__ import annotations

import unittest
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
SPARKS = REPO_ROOT / "inventory" / "group_vars" / "sparks.yml"
DEFAULTS = REPO_ROOT / "roles" / "spark_provision" / "defaults" / "main.yml"
PROVISION = REPO_ROOT / "roles" / "spark_provision" / "tasks" / "main.yml"
UMA_SYSCTL = REPO_ROOT / "roles" / "spark_provision" / "tasks" / "uma_sysctl.yml"
UMA_RECOVER = REPO_ROOT / "roles" / "spark_provision" / "tasks" / "uma_recover.yml"
ASSERT_TASKS = REPO_ROOT / "roles" / "spark_assert" / "tasks" / "main.yml"
ASSERT_DEFAULTS = REPO_ROOT / "roles" / "spark_assert" / "defaults" / "main.yml"
JUSTFILE = REPO_ROOT / "justfile"
DOCS = REPO_ROOT / "docs" / "provision_sparks.md"
SYSCTL_FILE = "/etc/sysctl.d/99-spark-uma.conf"


class SparkUmaAnsibleTests(unittest.TestCase):
    def test_inventory_declares_uma_knobs_without_arming_recover(self) -> None:
        # recover stays off; assert is on after the 2026-09-07 apply.
        data = yaml.safe_load(SPARKS.read_text())
        self.assertEqual(data["spark_uma_swappiness"], 0)
        self.assertTrue(data["spark_provision_uma_sysctl"])
        self.assertFalse(data["spark_uma_recover"])
        self.assertTrue(data["spark_uma_drop_caches_on_recover"])
        self.assertTrue(data["spark_uma_swapoff_on_recover"])
        self.assertTrue(data["spark_assert_uma_swappiness"])

    def test_role_defaults_match_inventory_contract(self) -> None:
        data = yaml.safe_load(DEFAULTS.read_text())
        self.assertEqual(data["spark_uma_swappiness"], 0)
        self.assertTrue(data["spark_provision_uma_sysctl"])
        self.assertFalse(data["spark_uma_recover"])
        self.assertEqual(data["spark_uma_sysctl_file"], SYSCTL_FILE)
        self.assertFalse(yaml.safe_load(ASSERT_DEFAULTS.read_text())["spark_assert_uma_swappiness"])

    def test_sysctl_task_persists_swappiness_drop_in(self) -> None:
        text = UMA_SYSCTL.read_text()
        self.assertIn(SYSCTL_FILE, text)
        self.assertIn("vm.swappiness", text)
        self.assertIn("spark_uma_swappiness", text)
        self.assertIn("sysctl", text)
        self.assertIn("-p", text)
        self.assertNotIn("drop_caches", text)
        self.assertNotIn("swapoff", text)

    def test_recover_is_opt_in_and_does_not_recreate_vllm(self) -> None:
        recover = UMA_RECOVER.read_text()
        provision = PROVISION.read_text()
        self.assertIn("drop_caches", recover)
        self.assertIn("swapoff", recover)
        self.assertIn("swapon", recover)
        self.assertIn("spark_uma_recover", recover)
        self.assertNotIn("vllm_stacked_container_recreate", recover)
        self.assertIn("uma_sysctl.yml", provision)
        self.assertIn("uma_recover.yml", provision)
        self.assertIn("spark_uma_recover", provision)
        self.assertIn("spark_uma", provision)

    def test_assert_swappiness_is_gated_off_until_apply(self) -> None:
        text = ASSERT_TASKS.read_text()
        self.assertIn("spark_assert_uma_swappiness", text)
        self.assertIn("/proc/sys/vm/swappiness", text)

    def test_just_recipes_are_tagged_and_do_not_full_provision(self) -> None:
        just = JUSTFILE.read_text()
        self.assertIn("spark-uma-apply", just)
        self.assertIn("spark-uma-recover", just)
        self.assertIn("spark-uma-recover-recreate", just)
        self.assertIn("spark-uma-status", just)
        apply_idx = just.index("spark-uma-apply")
        recover_idx = just.index("spark-uma-recover *extra:")
        recreate_idx = just.index("spark-uma-recover-recreate")
        apply = just[apply_idx:recover_idx]
        recover = just[recover_idx:recreate_idx]
        self.assertIn("--tags spark_uma", apply)
        self.assertNotIn("spark_uma_recover=true", apply)
        self.assertIn("spark_uma_recover=true", recover)
        self.assertIn("vllm_ngc_stack", just[recreate_idx : recreate_idx + 1200])
        self.assertNotIn("nfs_client", just[recreate_idx : recreate_idx + 1200])

    def test_docs_name_the_operator_sequence(self) -> None:
        docs = DOCS.read_text()
        self.assertIn("just spark-uma-apply", docs)
        self.assertIn("just spark-uma-recover", docs)
        self.assertIn("just spark-uma-recover-recreate", docs)
        self.assertIn("vm.swappiness", docs)


if __name__ == "__main__":
    unittest.main()
