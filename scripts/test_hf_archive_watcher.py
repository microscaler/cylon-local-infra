from __future__ import annotations

import contextlib
import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import hf_archive_watcher as watcher


SCRIPT = Path(__file__).with_name("hf_archive_watcher.py")


def model(
    repo_id: str,
    *,
    author: str = "moonshotai",
    parameters: dict[str, int] | None = None,
    tags: list[str] | None = None,
    config: dict[str, object] | None = None,
    sha: str = "a" * 40,
) -> dict[str, object]:
    return {
        "id": repo_id,
        "author": author,
        "sha": sha,
        "tags": tags or ["transformers", "safetensors"],
        "config": config or {},
        "safetensors": {
            "parameters": parameters or {},
            "total": sum((parameters or {}).values()),
        },
        "siblings": [{"rfilename": "model-00001-of-00002.safetensors"}],
    }


def backstop_model(sha: str = "2" * 40) -> dict[str, object]:
    return model(
        "RedHatAI/Kimi-K3-NVFP4",
        author="RedHatAI",
        sha=sha,
        tags=[
            "transformers",
            "safetensors",
            "kimi_k3",
            "compressed-tensors",
            "base_model:moonshotai/Kimi-K3",
        ],
        parameters={"U8": 2_722_740_830_208, "BF16": 57_179_884_544},
        config={
            "model_type": "kimi_k3",
            "quantization_config": {
                "format": "nvfp4-pack-quantized",
                "quant_method": "compressed-tensors",
                "config_groups": {
                    "group_0": {
                        "format": "nvfp4-pack-quantized",
                        "weights": {"num_bits": 4},
                    }
                },
            },
        },
    )


