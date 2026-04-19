#!/usr/bin/env python3
"""
vllm-stack-autoupgrade
======================

Systemd daemon on the leader Spark that promotes a newer NGC vLLM image to
the running stacked-Ray + vLLM-serve deployment, **only** when:

  1. The new tag has been marked ``ready`` on all nodes by
     ``ngc-image-sync.service`` for at least ``stabilization_sec`` seconds
     (default 1 hour — absorbs NGC flapping or a retract-and-republish).
  2. The vLLM API has been **quiescent** for ``quiet_window_sec`` seconds
     (default 5 min) — no in-flight or queued requests, no new successful
     completions.  Reads ``/metrics`` (Prometheus text format) directly from
     the vLLM HTTP server.
  3. The operator has explicitly flipped ``enabled: true`` for the cluster.

When all three hold, the daemon:

  - Captures the head container's runtime spec with ``docker inspect`` so we
    replay the exact env / mounts / shm_size / GPU / network / restart policy
    on the new image.
  - Captures the currently-executing ``vllm serve`` argv from inside the
    head container so we re-exec with the same flags.
  - Captures each peer worker's spec via ssh.
  - Bounces worker → head → re-runs head with new image → waits for Ray GCS →
    re-runs worker with new image → waits for Ray cluster size to return →
    ``docker exec -d head 'vllm serve …'`` → waits for ``/v1/models`` to return
    200 before calling the promotion successful.

No automatic rollback in this version: on failure we log loudly, set
``state.last_error``, and leave the stack down for operator triage.

Files
-----
* Config:  ``/etc/vllm-stack-autoupgrade/config.yaml``
* State:   ``/var/lib/vllm-stack-autoupgrade/state.json``
* Logs:    ``journalctl -u vllm-stack-autoupgrade``
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import yaml  # type: ignore[import-not-found]
except ImportError:
    print(
        "vllm-stack-autoupgrade: python module 'yaml' is required "
        "(apt install python3-yaml)",
        file=sys.stderr,
    )
    sys.exit(3)


log = logging.getLogger("vllm-stack-autoupgrade")

DEFAULT_CONFIG = Path("/etc/vllm-stack-autoupgrade/config.yaml")
DEFAULT_STATE = Path("/var/lib/vllm-stack-autoupgrade/state.json")

STATE_IDLE = "idle"
STATE_CANDIDATE = "candidate"            # new tag detected, waiting for stabilization
STATE_WAITING_QUIET = "waiting_quiet"    # counting quiet samples
STATE_PROMOTING = "promoting"            # bouncing containers
STATE_READY = "ready"                    # promotion complete, stack on new image
STATE_ERROR = "error"


# --------------------------------------------------------------------------- #
# Config                                                                      #
# --------------------------------------------------------------------------- #

@dataclass
class PeerSpec:
    host: str
    user: str = "nvidia"
    container: str = ""
    name: str | None = None

    @property
    def label(self) -> str:
        return self.name or self.host


@dataclass
class Config:
    # Kill switch — the whole service is a no-op when False.
    enabled: bool = False
    # Pin to a specific tag — if set, we only promote to this tag (no "newest ready").
    pinned_tag: str | None = None

    # Source of truth for "what's available".
    ngc_state_path: Path = Path("/var/lib/ngc-image-sync/state.json")
    # Repo prefix in ngc state keys, e.g. "nvcr.io/nvidia/vllm:" — we only look
    # at keys beginning with this prefix.
    repo: str = "nvcr.io/nvidia/vllm"
    # Regex a tag must match to be considered for promotion.
    tag_pattern: str = r"^\d{2}\.\d{2}-py3$"

    # Stack topology.
    leader_container: str = "vllm-ngc-ray-head"
    peers: list[PeerSpec] = field(default_factory=list)

    # Timing.
    poll_interval_sec: int = 300           # 5 min outer loop
    stabilization_sec: int = 3600          # tag must be ready for 1 h
    quiet_window_sec: int = 300            # 5 min of quiet before promoting
    quiet_sample_sec: int = 30             # sample /metrics this often
    max_wait_for_quiet_sec: int = 86400    # give up chasing quiet after 24 h
    ray_gcs_wait_sec: int = 120
    ray_cluster_wait_sec: int = 180
    vllm_api_wait_sec: int = 3600          # first-token load for a big model

    # vLLM metrics endpoint (localhost — we're on the leader).
    metrics_url: str = "http://127.0.0.1:8000/metrics"

    @classmethod
    def load(cls, path: Path) -> "Config":
        with path.open() as f:
            raw = yaml.safe_load(f) or {}
        peers = [
            PeerSpec(
                host=str(p["host"]),
                user=str(p.get("user", "nvidia")),
                container=str(p.get("container", "")),
                name=p.get("name"),
            )
            for p in (raw.get("peers") or [])
            if isinstance(p, dict) and p.get("host")
        ]
        return cls(
            enabled=bool(raw.get("enabled", False)),
            pinned_tag=raw.get("pinned_tag"),
            ngc_state_path=Path(raw.get("ngc_state_path", "/var/lib/ngc-image-sync/state.json")),
            repo=str(raw.get("repo", "nvcr.io/nvidia/vllm")),
            tag_pattern=str(raw.get("tag_pattern", r"^\d{2}\.\d{2}-py3$")),
            leader_container=str(raw.get("leader_container", "vllm-ngc-ray-head")),
            peers=peers,
            poll_interval_sec=int(raw.get("poll_interval_sec", 300)),
            stabilization_sec=int(raw.get("stabilization_sec", 3600)),
            quiet_window_sec=int(raw.get("quiet_window_sec", 300)),
            quiet_sample_sec=int(raw.get("quiet_sample_sec", 30)),
            max_wait_for_quiet_sec=int(raw.get("max_wait_for_quiet_sec", 86400)),
            ray_gcs_wait_sec=int(raw.get("ray_gcs_wait_sec", 120)),
            ray_cluster_wait_sec=int(raw.get("ray_cluster_wait_sec", 180)),
            vllm_api_wait_sec=int(raw.get("vllm_api_wait_sec", 3600)),
            metrics_url=str(raw.get("metrics_url", "http://127.0.0.1:8000/metrics")),
        )


# --------------------------------------------------------------------------- #
# State                                                                       #
# --------------------------------------------------------------------------- #

class StateStore:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self.data: dict[str, Any] = self._read()

    def _read(self) -> dict[str, Any]:
        if self.path.exists():
            try:
                return json.loads(self.path.read_text())
            except json.JSONDecodeError:
                log.warning("state file %s corrupt, starting fresh", self.path)
        return {
            "schema": 1,
            "host": socket.gethostname(),
            "pid": os.getpid(),
            "started_at": time.time(),
            "updated_at": time.time(),
            "status": STATE_IDLE,
            "current_image": None,
            "candidate_tag": None,
            "candidate_since": None,
            "last_promotion": None,
            "last_error": None,
        }

    def save(self) -> None:
        with self._lock:
            self.data["updated_at"] = time.time()
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data, indent=2, sort_keys=True))
            tmp.replace(self.path)

    def set(self, **fields: Any) -> None:
        with self._lock:
            self.data.update(fields)
            self.data["updated_at"] = time.time()
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data, indent=2, sort_keys=True))
            tmp.replace(self.path)


# --------------------------------------------------------------------------- #
# Docker helpers                                                              #
# --------------------------------------------------------------------------- #

def _run(cmd: list[str], *, check: bool = True, timeout: float | None = None,
         capture: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        capture_output=capture,
        text=True,
        check=check,
        timeout=timeout,
    )


def docker_inspect(name: str) -> dict[str, Any] | None:
    """Full docker inspect JSON or None if the container doesn't exist."""
    try:
        out = _run(["docker", "container", "inspect", name], check=True, capture=True).stdout
    except subprocess.CalledProcessError:
        return None
    data = json.loads(out)
    return data[0] if data else None


