from __future__ import annotations

import unittest
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
SPARKS = REPO_ROOT / "inventory" / "group_vars" / "sparks.yml"
SERVE = (
    REPO_ROOT / "roles" / "vllm_glm53_stacked" / "templates" / "glm53-serve.sh.j2"
)
DEFAULTS = REPO_ROOT / "roles" / "vllm_glm53_stacked" / "defaults" / "main.yml"
TASKS = REPO_ROOT / "roles" / "vllm_glm53_stacked" / "tasks" / "main.yml"
TEARDOWN = (
    REPO_ROOT / "roles" / "spark_provision" / "tasks" / "vllm_stack_teardown_inactive.yml"
)
PROVISION = REPO_ROOT / "roles" / "spark_provision" / "tasks" / "main.yml"
PATCH = (
    REPO_ROOT
    / "roles"
    / "vllm_glm53_stacked"
    / "files"
    / "sparse_attn_indexer_kpool_sm121.py"
)


class Glm53FlashAnsibleTests(unittest.TestCase):
    def test_inventory_declares_nvfp4_glm53_profile_knobs(self) -> None:
        data = yaml.safe_load(SPARKS.read_text())
        self.assertEqual(data["spark_llm_profile"], "qwen38")
        self.assertEqual(data["glm53_model"], "RedHatAI/GLM-5.3-Flash-NVFP4")
        self.assertEqual(data["glm53_served_model_name"], "glm-5.3-flash")
        self.assertEqual(data["dspark_model"], "deepseek-ai/DeepSeek-V4-Flash-0731")
        self.assertIn("Qwen/Qwen3.8-27B-FP8", data["hf_prefetch_models"])
        self.assertIn("RedHatAI/GLM-5.3-Flash-NVFP4", data["hf_prefetch_models"])
        self.assertNotIn("deepseek-ai/DeepSeek-V4-Flash-0731", data["hf_prefetch_models"])
        self.assertNotIn("LibertAIDAI/GLM-5.3-Flash-NVFP4", data["hf_prefetch_models"])

    def test_role_uses_patched_sm121_image_and_gb10_flags(self) -> None:
        defaults = DEFAULTS.read_text()
        serve = SERVE.read_text()
        self.assertIn("ghcr.io/tonyd2wild/vllm-glm53-flash:sm121-v8", defaults)
        self.assertIn("--moe-backend marlin", serve)
        self.assertIn("--reasoning-parser glm45", serve)
        self.assertIn("--tool-call-parser glm47", serve)
        self.assertIn("--language-model-only", serve)
        self.assertIn("--max-model-len", serve)
        self.assertNotIn("--kv-cache-memory", serve)
        self.assertIn("sparse_attn_indexer_kpool.py", TASKS.read_text())
        self.assertTrue(PATCH.is_file())
        self.assertIn("expand_pools_and_append_tail", PATCH.read_text()[:2000])

    def test_profile_is_wired_into_provision_and_teardown(self) -> None:
        provision = PROVISION.read_text()
        teardown = TEARDOWN.read_text()
        self.assertIn("vllm_glm53_stacked", provision)
        self.assertIn("glm53_flash", provision)
        self.assertIn("glm53_flash", teardown)
        self.assertIn("vllm-glm53", teardown)


if __name__ == "__main__":
    unittest.main()
