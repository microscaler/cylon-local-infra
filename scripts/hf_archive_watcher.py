#!/usr/bin/env python3
from __future__ import annotations

import argparse
import dataclasses
import fcntl
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Sequence


HF_BIN = Path(os.environ.get("HF_ARCHIVE_HF_BIN", "/opt/hf-archive-venv/bin/hf"))
GCLOUD_BIN = Path(os.environ.get("HF_ARCHIVE_GCLOUD_BIN", "/snap/bin/gcloud"))
ARCHIVE_ROOT = Path(os.environ.get("HF_ARCHIVE_ROOT", "/srv/hf-archive"))
BUCKET = os.environ.get("HF_ARCHIVE_BUCKET", "microscaler-huggingface")
STATE_DIR = ARCHIVE_ROOT / ".watcher"
STATE_PATH = STATE_DIR / "state.json"
LOCK_PATH = STATE_DIR / "watcher.lock"
PRECISION_ORDER = ("fp8", "fp16")
BACKSTOP_REPO_ID = "RedHatAI/Kimi-K3-NVFP4"
MIN_FREE_RESERVE = 20 * 1024**3

INCOMPATIBLE_MARKERS = (
    "gguf",
    "mlx",
    "onnx",
    "awq",
    "gptq",
    "exl2",
    "int4",
    "int8",
    "mxfp4",
    "nvfp4",
    "fp4",
)


@dataclasses.dataclass(frozen=True)
class Candidate:
    repo_id: str
    sha: str
    precision: str
    score: int

    def as_dict(self) -> dict[str, object]:
        return dataclasses.asdict(self)


def normalize_text(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value).lower())


def _metadata_text(model: dict[str, Any]) -> str:
    searchable = {
        "id": model.get("id"),
        "author": model.get("author"),
        "tags": model.get("tags"),
        "config": model.get("config"),
    }
    return normalize_text(json.dumps(searchable, sort_keys=True))


def _parameter_totals(model: dict[str, Any]) -> tuple[int, int, int]:
    parameters = (model.get("safetensors") or {}).get("parameters") or {}
    fp8 = 0
    fp16 = 0
    total = 0
    for raw_name, raw_count in parameters.items():
        try:
            count = int(raw_count)
        except (TypeError, ValueError):
            continue
        name = normalize_text(raw_name)
        total += count
        if name.startswith("f8") or "float8" in name or "fp8" in name:
            fp8 += count
        if name in {"f16", "fp16", "float16", "bf16", "bfloat16"}:
            fp16 += count
    return fp8, fp16, total


def classify_model(model: dict[str, Any]) -> Candidate | None:
    repo_id = str(model.get("id") or "")
    author = str(model.get("author") or "")
    sha = str(model.get("sha") or "")
    if author.lower() != "moonshotai" or not repo_id.lower().startswith("moonshotai/"):
        return None
    if not re.fullmatch(r"[0-9a-fA-F]{40}", sha):
        return None

    text = _metadata_text(model)
    if "kimik3" not in text:
        return None
    if any(marker in text for marker in INCOMPATIBLE_MARKERS):
        return None

    siblings = model.get("siblings") or []
    sibling_names = [str(item.get("rfilename") or "") for item in siblings]
    if not any(name.endswith(".safetensors") for name in sibling_names):
        return None

    fp8_count, fp16_count, total_count = _parameter_totals(model)
    precision: str | None = None
    if total_count > 0 and fp8_count / total_count >= 0.5:
        precision = "fp8"
    elif total_count > 0 and fp16_count / total_count >= 0.5:
        precision = "fp16"
    elif any(marker in text for marker in ("fp8", "float8", "f8e4m3", "nvfp8")):
        precision = "fp8"
    elif any(marker in text for marker in ("fp16", "float16", "bf16", "bfloat16")):
        precision = "fp16"
    if precision is None:
        return None

    score = 100
    if "nvidia" in text:
        score += 30
    if "vllm" in text:
        score += 20
    if "safetensors" in text:
        score += 15
    if precision in normalize_text(repo_id):
        score += 20
    if "base" not in normalize_text(repo_id):
        score += 5
    return Candidate(repo_id=repo_id, sha=sha.lower(), precision=precision, score=score)