def docker_image_of(name: str) -> str | None:
    info = docker_inspect(name)
    if not info:
        return None
    return info.get("Config", {}).get("Image")


def ssh_cmd(peer: PeerSpec, *argv: str, timeout: float = 60.0) -> subprocess.CompletedProcess:
    ssh = [
        "ssh",
        "-o", "StrictHostKeyChecking=no", "-o", "BatchMode=yes",
        "-o", "UserKnownHostsFile=/dev/null", "-o", "LogLevel=ERROR",
        f"{peer.user}@{peer.host}",
    ]
    return _run(ssh + list(argv), check=False, capture=True, timeout=timeout)


def peer_inspect(peer: PeerSpec) -> dict[str, Any] | None:
    rc = ssh_cmd(peer, "docker", "container", "inspect", peer.container)
    if rc.returncode != 0 or not rc.stdout.strip():
        return None
    data = json.loads(rc.stdout)
    return data[0] if data else None


def capture_vllm_serve_argv(leader_container: str) -> list[str] | None:
    """Read the currently-running vllm serve argv from inside the head container.
    Returns None if no `vllm serve` process is found (e.g. engine hasn't booted
    yet, or it's been killed) — in which case a bounce shouldn't be attempted."""
    rc = _run(
        ["docker", "exec", leader_container, "bash", "-lc",
         "ps -ww -eo pid,args | grep -E '[v]llm serve' | head -1 | awk '{$1=\"\"; print substr($0,2)}'"],
        check=False,
    )
    if rc.returncode != 0 or not rc.stdout.strip():
        return None
    # The first token is the python interpreter path, second is "vllm", rest is args.
    # We want everything starting from "vllm serve …".
    parts = rc.stdout.strip().split()
    # Find the "vllm" token and slice from there onwards.
    for i, tok in enumerate(parts):
        if tok.endswith("/vllm") or tok == "vllm":
            return parts[i:]
    # Fallback — return the whole thing.
    return parts


