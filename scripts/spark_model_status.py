#!/usr/bin/env python3
"""Probe DGX Spark vLLM API and hf-prefetch state; trigger sync / switchover from this repo.

Status (default):
  python3 scripts/spark_model_status.py
  SPARK_VLLM_API=http://192.168.1.104:8000 python3 scripts/spark_model_status.py --json

Deploy HF prefetch config from local inventory, then one-shot reconcile on leader:
  python3 scripts/spark_model_status.py cutover --repo ./
  python3 scripts/spark_model_status.py ansible-vllm --recreate -e vllm_default_model=Qwen/Qwen3.6-35B-A3B-FP8

Paths match roles/hf_prefetch_service and docs/provision_sparks.md.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

# Defaults aligned with roles/hf_prefetch_service/defaults/main.yml
DEFAULT_VLLM_BASE = os.environ.get("SPARK_VLLM_API", "http://192.168.1.104:8000")
DEFAULT_PREFETCH_PATH = "/var/lib/hf-prefetch/state.json"
DEFAULT_HF_PREFETCH_INSTALL = "/opt/hf-prefetch/hf_prefetch_service.py"
DEFAULT_HF_PREFETCH_CONFIG = "/etc/hf-prefetch/config.yaml"
DEFAULT_HF_PREFETCH_STATE = "/var/lib/hf-prefetch/state.json"
DEFAULT_PREFETCH_RUNTIME_HOME = "/home/nvidia"

COMMAND_SET = frozenset(
    {
        "status",
        "observe",
        "ansible-prefetch",
        "prefetch-once",
        "ansible-vllm",
        "cutover",
        "sync-hermes",
        "help",
    }
)

# Stacked Ray + vLLM (roles/vllm_stacked_container/defaults/main.yml)
DEFAULT_RAY_HEAD_CONTAINER = "vllm-ngc-ray-head"
DEFAULT_VLLM_API_PORT = "8000"
DEFAULT_RAY_DASHBOARD_PORT = "8265"
DEFAULT_RAY_GCS_PORT = "6379"


CANONICAL_SPARK_PLAYBOOK = "provision_sparks.yml"


def ansible_inventory_args(inventory: str | None) -> list[str]:
    inv = inventory or os.environ.get("SPARK_ANSIBLE_INVENTORY") or os.environ.get("ANSIBLE_INVENTORY")
    return ["-i", inv] if inv else []


def repo_root_from_script(script_file: str | Path | None = None) -> Path:
    """Directory containing playbooks/provision_sparks.yml (parent of scripts/)."""
    base = Path(script_file or __file__).resolve()
    for p in [base.parent, *base.parents]:
        if (p / "playbooks" / CANONICAL_SPARK_PLAYBOOK).is_file():
            return p
    return base.parent.parent


def _provision_sparks_cmd(
    repo: Path,
    *,
    inventory: str | None,
    extra_vars: list[str] | None = None,
    tags: str | None = None,
    check: bool = False,
) -> list[str]:
    """Build ansible-playbook for the single canonical Spark provision playbook."""
    cmd: list[str] = [
        "ansible-playbook",
        str(repo / "playbooks" / CANONICAL_SPARK_PLAYBOOK),
        "-l",
        "sparks",
        *ansible_inventory_args(inventory),
    ]
    if tags:
        cmd.extend(["--tags", tags])
    for raw in extra_vars or []:
        cmd.extend(["-e", raw])
    if check:
        cmd.append("--check")
    return cmd


def cmd_ansible_prefetch(
    repo: Path,
    *,
    inventory: str | None,
    check: bool,
) -> list[str]:
    """Deploy HF prefetch daemon config; assert gate included."""
    return _provision_sparks_cmd(
        repo,
        inventory=inventory,
        tags="hf_prefetch,spark_assert",
        check=check,
    )


def cmd_ansible_vllm(
    repo: Path,
    *,
    inventory: str | None,
    extra_vars: list[str],
    check: bool,
) -> list[str]:
    """Full end-to-end Spark reconcile + state assert (never partial vLLM-only tags)."""
    return _provision_sparks_cmd(
        repo,
        inventory=inventory,
        extra_vars=extra_vars,
        check=check,
    )


def cmd_ansible_sync_hermes_ms02(
    repo: Path,
    *,
    inventory: str | None,
    check: bool,
) -> list[str]:
    cmd: list[str] = [
        "ansible-playbook",
        str(repo / "playbooks" / "sync_hermes_ms02.yml"),
        "-l",
        "ms02",
        *ansible_inventory_args(inventory),
    ]
    if check:
        cmd.append("--check")
    return cmd


def run_process(cmd: list[str], *, cwd: Path | None) -> int:
    """Run command; stream is inherited. Returns exit code."""
    try:
        proc = subprocess.run(cmd, cwd=cwd, check=False)
    except FileNotFoundError as e:
        print(f"error: command not found: {e}", file=sys.stderr)
        return 127
    return int(proc.returncode)


def observe_remote_bash(
    *,
    head_container: str,
    vllm_port: str,
    ray_dashboard_port: str,
    ray_gcs_port: str,
    log_tail_lines: int,
) -> str:
    """Bash script run on the Spark leader via ssh … bash -s (set -u safe)."""
    hc = shlex.quote(head_container)
    n = max(5, min(int(log_tail_lines), 500))
    return f"""set -euo pipefail
