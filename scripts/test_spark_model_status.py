#!/usr/bin/env python3
"""Unit tests for spark_model_status (stdlib unittest)."""

from __future__ import annotations

import json
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import spark_model_status as sms


class TestSummarize(unittest.TestCase):
    def test_vllm_models_url(self) -> None:
        self.assertEqual(sms.vllm_models_url("http://h:8000"), "http://h:8000/v1/models")
        self.assertEqual(sms.vllm_models_url("http://h:8000/"), "http://h:8000/v1/models")

    def test_summarize_vllm_models(self) -> None:
        payload = {
            "object": "list",
            "data": [
                {"id": "qwen3", "root": "Qwen/Qwen3.6-35B-A3B-FP8"},
                {"id": "x", "root": ""},
            ],
        }
        lines = sms.summarize_vllm_models(payload)
        self.assertTrue(any("qwen3" in line for line in lines))

    def test_normalize_prefetch_models(self) -> None:
        self.assertEqual(len(sms.normalize_prefetch_models({"models": [{"id": "a"}]})), 1)
        self.assertEqual(len(sms.normalize_prefetch_models([{"id": "a"}])), 1)
        self.assertEqual(sms.normalize_prefetch_models({}), [])

    def test_prefetch_summary_lines(self) -> None:
        lines = sms.prefetch_summary_lines(
            [
                {
                    "id": "Qwen/x",
                    "status": "ready",
                    "reason": "cached + synced",
                    "cache_bytes": 100,
                    "bytes_per_sec": 0,
                }
            ]
        )
        self.assertTrue(any("ready" in line and "Qwen/x" in line for line in lines))


class TestObserveScript(unittest.TestCase):
    def test_observe_remote_bash_contains_ray_and_ports(self) -> None:
        s = sms.observe_remote_bash(
            head_container="vllm-ngc-ray-head",
            vllm_port="8000",
            ray_dashboard_port="8265",
            ray_gcs_port="6379",
            log_tail_lines=10,
        )
        self.assertIn("ray status", s)
        self.assertIn("vllm-serve.log", s)
        self.assertIn(":8000", s)
        self.assertIn("/metrics", s)


class TestTriggers(unittest.TestCase):
    def test_repo_root_from_script(self) -> None:
        root = sms.repo_root_from_script(__file__)
        self.assertTrue((root / "playbooks" / "refresh_hf_prefetch.yml").is_file())

    def test_cmd_ansible_prefetch(self) -> None:
        root = pathlib.Path(__file__).resolve().parent.parent
        cmd = sms.cmd_ansible_prefetch(root, inventory=None, check=False)
        self.assertEqual(cmd[0], "ansible-playbook")
        self.assertIn(str(root / "playbooks" / "refresh_hf_prefetch.yml"), cmd)

    def test_cmd_ansible_vllm(self) -> None:
        root = pathlib.Path(__file__).resolve().parent.parent
        cmd = sms.cmd_ansible_vllm(
            root, inventory=None, extra_vars=["vllm_default_model=X", "vllm_stacked_container_recreate=true"], check=True
        )
        self.assertIn("--check", cmd)
        self.assertIn("--tags", cmd)
        self.assertIn("vllm_ngc_stack", cmd)

    def test_cmd_ansible_sync_hermes_ms02(self) -> None:
        root = pathlib.Path(__file__).resolve().parent.parent
        cmd = sms.cmd_ansible_sync_hermes_ms02(root, inventory=None, check=False)
        self.assertEqual(cmd[0], "ansible-playbook")
        self.assertIn(str(root / "playbooks" / "sync_hermes_ms02.yml"), cmd)
        self.assertIn("ms02", cmd)

    def test_cmd_prefetch_once_ssh(self) -> None:
        cmd = sms.cmd_prefetch_once_ssh(
            "nvidia1",
            install_py="/opt/hf-prefetch/hf_prefetch_service.py",
            config_yaml="/etc/hf-prefetch/config.yaml",
            state_json="/var/lib/hf-prefetch/state.json",
            runtime_home="/home/nvidia",
            verbose=1,
        )
        joined = " ".join(cmd)
        self.assertIn("sudo -u nvidia", joined)
        self.assertIn("--once", joined)


class TestParsePrefetchJson(unittest.TestCase):
    def test_roundtrip_sample(self) -> None:
        raw = json.dumps(
            {
                "models": [
                    {"id": "m1", "status": "syncing", "reason": "", "cache_bytes": None},
                ]
            }
        )
        state = json.loads(raw)
        models = sms.normalize_prefetch_models(state)
        self.assertEqual(models[0]["status"], "syncing")


if __name__ == "__main__":
    unittest.main()