def compose_run_cmd(spec: dict[str, Any], new_image: str, name: str,
                    entrypoint_cmd: list[str]) -> list[str]:
    """Build a ``docker run -d`` argv that mirrors the captured spec, with the
    image swapped to ``new_image`` and the container renamed to ``name``.

    ``entrypoint_cmd`` replaces the container's ``Cmd`` so Ray's start command
    can be re-issued verbatim (docker inspect gives us the Cmd but we pass it
    as positional args after the image; bash -lc wrapping is already in the
    captured Cmd thanks to the role using ``--entrypoint /bin/bash -c …``)."""
    host = spec["HostConfig"]
    cfg = spec["Config"]

    cmd: list[str] = ["docker", "run", "-d", "--name", name]
    # Networking
    net = host.get("NetworkMode") or "host"
    cmd += ["--network", str(net)]
    # Restart policy
    rp = (host.get("RestartPolicy") or {}).get("Name") or ""
    if rp and rp != "no":
        cmd += ["--restart", rp]
    # Shm size
    shm = int(host.get("ShmSize") or 0)
    if shm > 0:
        cmd += ["--shm-size", f"{shm}b"]
    # GPU requests: role uses --gpus all
    for req in host.get("DeviceRequests") or []:
        if req.get("Driver") == "nvidia" or "gpu" in (req.get("Capabilities") or [[]])[0]:
            cmd += ["--gpus", "all"]
            break
    # Env (flat list of "K=V" strings from inspect)
    for e in cfg.get("Env") or []:
        cmd += ["-e", e]
    # Mounts / binds
    for b in host.get("Binds") or []:
        cmd += ["-v", b]
    # Entrypoint (role uses /bin/bash)
    ep = cfg.get("Entrypoint")
    if ep and isinstance(ep, list) and ep:
        cmd += ["--entrypoint", ep[0]]
    # Image
    cmd += [new_image]
    # Command after image
    cmd += entrypoint_cmd
    return cmd


# --------------------------------------------------------------------------- #
# Metrics / quietness                                                         #
# --------------------------------------------------------------------------- #

_PROM_LINE_RE = re.compile(r"^([a-zA-Z_:][a-zA-Z0-9_:]*)(\{[^}]*\})?\s+([0-9eE+\-.]+)")