def classify_backstop_model(model: dict[str, Any]) -> Candidate | None:
    repo_id = str(model.get("id") or "")
    author = str(model.get("author") or "")
    sha = str(model.get("sha") or "")
    if repo_id != BACKSTOP_REPO_ID or author.lower() != "redhatai":
        return None
    if not re.fullmatch(r"[0-9a-fA-F]{40}", sha):
        return None

    text = _metadata_text(model)
    required_markers = (
        "kimik3",
        "basemodelmoonshotaikimik3",
        "nvfp4packquantized",
        "compressedtensors",
        "numbits4",
    )
    if not all(marker in text for marker in required_markers):
        return None

    siblings = model.get("siblings") or []
    if not any(
        str(item.get("rfilename") or "").endswith(".safetensors")
        for item in siblings
    ):
        return None
    return Candidate(
        repo_id=repo_id,
        sha=sha.lower(),
        precision="nvfp4",
        score=100,
    )


def rank_candidates(models: Sequence[dict[str, Any]]) -> list[Candidate]:
    candidates = [candidate for model in models if (candidate := classify_model(model))]
    order = {precision: index for index, precision in enumerate(PRECISION_ORDER)}
    return sorted(
        candidates,
        key=lambda item: (order[item.precision], -item.score, item.repo_id.lower()),
    )


def parse_download_size(output: str) -> int:
    match = re.search(r"totalling\s+([0-9]+(?:\.[0-9]+)?)\s*([KMGT])", output, re.I)
    if not match:
        raise ValueError("Unable to parse total download size from hf dry-run output")
    value = float(match.group(1))
    multiplier = {
        "K": 1_000,
        "M": 1_000_000,
        "G": 1_000_000_000,
        "T": 1_000_000_000_000,
    }[match.group(2).upper()]
    return int(value * multiplier)


def _run(
    argv: Sequence[str | Path],
    *,
    capture: bool = False,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(item) for item in argv],
        check=check,
        capture_output=capture,
        text=True,
    )


def discover_models() -> list[dict[str, Any]]:
    common = [
        HF_BIN,
        "models",
        "list",
        "--expand=author,sha,tags,config,siblings,safetensors",
        "--format=json",
    ]
    queries = (
        [
            *common,
            "--author",
            "moonshotai",
            "--limit",
            "100",
        ],
        [
            *common,
            "--search",
            BACKSTOP_REPO_ID,
            "--limit",
            "10",
        ],
    )
    models: dict[str, dict[str, Any]] = {}
    for query in queries:
        completed = _run(query, capture=True)
        payload = json.loads(completed.stdout)
        if not isinstance(payload, list):
            raise ValueError("hf models list did not return a JSON list")
        for model in payload:
            repo_id = str(model.get("id") or "")
            if repo_id:
                models[repo_id] = model
    return list(models.values())


def _load_state() -> dict[str, Any]:
    if not STATE_PATH.exists():
        return {"archived": {}}
    return json.loads(STATE_PATH.read_text())