H={hc}
echo "== docker ps (vllm) =="
docker ps -a --filter name=vllm 2>/dev/null | head -25 || true
echo
echo "== listening TCP (vLLM {vllm_port}, Ray dashboard {ray_dashboard_port}, Ray GCS {ray_gcs_port}) =="
ss -lntp 2>/dev/null | grep -E ':{vllm_port}\\b|:{ray_dashboard_port}\\b|:{ray_gcs_port}\\b' || true
echo
echo "== ray status (inside $H) =="
if docker container inspect "$H" >/dev/null 2>&1; then
  docker exec "$H" bash -lc 'ray status' 2>&1 || true
else
  echo "(container $H not present)"
fi
echo
echo "== vllm serve process (inside $H) =="
if docker container inspect "$H" >/dev/null 2>&1; then
  docker exec "$H" bash -lc "pgrep -af '[v]llm serve' || true" 2>&1 || true
else
  echo "(skip)"
fi
echo
echo "== tail /root/vllm-serve.log (last {n} lines) =="
if docker container inspect "$H" >/dev/null 2>&1; then
  docker exec "$H" bash -lc 'test -f /root/vllm-serve.log && tail -n {n} /root/vllm-serve.log || echo "(no /root/vllm-serve.log yet)"' 2>&1 || true
else
  echo "(skip)"
