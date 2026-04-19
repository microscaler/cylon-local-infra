#!/usr/bin/env python3
"""
hf-prefetch-service
===================

Long-running, config-driven Hugging Face model prefetch + peer-sync daemon for
Cylon's DGX Spark cluster. Runs on the leader Spark under systemd as `nvidia`.

Responsibilities
----------------
* Read a YAML config listing models to keep in the local HF cache.
* For each model, resume/download via the NGC vLLM image's `hf` CLI
  (`docker run --rm nvcr.io/nvidia/vllm:<tag> hf download <repo>`).
* On successful download, `rsync` the repo's hub subtree to every configured
  peer over the QSFP interconnect (typically `nvidia@169.254.x`).
* Re-read the config file when its mtime changes, so adding a model is a
  `ansible-playbook` + systemd reload away — operators can poll the state
  JSON instead of blocking in Ansible.
* Never re-download a model that's already cached + synced.

Files
-----
* Config (input, operator-writable via Ansible): ``/etc/hf-prefetch/config.yaml``
* State  (output, JSON, operator-pollable):      ``/var/lib/hf-prefetch/state.json``
* Logs:                                          ``journalctl -u hf-prefetch``

State transitions per model
---------------------------
    unknown -> downloading -> (local cache valid) -> syncing -> ready
                   \\---(failure)---> error (with retry backoff)
    ready repos whose cache disappeared (e.g. manual `hf cache delete`) drop
    back to `downloading` on the next reconciliation.

Exit codes
----------
    0  normal shutdown via SIGTERM / SIGINT
    2  unrecoverable config error at startup (first-ever read)
    3  missing runtime dep (docker / rsync / pyyaml)

The service only uses the standard library plus ``pyyaml`` (Ubuntu apt
``python3-yaml``). No host-side `pip install` — everything heavy runs inside
the NGC image.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import yaml  # type: ignore[import-not-found]
except ImportError:
    print(
        "hf-prefetch-service: python module 'yaml' is required "
        "(apt install python3-yaml)",
        file=sys.stderr,
    )
    sys.exit(3)


log = logging.getLogger("hf-prefetch")

DEFAULT_CONFIG = Path("/etc/hf-prefetch/config.yaml")
DEFAULT_STATE = Path("/var/lib/hf-prefetch/state.json")

STATE_UNKNOWN = "unknown"
STATE_DOWNLOADING = "downloading"
STATE_SYNCING = "syncing"
STATE_READY = "ready"
STATE_ERROR = "error"

# Retry backoff for transient errors, in seconds. Bounded so we keep retrying
# forever — network can come back at any time.
RETRY_BACKOFF = [30, 60, 120, 300, 600, 900]
PROGRESS_INTERVAL_SEC = 5.0


# --------------------------------------------------------------------------- #
# Utilities                                                                   #
# --------------------------------------------------------------------------- #

def repo_to_cache_dirname(repo: str) -> str:
    """HF cache layout: ``hub/models--<org>--<name>``."""
    return "models--" + repo.replace("/", "--")


def human_size(n: int) -> str:
    step = 1024.0
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if n < step:
            return f"{n:.1f}{unit}"
        n /= step  # type: ignore[assignment]
    return f"{n:.1f}PiB"


def require_tool(name: str) -> None:
    if shutil.which(name) is None:
        print(f"hf-prefetch-service: required tool '{name}' not in PATH", file=sys.stderr)
        sys.exit(3)


# --------------------------------------------------------------------------- #
# Config                                                                      #
# --------------------------------------------------------------------------- #

@dataclass
class ModelEntry:
    repo: str
    revision: str | None = None

    @classmethod
    def parse(cls, raw: Any) -> "ModelEntry":
        if isinstance(raw, str):
            return cls(repo=raw)
        if isinstance(raw, dict) and "repo" in raw:
            return cls(repo=str(raw["repo"]), revision=raw.get("revision"))
        raise ValueError(f"invalid model entry: {raw!r}")


@dataclass
class SyncTarget:
    host: str
    user: str = "nvidia"
    cache_dir: str = "/home/nvidia/.cache/huggingface/hub"
    name: str | None = None  # optional label for logs

    @property
    def label(self) -> str:
        return self.name or self.host


@dataclass
class Config:
    cache_dir: Path
    image: str
    models: list[ModelEntry] = field(default_factory=list)
    sync_targets: list[SyncTarget] = field(default_factory=list)
    poll_interval_sec: int = 30
    # Extra env to pass into the `hf` container (e.g. HF_TOKEN via EnvironmentFile).
    hf_env: dict[str, str] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> "Config":
        with path.open() as f:
            raw = yaml.safe_load(f) or {}
        cache_dir = Path(raw.get("cache_dir", "/home/nvidia/.cache/huggingface/hub"))
        image = str(raw.get("image", "nvcr.io/nvidia/vllm:26.01-py3"))
        models = [ModelEntry.parse(m) for m in raw.get("models") or []]
        targets_raw = raw.get("sync_targets") or []
        targets = [
            SyncTarget(
                host=str(t["host"]),
                user=str(t.get("user", "nvidia")),
                cache_dir=str(t.get("cache_dir", "/home/nvidia/.cache/huggingface/hub")),
                name=t.get("name"),
            )
            for t in targets_raw
            if isinstance(t, dict) and t.get("host")
        ]
        poll = int(raw.get("poll_interval_sec", 30))
        hf_env = {str(k): str(v) for k, v in (raw.get("hf_env") or {}).items()}
        return cls(
            cache_dir=cache_dir,
            image=image,
            models=models,
            sync_targets=targets,
            poll_interval_sec=max(5, poll),
            hf_env=hf_env,
        )


# --------------------------------------------------------------------------- #
# State                                                                       #
# --------------------------------------------------------------------------- #

class StateStore:
    """JSON file describing per-model status. Atomic writes via tmp+rename.
    Thread-safe: the progress heartbeat thread and the main reconciliation
    loop both call ``set()`` / ``save()``."""

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
                log.warning("state file %s is corrupt, starting fresh", self.path)
        return {
            "schema": 1,
            "host": socket.gethostname(),
            "pid": os.getpid(),
            "started_at": time.time(),
            "updated_at": time.time(),
            "models": {},
        }

    def _save_locked(self) -> None:
        self.data["updated_at"] = time.time()
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=2, sort_keys=True))
        tmp.replace(self.path)

    def save(self) -> None:
        with self._lock:
            self._save_locked()

    def get(self, repo: str) -> dict[str, Any]:
        with self._lock:
            return dict(self.data["models"].get(repo, {}))

    def set(self, repo: str, **fields: Any) -> None:
        with self._lock:
            cur = self.data["models"].setdefault(repo, {})
            cur.update(fields)
            cur["updated_at"] = time.time()
            self._save_locked()

    def drop(self, repo: str) -> None:
        with self._lock:
            if repo in self.data["models"]:
                del self.data["models"][repo]
                self._save_locked()

    def all_ready(self, repos: list[str]) -> bool:
        return all(self.get(r).get("status") == STATE_READY for r in repos)

    def summary(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for m in self.data["models"].values():
            s = m.get("status", STATE_UNKNOWN)
            counts[s] = counts.get(s, 0) + 1
        return counts


# --------------------------------------------------------------------------- #
# Cache validation                                                            #
# --------------------------------------------------------------------------- #

def cache_status(cache_dir: Path, repo: str) -> tuple[bool, str]:
    """Return (cache_ok, reason). ``cache_ok`` is True iff the local cache has a
    complete snapshot with no ``.incomplete`` blobs."""
    repo_dir = cache_dir / repo_to_cache_dirname(repo)
    if not repo_dir.is_dir():
        return False, "no repo dir"

    blobs = repo_dir / "blobs"
    if blobs.is_dir():
        for blob in blobs.iterdir():
            if blob.name.endswith(".incomplete"):
                return False, f".incomplete blob present ({blob.name[:16]}…)"

    snapshots = repo_dir / "snapshots"
    if not snapshots.is_dir():
        return False, "no snapshots dir"
    snaps = [s for s in snapshots.iterdir() if s.is_dir()]
    if not snaps:
        return False, "no snapshot directories"
    # Expect at least a config.json in some snapshot.
    for snap in snaps:
        if (snap / "config.json").exists():
            return True, f"snapshot {snap.name[:8]} has config.json, no .incomplete blobs"
    return False, "no snapshot with config.json"


def cache_size_bytes(cache_dir: Path, repo: str) -> int:
    """Apparent size on disk of a repo's cache subtree (follows .incomplete too)."""
    repo_dir = cache_dir / repo_to_cache_dirname(repo)
    if not repo_dir.is_dir():
        return 0
    total = 0
    for dirpath, _, filenames in os.walk(repo_dir, followlinks=False):
        for fn in filenames:
            fp = Path(dirpath) / fn
            try:
                total += fp.stat(follow_symlinks=False).st_size
            except (FileNotFoundError, PermissionError):
                continue
    return total