def _save_state(state: dict[str, Any]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    temporary = STATE_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")
    temporary.replace(STATE_PATH)


def _candidate_for_precision(
    candidates: Sequence[Candidate], precision: str
) -> Candidate | None:
    return next((item for item in candidates if item.precision == precision), None)


def _safe_local_path(candidate: Candidate) -> Path:
    repo_name = normalize_text(candidate.repo_id.split("/", 1)[-1])
    return ARCHIVE_ROOT / "kimi-k3" / candidate.precision / repo_name / candidate.sha


def _bucket_path(candidate: Candidate) -> str:
    return f"gs://{BUCKET}/models/{candidate.repo_id}/{candidate.sha}"


def _verify_bucket(local_path: Path, bucket_path: str) -> None:
    completed = _run(
        [
            GCLOUD_BIN,
            "storage",
            "rsync",
            local_path,
            bucket_path,
            "--recursive",
            "--checksums-only",
            "--dry-run",
        ],
        capture=True,
    )
    output = completed.stdout + completed.stderr
    if "Would copy " in output or "Would delete " in output:
        raise RuntimeError("GCS checksum verification still reports pending operations")


def archive_candidate(candidate: Candidate, state: dict[str, Any]) -> None:
    local_path = _safe_local_path(candidate)
    local_path.mkdir(parents=True, exist_ok=True)

    dry_run = _run(
        [
            HF_BIN,
            "download",
            candidate.repo_id,
            "--revision",
            candidate.sha,
            "--local-dir",
            local_path,
            "--dry-run",
        ],
        capture=True,
    )
    dry_run_output = dry_run.stdout + dry_run.stderr
    try:
        required_bytes = parse_download_size(dry_run_output)
    except ValueError:
        required_bytes = 0
    free_bytes = shutil.disk_usage(ARCHIVE_ROOT).free
    if required_bytes and required_bytes + MIN_FREE_RESERVE > free_bytes:
        raise RuntimeError(
            f"{candidate.repo_id}@{candidate.sha} needs {required_bytes} bytes, "
            f"but only {free_bytes} bytes are free"
        )

    _run(
        [
            HF_BIN,
            "download",
            candidate.repo_id,
            "--revision",
            candidate.sha,
            "--local-dir",
            local_path,
            "--max-workers",
            "2",
        ]
    )
    bucket_path = _bucket_path(candidate)
    _run(
        [
            GCLOUD_BIN,
            "storage",
            "rsync",
            local_path,
            bucket_path,
            "--recursive",
            "--checksums-only",
        ]
    )
    _verify_bucket(local_path, bucket_path)

    state.setdefault("archived", {})[candidate.precision] = {
        "repo_id": candidate.repo_id,
        "sha": candidate.sha,
        "bucket": bucket_path,
    }
    _save_state(state)

    resolved = local_path.resolve()
    allowed_root = (ARCHIVE_ROOT / "kimi-k3").resolve()
    if allowed_root not in resolved.parents:
        raise RuntimeError(f"Refusing to remove unsafe path: {resolved}")
    shutil.rmtree(resolved)


def run_watcher() -> int:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with LOCK_PATH.open("w") as lock_file:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("Watcher already running; skipping this poll.")
            return 0

        state = _load_state()
        models = discover_models()
        if "nvfp4" not in state.get("archived", {}):
            backstop = next(
                (
                    candidate
                    for model in models
                    if (candidate := classify_backstop_model(model))
                ),
                None,
            )
            if backstop is not None:
                print(
                    f"Selected {backstop.repo_id}@{backstop.sha} "
                    "as the NVIDIA NVFP4 archive target."
                )
                archive_candidate(backstop, state)

        candidates = rank_candidates(models)
        for precision in PRECISION_ORDER:
            if precision in state.get("archived", {}):
                continue
            candidate = _candidate_for_precision(candidates, precision)
            if candidate is None:
                print(f"No verified Kimi K3 {precision.upper()} candidate yet.")
                return 0
            print(
                f"Selected {candidate.repo_id}@{candidate.sha} "
                f"for {candidate.precision.upper()} (score={candidate.score})."
            )
            archive_candidate(candidate, state)
        return 0


def render_systemd_units(user: str) -> tuple[str, str]:
    service = f"""[Unit]
Description=Archive Kimi K3 NVFP4, then official FP8 and FP16/BF16 weights
Wants=network-online.target
After=network-online.target
ConditionPathIsMountPoint=/srv/hf-archive

[Service]
Type=oneshot
User={user}
Group={user}
Environment=HOME=/home/{user}
Environment=HF_HOME=/srv/hf-archive/.cache/huggingface
ExecStart=/opt/hf-archive-watcher/hf_archive_watcher.py run
Nice=5
"""
    timer = """[Unit]
Description=Poll Hugging Face for Kimi K3 archive candidates every 15 minutes

[Timer]
OnBootSec=1min
OnUnitInactiveSec=15min
AccuracySec=30s
Persistent=true

[Install]
WantedBy=timers.target
"""
    return service, timer


def install_systemd(user: str) -> None:
    if os.geteuid() != 0:
        raise PermissionError("The install command must run as root")
    service, timer = render_systemd_units(user)
    Path("/etc/systemd/system/hf-archive-watcher.service").write_text(service)
    Path("/etc/systemd/system/hf-archive-watcher.timer").write_text(timer)
    _run(["systemctl", "daemon-reload"])
    _run(["systemctl", "enable", "--now", "hf-archive-watcher.timer"])


def _rank_command(input_path: Path) -> int:
    models = json.loads(input_path.read_text())
    print(json.dumps([item.as_dict() for item in rank_candidates(models)], indent=2))
    return 0


def _status_command() -> int:
    print(json.dumps(_load_state(), indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    rank_parser = subparsers.add_parser("rank", help="Rank model metadata fixture")
    rank_parser.add_argument("--input", type=Path, required=True)

    subparsers.add_parser("run", help="Poll and process the next precision")

    install_parser = subparsers.add_parser("install", help="Install systemd timer")
    install_parser.add_argument("--user", required=True)

    subparsers.add_parser("status", help="Print persistent archive state")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "rank":
        return _rank_command(args.input)
    if args.command == "run":
        return run_watcher()
    if args.command == "install":
        install_systemd(args.user)
        return 0
    if args.command == "status":
        return _status_command()
    raise AssertionError(f"Unhandled command: {args.command}")


if __name__ == "__main__":
    sys.exit(main())