fi
echo
echo "== GET http://127.0.0.1:{vllm_port}/v1/models (on leader host) =="
curl -sS --max-time 8 "http://127.0.0.1:{vllm_port}/v1/models" 2>&1 | head -c 2000 || echo "(curl failed)"
echo
echo
echo "== GET http://127.0.0.1:{vllm_port}/metrics (first 25 lines) =="
curl -sS --max-time 5 "http://127.0.0.1:{vllm_port}/metrics" 2>&1 | head -n 25 || echo "(metrics unavailable)"
"""


def run_observe_ssh(
    ssh_host: str,
    *,
    head_container: str,
    vllm_port: str,
    ray_dashboard_port: str,
    ray_gcs_port: str,
    log_tail_lines: int,
    json_out: bool,
) -> int:
    """SSH to leader and print Ray + vLLM visibility (one round-trip)."""
    host = (ssh_host or "").strip()
    if not host:
        print("error: observe requires --ssh-host (leader)", file=sys.stderr)
        return 2
    body = observe_remote_bash(
        head_container=head_container,
        vllm_port=vllm_port,
        ray_dashboard_port=ray_dashboard_port,
        ray_gcs_port=ray_gcs_port,
        log_tail_lines=log_tail_lines,
    )
    cmd = [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=20",
        host,
        "bash",
        "-s",
    ]
    try:
        proc = subprocess.run(
            cmd,
            input=body,
            text=True,
            capture_output=True,
            timeout=120.0,
            check=False,
        )
    except subprocess.TimeoutExpired:
        print("error: ssh observe timed out after 120s", file=sys.stderr)
        return 124
    except FileNotFoundError:
        print("error: ssh not found on PATH", file=sys.stderr)
        return 127
    out = proc.stdout or ""
    err = (proc.stderr or "").strip()
    if json_out:
        payload: dict[str, Any] = {
            "ssh_host": host,
            "exit_code": proc.returncode,
            "stdout": out,
        }
        if err:
            payload["stderr"] = err
        print(json.dumps(payload, indent=2))
        return 0 if proc.returncode == 0 else proc.returncode
    print(out, end="")
    if err:
        print(err, file=sys.stderr)
    return 0 if proc.returncode == 0 else proc.returncode


def cmd_prefetch_once_ssh(
    ssh_host: str,
    *,
    install_py: str,
    config_yaml: str,
    state_json: str,
    runtime_home: str,
    verbose: int,
) -> list[str]:
    py = shlex.quote(install_py)
    cfg = shlex.quote(config_yaml)
    st = shlex.quote(state_json)
    rh = shlex.quote(runtime_home)
    vflag = " -v" * max(0, min(verbose, 3))
    inner = (
        f"sudo -u nvidia env HOME={rh} XDG_CONFIG_HOME={rh}/.config "
        f"/usr/bin/python3 {py} --config {cfg} --state {st} --once{vflag}"
    )
    return ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", ssh_host, inner]


# --- status probe ------------------------------------------------------------


@dataclass(frozen=True)
class HttpResult:
    ok: bool
    status_code: int | None
    body: Any | None
    error: str | None


def fetch_json(url: str, timeout: float = 15.0) -> HttpResult:
    """GET URL and parse JSON body; non-JSON bodies are returned as raw text in error."""
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            code = getattr(resp, "status", None) or resp.getcode()
            try:
                return HttpResult(True, code, json.loads(raw), None)
            except json.JSONDecodeError:
                return HttpResult(False, code, None, f"non-JSON body (first 200 chars): {raw[:200]!r}")
    except urllib.error.HTTPError as e:
        try:
            raw = e.read().decode("utf-8", errors="replace")
        except Exception:
            raw = ""
        return HttpResult(False, e.code, None, f"HTTP {e.code}: {raw[:300]}")
    except Exception as e:
        return HttpResult(False, None, None, str(e))


def vllm_models_url(base: str) -> str:
    b = base.rstrip("/")
    return f"{b}/v1/models"


def summarize_vllm_models(payload: Mapping[str, Any] | None) -> list[str]:
    if not payload or "data" not in payload:
        return ["(no model list in response)"]
    rows: list[str] = []
    for item in payload.get("data") or []:
        if not isinstance(item, dict):
            continue
        mid = item.get("id", "?")
        root = item.get("root", "")
        rows.append(f"  - {mid}" + (f"  (root={root})" if root else ""))
    return rows if rows else ["  (empty data[])"]


def ssh_cat_remote_file(
    host: str,
    remote_path: str,
    *,
    timeout_connect: int = 10,
    timeout_overall: float | None = None,
) -> tuple[str | None, str | None]:
    """Return (stdout, error). Uses BatchMode SSH."""
    cmd = [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=" + str(timeout_connect),
        host,
        "sudo",
        "cat",
        remote_path,
    ]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_overall,
            check=False,
        )
    except subprocess.TimeoutExpired as e:
        return None, f"ssh timed out: {e}"
    except FileNotFoundError:
        return None, "ssh not found on PATH"
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        return None, f"ssh exit {proc.returncode}: {err[:500]}"
    return proc.stdout, None


def normalize_prefetch_models(state: Any) -> list[dict[str, Any]]:
    """Accept common shapes: {"models": [...]} or top-level list."""
    if isinstance(state, list):
        return [x for x in state if isinstance(x, dict)]
    if isinstance(state, dict):
        m = state.get("models")
        if isinstance(m, list):
            return [x for x in m if isinstance(x, dict)]
    return []


def prefetch_summary_lines(models: Sequence[Mapping[str, Any]]) -> list[str]:
    lines: list[str] = []
    for m in models:
        name = m.get("id") or m.get("model") or m.get("name") or "?"
        status = m.get("status", "?")
        reason = m.get("reason", "")
        bps = m.get("bytes_per_sec")
        cache_b = m.get("cache_bytes")
        extra = []
        if cache_b is not None:
            extra.append(f"cache_bytes={cache_b}")
        if bps is not None:
            extra.append(f"bytes_per_sec={bps}")
        tail = "  " + ", ".join(extra) if extra else ""
        r = f"  ({reason})" if reason else ""
        lines.append(f"  - {name}: {status}{r}{tail}")
    return lines if lines else ["  (no models in prefetch state)"]


def run_report(
    *,
    vllm_base: str,
    skip_vllm: bool,
    ssh_host: str | None,
    prefetch_path: str,
    skip_prefetch: bool,
    json_out: bool,
) -> int:
    out: dict[str, Any] = {
        "vllm_base": vllm_base,
    }

    if not skip_vllm:
        url = vllm_models_url(vllm_base)
        hr = fetch_json(url)
        out["vllm"] = {
            "url": url,
            "ok": hr.ok,
            "status_code": hr.status_code,
            "error": hr.error,
            "models": hr.body,
        }
    else:
        out["vllm"] = {"skipped": True}

    if not skip_prefetch and ssh_host:
        raw, err = ssh_cat_remote_file(ssh_host, prefetch_path)
        if err:
            out["hf_prefetch"] = {"ok": False, "host": ssh_host, "path": prefetch_path, "error": err}
        else:
            try:
                state = json.loads(raw or "null")
            except json.JSONDecodeError as e:
                out["hf_prefetch"] = {
                    "ok": False,
                    "host": ssh_host,
                    "path": prefetch_path,
                    "error": f"invalid JSON: {e}",
                }
            else:
                models = normalize_prefetch_models(state)
                out["hf_prefetch"] = {
                    "ok": True,
                    "host": ssh_host,
                    "path": prefetch_path,
                    "raw_models": models,
                    "state_keys": list(state.keys()) if isinstance(state, dict) else None,
                }
    elif skip_prefetch:
        out["hf_prefetch"] = {"skipped": True}
    else:
        out["hf_prefetch"] = {
            "skipped": True,
            "hint": "pass --ssh-host to read remote prefetch state",
        }

    if json_out:
        print(json.dumps(out, indent=2))
        return 0 if (skip_vllm or out.get("vllm", {}).get("ok")) else 1

    # Human-readable
    print("=== vLLM /v1/models ===")
    if skip_vllm:
        print("(skipped)")
    else:
        v = out.get("vllm") or {}
        if v.get("ok") and isinstance(v.get("models"), dict):
            print(f"GET {v.get('url')}  HTTP {v.get('status_code')}")
            for line in summarize_vllm_models(v["models"]):
                print(line)
        else:
            print(f"GET {v.get('url')}")
            print(f"  error: {v.get('error')}")

    print()
    print("=== hf-prefetch state (optional) ===")
    hp = out.get("hf_prefetch") or {}
    if hp.get("skipped"):
        print(hp.get("hint") or "(skipped)")
    elif not hp.get("ok"):
        print(f"{ssh_host}:{prefetch_path}")
        print(f"  error: {hp.get('error')}")
    else:
        print(f"{hp.get('host')}:{hp.get('path')}")
        for line in prefetch_summary_lines(hp.get("raw_models") or []):
            print(line)

    vllm_ok = skip_vllm or bool((out.get("vllm") or {}).get("ok"))
    return 0 if vllm_ok else 1


def build_status_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Probe Spark vLLM and hf-prefetch model status.")
    p.add_argument(
        "--vllm-base",
        default=DEFAULT_VLLM_BASE,
        help=f"OpenAI-compatible base URL (default env SPARK_VLLM_API or {DEFAULT_VLLM_BASE!r})",
    )
    p.add_argument("--skip-vllm", action="store_true", help="Do not query /v1/models.")
    p.add_argument(
        "--ssh-host",
        default=os.environ.get("SPARK_SSH_HOST", "nvidia1"),
        help="SSH host alias for leader (prefetch state). Empty disables SSH.",
    )
    p.add_argument("--prefetch-path", default=DEFAULT_PREFETCH_PATH, help="Remote path to state.json on leader.")
    p.add_argument("--skip-prefetch", action="store_true", help="Do not fetch hf-prefetch state.")
    p.add_argument("--json", dest="json_out", action="store_true", help="Emit one JSON object on stdout.")
    return p


def print_cli_help() -> None:
    print(
        """usage: spark_model_status.py [<command>] [options]