def parse_metrics(text: str) -> dict[str, float]:
    """Sum Prometheus values per metric name (labels merged)."""
    out: dict[str, float] = {}
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        m = _PROM_LINE_RE.match(line)
        if not m:
            continue
        name, _labels, val = m.groups()
        try:
            out[name] = out.get(name, 0.0) + float(val)
        except ValueError:
            continue
    return out


def fetch_metrics(url: str, timeout: float = 8.0) -> dict[str, float] | None:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return parse_metrics(r.read().decode("utf-8", errors="replace"))
    except (urllib.error.URLError, urllib.error.HTTPError, socket.timeout, ConnectionError, OSError):
        return None


def is_quiet(m: dict[str, float] | None) -> bool:
    if m is None:
        # API unreachable → no requests are being served → quiet by definition.
        return True
    running = m.get("vllm:num_requests_running", 0.0)
    waiting = m.get("vllm:num_requests_waiting", 0.0)
    return running == 0.0 and waiting == 0.0


# --------------------------------------------------------------------------- #
# Candidate selection                                                         #
# --------------------------------------------------------------------------- #

_NUMS_RE = re.compile(r"\d+")


def _tag_key(tag: str) -> tuple[int, ...]:
    nums = tuple(int(x) for x in _NUMS_RE.findall(tag))
    return nums or (0,)