class HfArchiveWatcherTests(unittest.TestCase):
    def test_normalizes_kimi_k3_name_variants(self) -> None:
        for value in ("Kimi-K3", "kimi_k3", "Kimi K3", "KIMIK3"):
            self.assertIn("kimik3", watcher.normalize_text(value))

    def test_detects_fp8_from_tensor_metadata(self) -> None:
        candidate = model(
            "moonshotai/Kimi-K3",
            parameters={"F8_E4M3": 2_800_000, "BF16": 20_000},
        )
        result = watcher.classify_model(candidate)
        self.assertIsNotNone(result)
        self.assertEqual("fp8", result.precision)

    def test_detects_full_precision_bf16_as_fp16_target(self) -> None:
        candidate = model(
            "moonshotai/KimiK3-Full",
            parameters={"BF16": 2_800_000, "F32": 2_000},
        )
        result = watcher.classify_model(candidate)
        self.assertIsNotNone(result)
        self.assertEqual("fp16", result.precision)

    def test_detects_fp8_from_config_when_name_is_generic(self) -> None:
        candidate = model(
            "moonshotai/Kimi-K3",
            config={"quantization_config": {"quant_method": "fp8"}},
        )
        result = watcher.classify_model(candidate)
        self.assertIsNotNone(result)
        self.assertEqual("fp8", result.precision)

    def test_rejects_unverified_or_incompatible_quantizations(self) -> None:
        incompatible = ("GGUF", "MLX", "AWQ", "GPTQ", "MXFP4", "NVFP4")
        for suffix in incompatible:
            with self.subTest(suffix=suffix):
                self.assertIsNone(
                    watcher.classify_model(
                        model(
                            f"moonshotai/Kimi-K3-{suffix}",
                            parameters={"BF16": 2_800_000},
                            tags=["safetensors", suffix.lower()],
                        )
                    )
                )
        self.assertIsNone(
            watcher.classify_model(
                model(
                    "mirror/Kimi-K3-FP8",
                    author="mirror",
                    parameters={"F8_E4M3": 2_800_000},
                )
            )
        )

    def test_ranks_fp8_before_fp16_and_pins_sha(self) -> None:
        candidates = [
            model("moonshotai/Kimi-K3-BF16", parameters={"BF16": 2_800_000}),
            model(
                "moonshotai/Kimi-K3-NVIDIA-FP8",
                parameters={"F8_E4M3": 2_800_000},
                tags=["transformers", "safetensors", "nvidia", "vllm"],
                sha="b" * 40,
            ),
        ]
        ranked = watcher.rank_candidates(candidates)
        self.assertEqual("fp8", ranked[0].precision)
        self.assertEqual("b" * 40, ranked[0].sha)
        self.assertEqual("fp16", ranked[1].precision)

    def test_accepts_only_exact_validated_redhat_nvfp4_target(self) -> None:
        result = watcher.classify_backstop_model(backstop_model())
        self.assertIsNotNone(result)
        self.assertEqual("nvfp4", result.precision)
        self.assertEqual("RedHatAI/Kimi-K3-NVFP4", result.repo_id)

        wrong_repo = backstop_model()
        wrong_repo["id"] = "RedHatAI/Kimi-K3-GGUF"
        self.assertIsNone(watcher.classify_backstop_model(wrong_repo))

        wrong_format = backstop_model()
        wrong_format["config"] = {"model_type": "kimi_k3"}
        self.assertIsNone(watcher.classify_backstop_model(wrong_format))

    def test_parses_hf_dry_run_sizes(self) -> None:
        self.assertEqual(71_900_000_000, watcher.parse_download_size("totalling 71.9G"))
        self.assertEqual(
            1_400_000_000_000,
            watcher.parse_download_size("Will download files totalling 1.4T."),
        )

    def test_cli_rank_exercises_public_command(self) -> None:
        payload = [
            model(
                "moonshotai/Kimi-K3-FP8",
                parameters={"F8_E4M3": 2_800_000},
                sha="c" * 40,
            )
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            input_path = Path(temp_dir) / "models.json"
            input_path.write_text(json.dumps(payload))
            completed = subprocess.run(
                [sys.executable, str(SCRIPT), "rank", "--input", str(input_path)],
                check=True,
                capture_output=True,
                text=True,
            )
        output = json.loads(completed.stdout)
        self.assertEqual("fp8", output[0]["precision"])
        self.assertEqual("c" * 40, output[0]["sha"])

    def test_systemd_timer_polls_fifteen_minutes(self) -> None:
        service, timer = watcher.render_systemd_units("archive-user")
        self.assertIn("User=archive-user", service)
        self.assertIn("ExecStart=/opt/hf-archive-watcher/hf_archive_watcher.py run", service)
        self.assertIn("OnUnitInactiveSec=15min", timer)
        self.assertIn("Persistent=true", timer)

    def test_discover_models_uses_official_and_exact_backstop_queries(self) -> None:
        official = [model("moonshotai/Kimi-K3-FP8", parameters={"F8_E4M3": 10})]
        backstop = [backstop_model()]
        responses = [
            subprocess.CompletedProcess([], 0, stdout=json.dumps(official), stderr=""),
            subprocess.CompletedProcess([], 0, stdout=json.dumps(backstop), stderr=""),
        ]
        with mock.patch.object(watcher, "_run", side_effect=responses) as run:
            self.assertEqual(official + backstop, watcher.discover_models())
        official_argv = run.call_args_list[0].args[0]
        backstop_argv = run.call_args_list[1].args[0]
        self.assertIn("--author", official_argv)
        self.assertIn("moonshotai", official_argv)
        self.assertIn("--search", backstop_argv)
        self.assertIn("RedHatAI/Kimi-K3-NVFP4", backstop_argv)

    def test_archive_candidate_downloads_uploads_verifies_and_cleans(self) -> None:
        candidate = watcher.Candidate(
            repo_id="moonshotai/Kimi-K3-FP8",
            sha="d" * 40,
            precision="fp8",
            score=150,
        )
        dry_run = subprocess.CompletedProcess(
            [], 0, stdout="Will download files totalling 1.4G.", stderr=""
        )
        success = subprocess.CompletedProcess([], 0, stdout="", stderr="")
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            state_path = root / ".watcher" / "state.json"
            with (
                mock.patch.object(watcher, "ARCHIVE_ROOT", root),
                mock.patch.object(watcher, "STATE_DIR", state_path.parent),
                mock.patch.object(watcher, "STATE_PATH", state_path),
                mock.patch.object(
                    watcher, "_run", side_effect=[dry_run, success, success, success]
                ) as run,
            ):
                state: dict[str, object] = {"archived": {}}
                watcher.archive_candidate(candidate, state)
                local_path = watcher._safe_local_path(candidate)
            self.assertFalse(local_path.exists())
            saved = json.loads(state_path.read_text())
            self.assertEqual("d" * 40, saved["archived"]["fp8"]["sha"])
            self.assertEqual(4, run.call_count)
            download_argv = run.call_args_list[1].args[0]
            self.assertEqual(
                "2", str(download_argv[download_argv.index("--max-workers") + 1])
            )

    def test_archive_candidate_refuses_when_disk_is_too_small(self) -> None:
        candidate = watcher.Candidate(
            repo_id="moonshotai/Kimi-K3-FP8",
            sha="e" * 40,
            precision="fp8",
            score=150,
        )
        dry_run = subprocess.CompletedProcess(
            [], 0, stdout="Will download files totalling 1.4T.", stderr=""
        )
        usage = shutil.disk_usage("/")
        too_small = type(usage)(usage.total, usage.used, 1000)
        with tempfile.TemporaryDirectory() as temp_dir:
            with (
                mock.patch.object(watcher, "ARCHIVE_ROOT", Path(temp_dir)),
                mock.patch.object(watcher, "_run", return_value=dry_run),
                mock.patch.object(watcher.shutil, "disk_usage", return_value=too_small),
            ):
                with self.assertRaisesRegex(RuntimeError, "only 1000 bytes are free"):
                    watcher.archive_candidate(candidate, {"archived": {}})

    def test_run_watcher_waits_for_fp8_before_fp16(self) -> None:
        fp16 = model(
            "moonshotai/Kimi-K3-BF16",
            parameters={"BF16": 2_800_000},
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with (
                mock.patch.object(watcher, "STATE_DIR", root),
                mock.patch.object(watcher, "STATE_PATH", root / "state.json"),
                mock.patch.object(watcher, "LOCK_PATH", root / "watcher.lock"),
                mock.patch.object(watcher, "discover_models", return_value=[fp16]),
                mock.patch.object(watcher, "archive_candidate") as archive,
            ):
                self.assertEqual(0, watcher.run_watcher())
            archive.assert_not_called()

    def test_run_watcher_processes_fp8_then_fp16(self) -> None:
        models = [
            model(
                "moonshotai/Kimi-K3-FP8",
                parameters={"F8_E4M3": 2_800_000},
                sha="f" * 40,
            ),
            model(
                "moonshotai/Kimi-K3-BF16",
                parameters={"BF16": 2_800_000},
                sha="1" * 40,
            ),
        ]

        def mark_archived(
            candidate: watcher.Candidate, state: dict[str, object]
        ) -> None:
            state.setdefault("archived", {})[candidate.precision] = {
                "repo_id": candidate.repo_id,
                "sha": candidate.sha,
            }

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with (
                mock.patch.object(watcher, "STATE_DIR", root),
                mock.patch.object(watcher, "STATE_PATH", root / "state.json"),
                mock.patch.object(watcher, "LOCK_PATH", root / "watcher.lock"),
                mock.patch.object(watcher, "discover_models", return_value=models),
                mock.patch.object(
                    watcher, "archive_candidate", side_effect=mark_archived
                ) as archive,
            ):
                self.assertEqual(0, watcher.run_watcher())
            self.assertEqual(["fp8", "fp16"], [call.args[0].precision for call in archive.call_args_list])

    def test_run_watcher_archives_nvfp4_without_satisfying_fp8(self) -> None:
        models = [backstop_model()]

        def mark_archived(
            candidate: watcher.Candidate, state: dict[str, object]
        ) -> None:
            state.setdefault("archived", {})[candidate.precision] = {
                "repo_id": candidate.repo_id,
                "sha": candidate.sha,
            }

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with (
                mock.patch.object(watcher, "STATE_DIR", root),
                mock.patch.object(watcher, "STATE_PATH", root / "state.json"),
                mock.patch.object(watcher, "LOCK_PATH", root / "watcher.lock"),
                mock.patch.object(watcher, "discover_models", return_value=models),
                mock.patch.object(
                    watcher, "archive_candidate", side_effect=mark_archived
                ) as archive,
            ):
                self.assertEqual(0, watcher.run_watcher())
            self.assertEqual(["nvfp4"], [call.args[0].precision for call in archive.call_args_list])

    def test_install_systemd_enables_timer(self) -> None:
        with (
            mock.patch.object(watcher.os, "geteuid", return_value=0),
            mock.patch.object(watcher.Path, "write_text") as write_text,
            mock.patch.object(watcher, "_run") as run,
        ):
            watcher.install_systemd("archive-user")
        self.assertEqual(2, write_text.call_count)
        self.assertEqual(
            ["systemctl", "enable", "--now", "hf-archive-watcher.timer"],
            run.call_args_list[-1].args[0],
        )

    def test_public_main_dispatches_rank_run_install_and_status(self) -> None:
        payload = [
            model(
                "moonshotai/Kimi-K3-FP8",
                parameters={"F8_E4M3": 2_800_000},
            )
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            input_path = Path(temp_dir) / "models.json"
            input_path.write_text(json.dumps(payload))
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(
                    0, watcher.main(["rank", "--input", str(input_path)])
                )
            self.assertEqual("fp8", json.loads(output.getvalue())[0]["precision"])

        with mock.patch.object(watcher, "run_watcher", return_value=0) as run:
            self.assertEqual(0, watcher.main(["run"]))
        run.assert_called_once_with()

        with mock.patch.object(watcher, "install_systemd") as install:
            self.assertEqual(0, watcher.main(["install", "--user", "archive-user"]))
        install.assert_called_once_with("archive-user")

        with (
            mock.patch.object(watcher, "_load_state", return_value={"archived": {}}),
            contextlib.redirect_stdout(io.StringIO()) as output,
        ):
            self.assertEqual(0, watcher.main(["status"]))
        self.assertEqual({"archived": {}}, json.loads(output.getvalue()))

    def test_bucket_verification_rejects_pending_copies(self) -> None:
        pending = subprocess.CompletedProcess(
            [], 0, stdout="Would copy file://a to gs://bucket/a", stderr=""
        )
        with mock.patch.object(watcher, "_run", return_value=pending):
            with self.assertRaisesRegex(RuntimeError, "pending operations"):
                watcher._verify_bucket(Path("/tmp/local"), "gs://bucket/model")

    def test_install_requires_root(self) -> None:
        with mock.patch.object(watcher.os, "geteuid", return_value=1000):
            with self.assertRaisesRegex(PermissionError, "must run as root"):
                watcher.install_systemd("archive-user")


if __name__ == "__main__":
    unittest.main()