Commands (default: status):
  status              Probe vLLM /v1/models and optional hf-prefetch state (default).
  observe             SSH to leader: docker ps, ss ports, ray status, vllm-serve.log tail, /metrics.
  ansible-prefetch    ansible-playbook playbooks/provision_sparks.yml --tags hf_prefetch,spark_assert
  prefetch-once       SSH to leader: run hf_prefetch_service.py --once (download + peer rsync pass).
  ansible-vllm        Full playbooks/provision_sparks.yml reconcile + spark_assert (--recreate for cutover)
  cutover             ansible-prefetch, then prefetch-once, then full ansible-vllm (model switch workflow).
  sync-hermes         ansible-playbook playbooks/sync_hermes_ms02.yml — patch Hermes .env on ms02.

Environment:
  SPARK_VLLM_API          Base URL for status probes (default http://192.168.1.104:8000)
  SPARK_SSH_HOST          Leader SSH alias (default nvidia1)
  SPARK_ANSIBLE_INVENTORY Optional ansible -i path

Examples:
  python3 scripts/spark_model_status.py
  python3 scripts/spark_model_status.py observe --ssh-host nvidia1
  python3 scripts/spark_model_status.py ansible-prefetch --repo .
  python3 scripts/spark_model_status.py prefetch-once --ssh-host nvidia1
  python3 scripts/spark_model_status.py ansible-vllm --recreate
  python3 scripts/spark_model_status.py ansible-vllm -e vllm_default_model=Qwen/Qwen3.6-35B-A3B-FP8 --recreate
  python3 scripts/spark_model_status.py cutover --recreate
  python3 scripts/spark_model_status.py cutover --recreate --sync-hermes
  python3 scripts/spark_model_status.py sync-hermes

Edit inventory/group_vars/sparks.yml (hf_prefetch_models, vllm_default_model) before cutover.
Set host_vars/ms02.yml hermes_agent_dotenv_path for Hermes sync.
"""
    )


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        args = build_status_parser().parse_args([])
        ssh_host = (args.ssh_host or "").strip() or None
        return run_report(
            vllm_base=args.vllm_base,
            skip_vllm=args.skip_vllm,
            ssh_host=ssh_host,
            prefetch_path=args.prefetch_path,
            skip_prefetch=args.skip_prefetch,
            json_out=args.json_out,
        )

    if argv[0] in ("-h", "--help"):
        print_cli_help()
        return 0

    if argv[0] == "help":
        print_cli_help()
        return 0

    cmd = argv[0]
    if cmd not in COMMAND_SET:
        args = build_status_parser().parse_args(argv)
        ssh_host = (args.ssh_host or "").strip() or None
        return run_report(
            vllm_base=args.vllm_base,
            skip_vllm=args.skip_vllm,
            ssh_host=ssh_host,
            prefetch_path=args.prefetch_path,
            skip_prefetch=args.skip_prefetch,
            json_out=args.json_out,
        )

    rest = argv[1:]

    if cmd == "status":
        args = build_status_parser().parse_args(rest)
        ssh_host = (args.ssh_host or "").strip() or None
        return run_report(
            vllm_base=args.vllm_base,
            skip_vllm=args.skip_vllm,
            ssh_host=ssh_host,
            prefetch_path=args.prefetch_path,
            skip_prefetch=args.skip_prefetch,
            json_out=args.json_out,
        )

    if cmd == "observe":
        ap = argparse.ArgumentParser(description="SSH to leader: Ray status, ports, vllm-serve.log tail, /metrics.")
        ap.add_argument(
            "--ssh-host",
            default=os.environ.get("SPARK_SSH_HOST", "nvidia1"),
            help="Leader SSH host (Ray head + vLLM API).",
        )
        ap.add_argument(
            "--head-container",
            default=os.environ.get("SPARK_RAY_HEAD_CONTAINER", DEFAULT_RAY_HEAD_CONTAINER),
            help="Docker name for Ray head (default from vllm_stacked_container).",
        )
        ap.add_argument("--vllm-port", default=os.environ.get("SPARK_VLLM_PORT", DEFAULT_VLLM_API_PORT))
        ap.add_argument("--ray-dashboard-port", default=DEFAULT_RAY_DASHBOARD_PORT)
        ap.add_argument("--ray-gcs-port", default=DEFAULT_RAY_GCS_PORT)
        ap.add_argument(
            "--tail",
            type=int,
            default=40,
            dest="log_tail_lines",
            help="Lines of /root/vllm-serve.log to show (inside head container).",
        )
        ap.add_argument("--json", dest="json_out", action="store_true", help="Emit JSON with stdout/stderr from ssh.")
        ns = ap.parse_args(rest)
        return run_observe_ssh(
            ns.ssh_host,
            head_container=ns.head_container,
            vllm_port=str(ns.vllm_port),
            ray_dashboard_port=str(ns.ray_dashboard_port),
            ray_gcs_port=str(ns.ray_gcs_port),
            log_tail_lines=ns.log_tail_lines,
            json_out=ns.json_out,
        )

    p_common = argparse.ArgumentParser(add_help=False)
    p_common.add_argument(
        "--repo",
        type=Path,
        default=None,
        help="Path to cylon-local-infra repo root (default: parent of scripts/).",
    )
    p_common.add_argument(
        "-i",
        "--inventory",
        default=None,
        help="Ansible inventory path (-i). Overrides SPARK_ANSIBLE_INVENTORY.",
    )
    p_common.add_argument(
        "--check",
        action="store_true",
        help="Pass ansible-playbook --check (dry run).",
    )

    if cmd == "ansible-prefetch":
        ap = argparse.ArgumentParser(parents=[p_common])
        ns = ap.parse_args(rest)
        repo = ns.repo or repo_root_from_script()
        if not (repo / "playbooks" / CANONICAL_SPARK_PLAYBOOK).is_file():
            print(f"error: not a cylon-local-infra repo root: {repo}", file=sys.stderr)
            return 2
        c = cmd_ansible_prefetch(repo, inventory=ns.inventory, check=ns.check)
        print("+", shlex.join(c), flush=True)
        return run_process(c, cwd=repo)

    if cmd == "prefetch-once":
        ap = argparse.ArgumentParser(parents=[p_common])
        ap.add_argument(
            "--ssh-host",
            default=os.environ.get("SPARK_SSH_HOST", "nvidia1"),
            help="Leader SSH host alias.",
        )
        ap.add_argument("--install-py", default=DEFAULT_HF_PREFETCH_INSTALL)
        ap.add_argument("--config", dest="config_yaml", default=DEFAULT_HF_PREFETCH_CONFIG)
        ap.add_argument("--state", dest="state_json", default=DEFAULT_HF_PREFETCH_STATE)
        ap.add_argument("--runtime-home", default=DEFAULT_PREFETCH_RUNTIME_HOME)
        ap.add_argument("-v", action="count", default=1, help="Pass -v to remote (repeat for -vv).")
        ns = ap.parse_args(rest)
        host = (ns.ssh_host or "").strip()
        if not host:
            print("error: --ssh-host required", file=sys.stderr)
            return 2
        c = cmd_prefetch_once_ssh(
            host,
            install_py=ns.install_py,
            config_yaml=ns.config_yaml,
            state_json=ns.state_json,
            runtime_home=ns.runtime_home,
            verbose=ns.v,
        )
        print("+", shlex.join(c), flush=True)
        return run_process(c, cwd=None)

    if cmd == "ansible-vllm":
        ap = argparse.ArgumentParser(parents=[p_common])
        ap.add_argument(
            "--recreate",
            action="store_true",
            help="Set -e vllm_stacked_container_recreate=true (needed to restart vllm serve on new weights).",
        )
        ap.add_argument(
            "--model",
            default=None,
            help="Shortcut for -e vllm_default_model=...",
        )
        ap.add_argument(
            "-e",
            "--extra-var",
            action="append",
            default=[],
            metavar="KEY=VAL",
            dest="extra_vars",
            help="ansible-playbook extra var (repeatable).",
        )
        ns = ap.parse_args(rest)
        repo = ns.repo or repo_root_from_script()
        if not (repo / "playbooks" / "provision_sparks.yml").is_file():
            print(f"error: not a cylon-local-infra repo root: {repo}", file=sys.stderr)
            return 2
        ev: list[str] = list(ns.extra_vars)
        if ns.model:
            ev.append(f"vllm_default_model={ns.model}")
        if ns.recreate:
            ev.append("vllm_stacked_container_recreate=true")
            ev.append("vllm_torchrun_stacked_recreate=true")
        c = cmd_ansible_vllm(repo, inventory=ns.inventory, extra_vars=ev, check=ns.check)
        print("+", shlex.join(c), flush=True)
        return run_process(c, cwd=repo)

    if cmd == "cutover":
        ap = argparse.ArgumentParser(parents=[p_common])
        ap.add_argument(
            "--ssh-host",
            default=os.environ.get("SPARK_SSH_HOST", "nvidia1"),
            help="Leader for prefetch-once.",
        )
        ap.add_argument(
            "--skip-prefetch-once",
            action="store_true",
            help="Only run ansible steps (skip remote hf_prefetch --once).",
        )
        ap.add_argument(
            "--recreate",
            action="store_true",
            help="Pass vllm_stacked_container_recreate=true to ansible-vllm step.",
        )
        ap.add_argument(
            "--model",
            default=None,
            help="Shortcut for vllm_default_model= on the ansible-vllm step.",
        )
        ap.add_argument(
            "-e",
            "--extra-var",
            action="append",
            default=[],
            metavar="KEY=VAL",
            dest="extra_vars",
            help="ansible-playbook extra var for the final vLLM step (repeatable).",
        )
        ap.add_argument(
            "--sync-hermes",
            action="store_true",
            help="After vLLM: run playbooks/sync_hermes_ms02.yml (Hermes .env on ms02).",
        )
        ns = ap.parse_args(rest)
        repo = ns.repo or repo_root_from_script()
        if not (repo / "playbooks" / CANONICAL_SPARK_PLAYBOOK).is_file():
            print(f"error: not a cylon-local-infra repo root: {repo}", file=sys.stderr)
            return 2

        c1 = cmd_ansible_prefetch(repo, inventory=ns.inventory, check=ns.check)
        print("+", shlex.join(c1), flush=True)
        rc = run_process(c1, cwd=repo)
        if rc != 0:
            return rc

        host = (ns.ssh_host or "").strip()
        if not ns.skip_prefetch_once and host:
            c2 = cmd_prefetch_once_ssh(
                host,
                install_py=DEFAULT_HF_PREFETCH_INSTALL,
                config_yaml=DEFAULT_HF_PREFETCH_CONFIG,
                state_json=DEFAULT_HF_PREFETCH_STATE,
                runtime_home=DEFAULT_PREFETCH_RUNTIME_HOME,
                verbose=1,
            )
            print("+", shlex.join(c2), flush=True)
            rc = run_process(c2, cwd=None)
            if rc != 0:
                return rc

        ev: list[str] = list(ns.extra_vars)
        if ns.model:
            ev.append(f"vllm_default_model={ns.model}")
        if ns.recreate:
            ev.append("vllm_stacked_container_recreate=true")
            ev.append("vllm_torchrun_stacked_recreate=true")
        c3 = cmd_ansible_vllm(repo, inventory=ns.inventory, extra_vars=ev, check=ns.check)
        print("+", shlex.join(c3), flush=True)
        rc = run_process(c3, cwd=repo)
        if rc != 0:
            return rc
        if ns.sync_hermes:
            c4 = cmd_ansible_sync_hermes_ms02(repo, inventory=ns.inventory, check=ns.check)
            print("+", shlex.join(c4), flush=True)
            return run_process(c4, cwd=repo)
        return 0

    if cmd == "sync-hermes":
        ap = argparse.ArgumentParser(parents=[p_common])
        ns = ap.parse_args(rest)
        repo = ns.repo or repo_root_from_script()
        if not (repo / "playbooks" / "sync_hermes_ms02.yml").is_file():
            print(f"error: not a cylon-local-infra repo root: {repo}", file=sys.stderr)
            return 2
        c = cmd_ansible_sync_hermes_ms02(repo, inventory=ns.inventory, check=ns.check)
        print("+", shlex.join(c), flush=True)
        return run_process(c, cwd=repo)

    print_cli_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