# --------------------------------------------------------------------------- #
# Subprocess runners                                                          #
# --------------------------------------------------------------------------- #

class Runner:
    """Wraps long-running subprocesses so a SIGTERM to the service terminates
    any in-flight ``docker run`` / ``rsync`` cleanly. For `docker run` we also
    know the container name, so on shutdown we `docker stop` the container so
    it doesn't outlive the CLI client (`docker run --rm` is a wrapper — killing
    the CLI does not stop the daemon-managed container)."""

    def __init__(self) -> None:
        self._current: subprocess.Popen[str] | None = None
        self.current_container: str | None = None

    def run(self, cmd: list[str], *, env_extra: dict[str, str] | None = None,
            expect_success: bool = True) -> int:
        """Run ``cmd``, stream its output to the service log. Returns exit code.
        If the service receives SIGTERM, the child is SIGTERM'd too.
        When ``expect_success`` is False, non-zero exit is logged at DEBUG."""
        env = os.environ.copy()
        if env_extra:
            env.update(env_extra)
        log.info("exec: %s", " ".join(cmd))
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=env,
        )
        self._current = proc
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                sys.stdout.write(line)
                sys.stdout.flush()
            rc = proc.wait()
        finally:
            self._current = None
        if rc != 0 and not expect_success:
            log.debug("non-success rc=%s (expected): %s", rc, " ".join(cmd))
        return rc

    def terminate(self) -> None:
        # If a docker run is in flight, stopping the daemon-managed container
        # is the authoritative way to end the work. We do this before SIGTERM'ing
        # the `docker run` CLI so the CLI exits cleanly.
        name = self.current_container
        if name:
            log.info("stopping in-flight container %s", name)
            try:
                subprocess.run(
                    ["docker", "stop", "-t", "10", name],
                    timeout=30,
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
                log.warning("docker stop %s failed: %s", name, exc)

        proc = self._current
        if proc and proc.poll() is None:
            log.info("terminating child pid=%s", proc.pid)
            try:
                proc.terminate()
            except ProcessLookupError:
                return
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                log.warning("child pid=%s didn't exit on SIGTERM, sending SIGKILL", proc.pid)
                proc.kill()


def _container_name(repo: str) -> str:
    # docker accepts [a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}; HF repos have `/` and `.`.
    return ("hf-prefetch-" + repo.replace("/", "_").replace(".", "_"))[:63]


def hf_download(runner: Runner, cfg: Config, entry: ModelEntry,
                state: StateStore | None = None) -> int:
    """Launch `hf download <repo>` inside the NGC image. Resumes on partials.
    Any leftover container from a prior crashed run is removed first so the
    fixed --name doesn't collide.

    While the download is in flight, a heartbeat thread updates
    ``state.models[repo].cache_bytes`` every ``PROGRESS_INTERVAL_SEC`` seconds
    so operators polling ``state.json`` see live progress instead of a number
    frozen at ``0`` until the process exits."""
    # cache_dir is the `hub` dir; mount its parent so the container sees
    # /root/.cache/huggingface{hub,xet,...} and hf-xet can use its staging area.
    host_hf_home = cfg.cache_dir.parent
    name = _container_name(entry.repo)

    # Idempotent pre-clean. Swallow exit code — absence-of-container is fine.
    runner.run(["docker", "rm", "-f", name], expect_success=False)

    cmd = [
        "docker", "run", "--rm",
        "--name", name,
        "--network", "host",
        "-v", f"{host_hf_home}:/root/.cache/huggingface",
        "--entrypoint", "hf",
    ]
    for k, v in cfg.hf_env.items():
        cmd.extend(["-e", f"{k}={v}"])
    cmd.append(cfg.image)
    cmd += ["download", entry.repo]
    if entry.revision:
        cmd += ["--revision", entry.revision]

    stop_heartbeat = threading.Event()

    def _progress_heartbeat() -> None:
        # Runs for the lifetime of the subprocess below. Reads the on-disk size
        # (which grows even while .incomplete blobs are being streamed by hf-xet).
        start = time.monotonic()
        last_bytes = -1
        while not stop_heartbeat.is_set():
            try:
                cur = cache_size_bytes(cfg.cache_dir, entry.repo)
            except Exception as exc:
                log.debug("progress heartbeat stat failed: %s", exc)
                cur = last_bytes
            if state is not None and cur != last_bytes:
                elapsed = max(1.0, time.monotonic() - start)
                state.set(
                    entry.repo,
                    status=STATE_DOWNLOADING,
                    cache_bytes=cur,
                    bytes_per_sec=int((cur - max(last_bytes, 0)) / PROGRESS_INTERVAL_SEC)
                        if last_bytes > 0 else None,
                )
                last_bytes = cur
            stop_heartbeat.wait(timeout=PROGRESS_INTERVAL_SEC)

    heartbeat = threading.Thread(target=_progress_heartbeat, name="hf-progress", daemon=True)
    heartbeat.start()

    # Track the container name so SIGTERM during a download can stop it.
    runner.current_container = name
    try:
        return runner.run(cmd)
    finally:
        runner.current_container = None
        stop_heartbeat.set()
        heartbeat.join(timeout=PROGRESS_INTERVAL_SEC + 2)
        # Final on-disk size reading after the process has exited.
        if state is not None:
            try:
                state.set(entry.repo, cache_bytes=cache_size_bytes(cfg.cache_dir, entry.repo))
            except Exception:
                pass


def rsync_to_peer(runner: Runner, cfg: Config, repo: str, target: SyncTarget) -> int:
    """rsync the repo's hub subtree from this host to a peer. ``-a`` preserves
    the HF cache layout (symlinks inside ``snapshots/`` → ``blobs/``)."""
    src = str(cfg.cache_dir / repo_to_cache_dirname(repo)) + "/"
    dest = f"{target.user}@{target.host}:{target.cache_dir}/{repo_to_cache_dirname(repo)}/"
    ssh_opts = (
        "ssh -o StrictHostKeyChecking=no -o BatchMode=yes "
        "-o UserKnownHostsFile=/dev/null -o LogLevel=ERROR"
    )
    cmd = [
        "rsync", "-a", "--delete", "--partial", "--partial-dir=.rsync-partial",
        "--info=stats2,progress0",
        "-e", ssh_opts,
        src, dest,
    ]
    return runner.run(cmd)


# --------------------------------------------------------------------------- #
# Reconciliation                                                              #
# --------------------------------------------------------------------------- #

@dataclass
class BackoffTracker:
    failures: dict[str, int] = field(default_factory=dict)
    last_attempt: dict[str, float] = field(default_factory=dict)

    def ready(self, repo: str, now: float) -> bool:
        n = self.failures.get(repo, 0)
        if n == 0:
            return True
        wait = RETRY_BACKOFF[min(n - 1, len(RETRY_BACKOFF) - 1)]
        return (now - self.last_attempt.get(repo, 0)) >= wait

    def succeed(self, repo: str) -> None:
        self.failures.pop(repo, None)
        self.last_attempt.pop(repo, None)

    def fail(self, repo: str, now: float) -> None:
        self.failures[repo] = self.failures.get(repo, 0) + 1
        self.last_attempt[repo] = now


def reconcile_once(
    cfg: Config,
    state: StateStore,
    runner: Runner,
    backoff: BackoffTracker,
    stop_event: "StopEvent",
) -> None:
    """Single reconciliation pass over all models in the config."""
    configured = {m.repo for m in cfg.models}

    # Drop state for repos that were removed from the config; leave the cache
    # on disk (operator-initiated cleanup via `hf cache delete`, not us).
    for known in list(state.data["models"]):
        if known not in configured:
            log.info("dropping state for unconfigured repo %s", known)
            state.drop(known)

    for entry in cfg.models:
        if stop_event.is_set():
            return

        repo = entry.repo
        now = time.time()

        ok, reason = cache_status(cfg.cache_dir, repo)
        cur_state = state.get(repo).get("status")

        if ok and cur_state == STATE_READY:
            continue

        if ok and cur_state != STATE_READY:
            # Cache is complete but we haven't (re)synced yet.
            log.info("%s cache already complete (%s); will sync", repo, reason)
        else:
            # Cache missing or incomplete → download (or resume).
            if not backoff.ready(repo, now):
                continue
            size_before = cache_size_bytes(cfg.cache_dir, repo)
            state.set(
                repo,
                status=STATE_DOWNLOADING,
                reason="hf download" + (" (resume)" if size_before > 0 else ""),
                cache_bytes=size_before,
                image=cfg.image,
            )
            rc = hf_download(runner, cfg, entry, state=state)
            if stop_event.is_set():
                return
            if rc != 0:
                backoff.fail(repo, now)
                state.set(
                    repo,
                    status=STATE_ERROR,
                    reason=f"hf download exited rc={rc}",
                    failures=backoff.failures.get(repo, 0),
                )
                continue
            ok, reason = cache_status(cfg.cache_dir, repo)
            if not ok:
                backoff.fail(repo, now)
                state.set(
                    repo,
                    status=STATE_ERROR,
                    reason=f"download finished but cache invalid: {reason}",
                    failures=backoff.failures.get(repo, 0),
                )
                continue

        # --- sync phase --------------------------------------------------- #
        sync_ok = True
        for target in cfg.sync_targets:
            if stop_event.is_set():
                return
            state.set(
                repo,
                status=STATE_SYNCING,
                reason=f"rsync → {target.label}",
                cache_bytes=cache_size_bytes(cfg.cache_dir, repo),
            )
            rc = rsync_to_peer(runner, cfg, repo, target)
            if stop_event.is_set():
                return
            if rc != 0:
                sync_ok = False
                backoff.fail(repo, now)
                state.set(
                    repo,
                    status=STATE_ERROR,
                    reason=f"rsync {target.label} rc={rc}",
                    failures=backoff.failures.get(repo, 0),
                )
                break

        if sync_ok:
            backoff.succeed(repo)
            state.set(
                repo,
                status=STATE_READY,
                reason="cached + synced" if cfg.sync_targets else "cached (no peers configured)",
                cache_bytes=cache_size_bytes(cfg.cache_dir, repo),
                failures=0,
            )
            log.info("%s READY (%s on disk)", repo, human_size(cache_size_bytes(cfg.cache_dir, repo)))


# --------------------------------------------------------------------------- #
# Event loop                                                                  #
# --------------------------------------------------------------------------- #

class StopEvent:
    def __init__(self) -> None:
        self._stop = False

    def set(self) -> None:
        self._stop = True

    def is_set(self) -> bool:
        return self._stop


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Hugging Face prefetch + peer-sync daemon.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE)
    parser.add_argument("--once", action="store_true",
                        help="Run a single reconciliation pass and exit (for tests / oneshot mode).")
    parser.add_argument("-v", "--verbose", action="count", default=0)
    args = parser.parse_args(argv)

    level = logging.DEBUG if args.verbose >= 2 else (logging.INFO if args.verbose else logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    require_tool("docker")
    require_tool("rsync")

    if not args.config.exists():
        log.error("config file %s does not exist", args.config)
        return 2

    stop_event = StopEvent()
    runner = Runner()
    backoff = BackoffTracker()

    def handle_signal(signum: int, _frame: Any) -> None:
        name = signal.Signals(signum).name
        log.info("received %s — requesting shutdown", name)
        stop_event.set()
        runner.terminate()

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    state = StateStore(args.state)
    log.info("hf-prefetch-service started, config=%s state=%s", args.config, args.state)

    last_mtime = 0.0
    cfg: Config | None = None
    while not stop_event.is_set():
        try:
            mtime = args.config.stat().st_mtime
            if mtime != last_mtime:
                log.info("(re)loading config %s", args.config)
                cfg = Config.load(args.config)
                last_mtime = mtime
                log.info(
                    "config: %d model(s), %d sync target(s), image=%s, poll=%ss",
                    len(cfg.models), len(cfg.sync_targets), cfg.image, cfg.poll_interval_sec,
                )
        except Exception as exc:
            log.error("failed to load config: %s", exc)
            if cfg is None:
                time.sleep(15)
                continue

        assert cfg is not None
        try:
            reconcile_once(cfg, state, runner, backoff, stop_event)
        except Exception:
            log.exception("reconciliation failed")

        if args.once:
            return 0

        log.info("summary: %s", state.summary())
        # Sleep in small chunks so signals are responsive.
        deadline = time.monotonic() + cfg.poll_interval_sec
        while not stop_event.is_set() and time.monotonic() < deadline:
            time.sleep(min(1.0, deadline - time.monotonic()))

    log.info("hf-prefetch-service exiting")
    return 0


if __name__ == "__main__":
    sys.exit(main())