def find_candidate(cfg: Config) -> tuple[str | None, float | None]:
    """Return (tag, first_seen_ready_at_epoch) for the newest ready tag in the
    ngc-image-sync state that beats ``current_image``. ``None`` if nothing new."""
    if cfg.pinned_tag:
        return cfg.pinned_tag, 0.0  # always "eligible", no stabilization

    try:
        data = json.loads(cfg.ngc_state_path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return None, None

    pat = re.compile(cfg.tag_pattern)
    best: tuple[str, float] | None = None
    prefix = f"{cfg.repo}:"
    for key, entry in (data.get("images") or {}).items():
        if not key.startswith(prefix):
            continue
        tag = key[len(prefix):]
        if tag.startswith("@"):  # internal entries like `@discovery`
            continue
        if not pat.match(tag):
            continue
        if entry.get("status") != "ready":
            continue
        first_ready = float(entry.get("updated_at") or 0)
        if best is None or _tag_key(tag) > _tag_key(best[0]):
            best = (tag, first_ready)
    if best is None:
        return None, None
    return best


# --------------------------------------------------------------------------- #
# Bounce                                                                      #
# --------------------------------------------------------------------------- #

@dataclass
class CapturedStack:
    leader_spec: dict[str, Any]
    leader_cmd: list[str]
    leader_serve_argv: list[str] | None
    peer_specs: list[tuple[PeerSpec, dict[str, Any], list[str]]]  # (peer, spec, cmd)


def capture_stack(cfg: Config) -> CapturedStack:
    leader = docker_inspect(cfg.leader_container)
    if leader is None:
        raise RuntimeError(f"leader container {cfg.leader_container} not found")
    leader_cmd = leader.get("Config", {}).get("Cmd") or []

    serve_argv = capture_vllm_serve_argv(cfg.leader_container)

    peer_specs: list[tuple[PeerSpec, dict[str, Any], list[str]]] = []
    for peer in cfg.peers:
        pspec = peer_inspect(peer)
        if pspec is None:
            raise RuntimeError(f"peer {peer.label} container {peer.container} not found")
        pcmd = pspec.get("Config", {}).get("Cmd") or []
        peer_specs.append((peer, pspec, pcmd))

    return CapturedStack(
        leader_spec=leader,
        leader_cmd=leader_cmd,
        leader_serve_argv=serve_argv,
        peer_specs=peer_specs,
    )


def bounce(cfg: Config, captured: CapturedStack, new_image: str, stop_event: "StopEvent") -> None:
    """Perform the actual container cutover."""

    # 1) Stop + rm workers on every peer.
    for peer, _spec, _cmd in captured.peer_specs:
        log.info("stopping peer worker %s on %s", peer.container, peer.label)
        ssh_cmd(peer, "docker", "stop", "-t", "30", peer.container)
        ssh_cmd(peer, "docker", "rm", "-f", peer.container)
        if stop_event.is_set():
            return

    # 2) Stop + rm leader head.
    log.info("stopping leader head %s", cfg.leader_container)
    _run(["docker", "stop", "-t", "30", cfg.leader_container], check=False)
    _run(["docker", "rm", "-f", cfg.leader_container], check=False)

    # 3) Run new head with the same spec + new image.
    head_cmd = compose_run_cmd(
        captured.leader_spec, new_image, cfg.leader_container, captured.leader_cmd,
    )
    log.info("starting new leader head on %s", new_image)
    _run(head_cmd, check=True)

    # 4) Wait for Ray GCS port on 127.0.0.1:6379 from inside the new head container.
    log.info("waiting up to %ds for Ray GCS inside head", cfg.ray_gcs_wait_sec)
    deadline = time.monotonic() + cfg.ray_gcs_wait_sec
    while time.monotonic() < deadline and not stop_event.is_set():
        rc = _run(
            ["docker", "exec", cfg.leader_container, "bash", "-lc",
             "ss -tln | awk 'NR>1{print $4}' | grep -E ':6379$' >/dev/null && echo up || true"],
            check=False,
        )
        if rc.returncode == 0 and "up" in (rc.stdout or ""):
            break
        time.sleep(2)
    else:
        if not stop_event.is_set():
            raise RuntimeError("Ray GCS did not appear on :6379 in the new head container")

    # 5) Start workers on each peer with their captured spec + new image.
    #    compose_run_cmd starts with ["docker", "run", ...] — we pass the whole
    #    argv to ssh so the remote `nvidia` shell executes `docker run` itself.
    for peer, pspec, pcmd in captured.peer_specs:
        if stop_event.is_set():
            return
        peer_cmd_argv = compose_run_cmd(pspec, new_image, peer.container, pcmd)
        log.info("starting new worker %s on %s (%s)", peer.container, peer.label, new_image)
        rc = ssh_cmd(peer, *peer_cmd_argv)
        if rc.returncode != 0:
            raise RuntimeError(
                f"failed to start {peer.container} on {peer.label}: "
                f"rc={rc.returncode} stdout={rc.stdout!r} stderr={rc.stderr!r}"
            )

    # 6) Wait for Ray cluster to show all nodes.
    log.info("waiting up to %ds for Ray cluster to include all %d nodes",
             cfg.ray_cluster_wait_sec, 1 + len(cfg.peers))
    expected = 1 + len(cfg.peers)
    deadline = time.monotonic() + cfg.ray_cluster_wait_sec
    while time.monotonic() < deadline and not stop_event.is_set():
        rc = _run(
            ["docker", "exec", cfg.leader_container, "ray", "status"],
            check=False,
        )
        if rc.returncode == 0 and rc.stdout.count("node_") >= expected:
            break
        time.sleep(5)
    else:
        if not stop_event.is_set():
            raise RuntimeError(f"Ray cluster did not reach {expected} nodes")

    # 7) Re-exec vllm serve inside the head with the captured argv.
    if not captured.leader_serve_argv:
        log.warning("no prior vllm serve argv captured; skipping re-exec (operator must start vllm serve)")
        return
    argv = captured.leader_serve_argv
    # Persist stdout to a file inside the container for diagnostics.
    quoted = " ".join(_sh_quote(a) for a in argv)
    log.info("re-executing inside head: %s", quoted)
    _run(
        ["docker", "exec", "-d", cfg.leader_container, "bash", "-lc",
         f"exec > /root/vllm-serve.log 2>&1; {quoted}"],
        check=True,
    )

    # 8) Wait for /v1/models to respond 200.
    log.info("waiting up to %ds for /v1/models 200", cfg.vllm_api_wait_sec)
    deadline = time.monotonic() + cfg.vllm_api_wait_sec
    metrics_host = "127.0.0.1:8000"  # mirrors default; refined below if argv says otherwise
    # Best-effort port extraction from the serve argv.
    for i, tok in enumerate(argv):
        if tok == "--port" and i + 1 < len(argv):
            metrics_host = f"127.0.0.1:{argv[i + 1]}"
            break
    while time.monotonic() < deadline and not stop_event.is_set():
        try:
            with urllib.request.urlopen(f"http://{metrics_host}/v1/models", timeout=5.0) as r:
                if r.status == 200:
                    break
        except Exception:
            pass
        time.sleep(5)
    else:
        if not stop_event.is_set():
            raise RuntimeError(f"vllm API never responded 200 at {metrics_host}")


def _sh_quote(s: str) -> str:
    import shlex
    return shlex.quote(s)


# --------------------------------------------------------------------------- #
# Main loop                                                                   #
# --------------------------------------------------------------------------- #

class StopEvent:
    def __init__(self) -> None:
        self._stop = False

    def set(self) -> None:
        self._stop = True

    def is_set(self) -> bool:
        return self._stop


def loop(cfg_path: Path, state: StateStore, stop_event: StopEvent) -> None:
    last_mtime = 0.0
    cfg: Config | None = None

    while not stop_event.is_set():
        try:
            mtime = cfg_path.stat().st_mtime
            if mtime != last_mtime:
                log.info("(re)loading config %s", cfg_path)
                cfg = Config.load(cfg_path)
                last_mtime = mtime
                log.info(
                    "enabled=%s, pin=%s, stabilization=%ds, quiet=%ds, poll=%ds",
                    cfg.enabled, cfg.pinned_tag, cfg.stabilization_sec,
                    cfg.quiet_window_sec, cfg.poll_interval_sec,
                )
        except Exception as exc:
            log.error("config reload failed: %s", exc)
            if cfg is None:
                time.sleep(15); continue

        assert cfg is not None
        current = docker_image_of(cfg.leader_container)
        state.set(
            current_image=current,
            enabled=cfg.enabled,
            leader_container=cfg.leader_container,
        )

        if not cfg.enabled:
            state.set(status=STATE_IDLE, candidate_tag=None, candidate_since=None)
            _interruptible_sleep(stop_event, cfg.poll_interval_sec)
            continue

        if current is None:
            log.info("leader container %s not running; nothing to promote yet", cfg.leader_container)
            state.set(status=STATE_IDLE)
            _interruptible_sleep(stop_event, cfg.poll_interval_sec)
            continue

        # Find the newest candidate tag in the ngc-image-sync state.
        cand, cand_ready_at = find_candidate(cfg)
        if cand is None:
            state.set(status=STATE_IDLE, candidate_tag=None, candidate_since=None,
                      last_error=None)
            _interruptible_sleep(stop_event, cfg.poll_interval_sec)
            continue

        wanted_image = f"{cfg.repo}:{cand}"
        if wanted_image == current:
            # Already running the desired image.
            state.set(status=STATE_READY, candidate_tag=cand, candidate_since=None,
                      last_error=None)
            _interruptible_sleep(stop_event, cfg.poll_interval_sec)
            continue

        # Stabilization: require the candidate to have been ready for at least
        # stabilization_sec (unless the operator pinned it, in which case we
        # promote immediately).
        age = time.time() - (cand_ready_at or 0)
        if cfg.pinned_tag is None and age < cfg.stabilization_sec:
            state.set(status=STATE_CANDIDATE, candidate_tag=cand,
                      candidate_since=cand_ready_at,
                      reason=f"waiting {int(cfg.stabilization_sec - age)}s more for stabilization")
            log.info("candidate %s not yet stable (%.0fs of %ds)", cand, age, cfg.stabilization_sec)
            _interruptible_sleep(stop_event, cfg.poll_interval_sec)
            continue

        # Wait for quiet window.
        state.set(status=STATE_WAITING_QUIET, candidate_tag=cand,
                  candidate_since=cand_ready_at,
                  reason=f"observing {cfg.quiet_window_sec}s quiet window")
        ok = wait_for_quiet(cfg, state, stop_event)
        if stop_event.is_set():
            return
        if not ok:
            state.set(status=STATE_CANDIDATE,
                      reason=f"API busy beyond max_wait_for_quiet_sec={cfg.max_wait_for_quiet_sec}; will retry later")
            _interruptible_sleep(stop_event, cfg.poll_interval_sec)
            continue

        # All pre-conditions satisfied — promote.
        state.set(status=STATE_PROMOTING,
                  reason=f"bouncing to {wanted_image}")
        try:
            captured = capture_stack(cfg)
            log.info("captured running stack (head + %d peers); bouncing to %s",
                     len(captured.peer_specs), wanted_image)
            bounce(cfg, captured, wanted_image, stop_event)
            state.set(
                status=STATE_READY,
                current_image=wanted_image,
                last_promotion={
                    "from": current,
                    "to": wanted_image,
                    "at": time.time(),
                },
                candidate_tag=cand,
                reason="promoted successfully",
                last_error=None,
            )
            log.info("promotion complete: %s → %s", current, wanted_image)
        except Exception as exc:
            log.exception("promotion failed: %s", exc)
            state.set(
                status=STATE_ERROR,
                last_error=str(exc),
                reason="promotion failed — operator triage required",
            )

        _interruptible_sleep(stop_event, cfg.poll_interval_sec)


def wait_for_quiet(cfg: Config, state: StateStore, stop_event: StopEvent) -> bool:
    """Return True if we saw ``quiet_window_sec`` of continuous quiet, False if
    we gave up after ``max_wait_for_quiet_sec``."""
    needed = max(1, cfg.quiet_window_sec // max(cfg.quiet_sample_sec, 1))
    consecutive = 0
    started = time.monotonic()
    last_success_total = None
    while not stop_event.is_set():
        if time.monotonic() - started > cfg.max_wait_for_quiet_sec:
            return False
        m = fetch_metrics(cfg.metrics_url)
        quiet = is_quiet(m)
        # Additionally require success_total to be stable across samples if we
        # can read it at all; that catches the case where num_requests_running
        # was 0 between requests.
        if quiet and m is not None:
            cur_success = m.get("vllm:request_success_total", 0.0)
            if last_success_total is not None and cur_success != last_success_total:
                quiet = False
            last_success_total = cur_success
        if quiet:
            consecutive += 1
            state.set(quiet_consecutive=consecutive, quiet_needed=needed,
                      reason=f"quiet sample {consecutive}/{needed}")
            if consecutive >= needed:
                return True
        else:
            consecutive = 0
            state.set(quiet_consecutive=0, quiet_needed=needed,
                      reason="requests in flight; resetting quiet counter")
            last_success_total = m.get("vllm:request_success_total", 0.0) if m else None
        time.sleep(cfg.quiet_sample_sec)
    return False


def _interruptible_sleep(stop_event: StopEvent, secs: float) -> None:
    deadline = time.monotonic() + secs
    while not stop_event.is_set() and time.monotonic() < deadline:
        time.sleep(min(1.0, deadline - time.monotonic()))


# --------------------------------------------------------------------------- #
# Entry                                                                       #
# --------------------------------------------------------------------------- #

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="vLLM stack auto-upgrade daemon.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE)
    parser.add_argument("-v", "--verbose", action="count", default=0)
    args = parser.parse_args(argv)

    level = logging.DEBUG if args.verbose >= 2 else logging.INFO
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(message)s")

    for tool in ("docker", "ssh"):
        if shutil.which(tool) is None:
            print(f"vllm-stack-autoupgrade: required tool '{tool}' not in PATH", file=sys.stderr)
            return 3

    if not args.config.exists():
        log.error("config file %s does not exist", args.config)
        return 2

    stop_event = StopEvent()

    def handle_signal(signum: int, _frame: Any) -> None:
        log.info("received %s — requesting shutdown", signal.Signals(signum).name)
        stop_event.set()

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    state = StateStore(args.state)
    log.info("vllm-stack-autoupgrade started, config=%s state=%s", args.config, args.state)
    try:
        loop(args.config, state, stop_event)
    finally:
        log.info("vllm-stack-autoupgrade exiting")
    return 0


if __name__ == "__main__":
    sys.exit(main())
