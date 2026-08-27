from __future__ import annotations

import json
import unittest
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
YARN_JSON = (
    REPO_ROOT
    / "roles"
    / "vllm_stacked_container"
    / "files"
    / "qwen_moe_patches"
    / "qwen38-yarn.json"
)
SPARKS = REPO_ROOT / "inventory" / "group_vars" / "sparks.yml"
STAGE_TASK = REPO_ROOT / "roles" / "vllm_stacked_container" / "tasks" / "main.yml"
CONTAINER_YARN = "/vllm-patches/qwen38-yarn.json"


class Qwen38YarnAnsibleTests(unittest.TestCase):
    def test_yarn_json_is_official_factor_2_on_native_262k(self) -> None:
        data = json.loads(YARN_JSON.read_text())
        rope = data["text_config"]["rope_parameters"]
        self.assertEqual(rope["rope_type"], "yarn")
        self.assertEqual(rope["factor"], 2.0)
        self.assertEqual(rope["original_max_position_embeddings"], 262144)
        self.assertTrue(rope["mrope_interleaved"])
        self.assertEqual(rope["mrope_section"], [11, 11, 10])
        self.assertEqual(rope["partial_rotary_factor"], 0.25)
        self.assertEqual(rope["rope_theta"], 10000000)

    def test_inventory_caps_at_512_ki_and_loads_overrides_via_cat(self) -> None:
        extra = yaml.safe_load(SPARKS.read_text())["vllm_api_server_extra_args"]
        self.assertIn("--max-model-len", extra)
        self.assertEqual(extra[extra.index("--max-model-len") + 1], "524288")
        self.assertIn("--hf-overrides", extra)
        override = extra[extra.index("--hf-overrides") + 1]
        self.assertIn(CONTAINER_YARN, override)
        self.assertTrue(
            override.startswith('"$(cat ') and override.endswith(')"'),
            msg="override must be double-quoted command substitution so bash -lc "
            "does not brace-expand the JSON",
        )
        self.assertNotIn("rope_type", override)

    def test_role_stages_yarn_json_onto_patch_bind_mount(self) -> None:
        tasks = STAGE_TASK.read_text()
        self.assertIn("qwen38-yarn.json", tasks)
        self.assertIn("Stage Qwen3 MoE patches + chat template onto Spark host", tasks)


if __name__ == "__main__":
    unittest.main()
