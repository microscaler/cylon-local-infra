#!/usr/bin/env python3
"""
ngc-image-sync-service
======================

Long-running, config-driven daemon that keeps the configured OCI/Docker
container images on the leader Spark current, and replicates new tags to every
peer Spark over the QSFP interconnect.

Responsibilities
----------------
* Periodically (default weekly) query each tracked repository for the list of
  tags it publishes, using the OCI Distribution v2 token-auth flow
  (anonymous works for public NGC images like ``nvcr.io/nvidia/vllm``).
* Select the ``keep_latest`` newest tags matching a regex (e.g. ``YY.MM-py3``)
  plus any ``always_keep`` explicit tags.
* For each selected tag that isn't already on the local Docker daemon, ``docker
  pull`` it here.
* For each peer, ``docker save <repo>:<tag> | ssh <peer> docker load`` so the
  image exists on every node without a second WAN pull.
* Optionally prune local tags that are no longer in the keep set (skipped if
  a container is currently using the image — Docker refuses).

Mirrors the design of ``hf_prefetch_service.py`` — same dependencies (stdlib +
pyyaml), same state-file / config-reload / signal-handling patterns — so
operators only need to learn one shape of service.

Files
-----
* Config:  ``/etc/ngc-image-sync/config.yaml`` (managed by Ansible)
* State:   ``/var/lib/ngc-image-sync/state.json`` (JSON, poll with ``jq``)
* Logs:    ``journalctl -u ngc-image-sync``

State transitions per (repo, tag)
---------------------------------
    unknown -> pulling -> (local) -> syncing -> ready
                 \\---(failure)--> error  (exponential backoff)

Tags that are no longer "kept" transition to ``pruned`` (or stay ``ready`` if
they're in use by a running container).

Exit codes
----------
    0  normal shutdown (SIGTERM / SIGINT)
    2  first-time config error
    3  missing runtime dep (docker / ssh / pyyaml)
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
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import yaml  # type: ignore[import-not-found]
except ImportError:
    print(
        "ngc-image-sync-service: python module 'yaml' is required "
        "(apt install python3-yaml)",
        file=sys.stderr,
    )
    sys.exit(3)


log = logging.getLogger("ngc-image-sync")

DEFAULT_CONFIG = Path("/etc/ngc-image-sync/config.yaml")
DEFAULT_STATE = Path("/var/lib/ngc-image-sync/state.json")

STATE_UNKNOWN = "unknown"
STATE_PULLING = "pulling"
STATE_SYNCING = "syncing"
STATE_READY = "ready"
STATE_ERROR = "error"
STATE_PRUNED = "pruned"

# Retry backoff for transient pull/sync failures. We keep trying forever.
RETRY_BACKOFF = [60, 300, 900, 1800, 3600]
DEFAULT_POLL_INTERVAL_SEC = 7 * 24 * 3600  # weekly

# WWW-Authenticate: Bearer realm="...",service="...",scope="..."
_WWW_AUTH_PARAM_RE = re.compile(r'(\w+)="([^"]*)"')


# --------------------------------------------------------------------------- #
# Config                                                                      #
# --------------------------------------------------------------------------- #

@dataclass
class ImageSpec:
    repo: str
    tag_pattern: str = r".*"
    keep_latest: int = 2
    always_keep: list[str] = field(default_factory=list)
    prune_stale: bool = False  # operator opt-in; default off

    @property
    def registry(self) -> str:
        # nvcr.io/nvidia/vllm → nvcr.io
        return self.repo.split("/", 1)[0]

    @property
    def name(self) -> str:
        # nvcr.io/nvidia/vllm → nvidia/vllm
        return self.repo.split("/", 1)[1]

    @classmethod
    def parse(cls, raw: Any) -> "ImageSpec":
        if not isinstance(raw, dict) or "repo" not in raw:
            raise ValueError(f"invalid image entry (need 'repo'): {raw!r}")
        return cls(
            repo=str(raw["repo"]),
            tag_pattern=str(raw.get("tag_pattern", r".*")),
            keep_latest=int(raw.get("keep_latest", 2)),
            always_keep=[str(t) for t in (raw.get("always_keep") or [])],
            prune_stale=bool(raw.get("prune_stale", False)),
        )


@dataclass
class SyncTarget:
    host: str
    user: str = "nvidia"
    name: str | None = None

    @property
    def label(self) -> str:
        return self.name or self.host


@dataclass
class Config:
    images: list[ImageSpec] = field(default_factory=list)
    sync_targets: list[SyncTarget] = field(default_factory=list)
    poll_interval_sec: int = DEFAULT_POLL_INTERVAL_SEC
    # Seconds to sleep on first run after service start before beginning work.
    # Useful so a unit restart doesn't thundering-herd the registry.
    startup_delay_sec: int = 0

    @classmethod
    def load(cls, path: Path) -> "Config":
        with path.open() as f:
            raw = yaml.safe_load(f) or {}
        images = [ImageSpec.parse(i) for i in raw.get("images") or []]
        targets_raw = raw.get("sync_targets") or []
        targets = [
            SyncTarget(
                host=str(t["host"]),
                user=str(t.get("user", "nvidia")),
                name=t.get("name"),
            )
            for t in targets_raw
            if isinstance(t, dict) and t.get("host")
        ]
        poll = int(raw.get("poll_interval_sec", DEFAULT_POLL_INTERVAL_SEC))
        return cls(
            images=images,
            sync_targets=targets,
            poll_interval_sec=max(60, poll),
            startup_delay_sec=max(0, int(raw.get("startup_delay_sec", 0))),
        )


# --------------------------------------------------------------------------- #
# State                                                                       #
# --------------------------------------------------------------------------- #

class StateStore:
    """JSON state. Keyed by ``<repo>:<tag>``. Thread-safe."""

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
            "images": {},
            "last_poll_at": None,
            "next_poll_at": None,
        }

    def _save_locked(self) -> None:
        self.data["updated_at"] = time.time()
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=2, sort_keys=True))
        tmp.replace(self.path)

    def save(self) -> None:
        with self._lock:
            self._save_locked()

    def get(self, key: str) -> dict[str, Any]:
        with self._lock:
            return dict(self.data["images"].get(key, {}))

    def set(self, key: str, **fields: Any) -> None:
        with self._lock:
            cur = self.data["images"].setdefault(key, {})
            cur.update(fields)
            cur["updated_at"] = time.time()
            self._save_locked()

    def drop(self, key: str) -> None:
        with self._lock:
            if key in self.data["images"]:
                del self.data["images"][key]
                self._save_locked()

    def set_top(self, **fields: Any) -> None:
        with self._lock:
            self.data.update(fields)
            self._save_locked()

    def summary(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for m in self.data["images"].values():
            s = m.get("status", STATE_UNKNOWN)
            counts[s] = counts.get(s, 0) + 1
        return counts


# --------------------------------------------------------------------------- #
# Docker Registry v2 — anonymous bearer-auth flow                             #
# --------------------------------------------------------------------------- #

class RegistryError(RuntimeError):
    pass


def _http_get(url: str, headers: dict[str, str] | None = None, timeout: float = 20.0,
              allow_401: bool = False) -> tuple[int, dict[str, str], bytes]:
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, {k.lower(): v for k, v in resp.headers.items()}, resp.read()
    except urllib.error.HTTPError as exc:
        if exc.code == 401 and allow_401:
            return exc.code, {k.lower(): v for k, v in exc.headers.items()}, exc.read() or b""
        raise RegistryError(f"HTTP {exc.code} on {url}: {exc.reason}") from exc
    except urllib.error.URLError as exc:
        raise RegistryError(f"URL error on {url}: {exc}") from exc


def _parse_www_auth(value: str) -> dict[str, str]:
    # "Bearer realm=\"...\",service=\"...\",scope=\"...\""
    return dict(_WWW_AUTH_PARAM_RE.findall(value or ""))


def _get_bearer_token(registry: str, scope: str) -> str:
    """Run the Docker Registry v2 token challenge: GET /v2/ to learn the realm,
    then fetch a bearer token scoped to the repository."""
    probe_url = f"https://{registry}/v2/"
    status, headers, _ = _http_get(probe_url, allow_401=True)
    if status == 200:
        return ""
    if status != 401:
        raise RegistryError(f"unexpected status {status} from {probe_url}")
    challenge = headers.get("www-authenticate", "")
    params = _parse_www_auth(challenge)
    realm = params.get("realm")
    service = params.get("service", registry)
    if not realm:
        raise RegistryError(f"no realm in WWW-Authenticate from {registry}: {challenge!r}")
    q = {"service": service, "scope": scope}
    token_url = realm + "?" + urllib.parse.urlencode(q)
    status, _, body = _http_get(token_url)
    if status != 200:
        raise RegistryError(f"token request failed: {status}")
    try:
        data = json.loads(body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise RegistryError(f"token response not JSON: {exc}") from exc
    token = data.get("token") or data.get("access_token")
    if not token:
        raise RegistryError(f"no token in response: {data}")
    return token


def fetch_tags(spec: ImageSpec) -> list[str]:
    """Return the full list of tags published for ``spec.repo`` from its
    registry. Handles pagination."""
    scope = f"repository:{spec.name}:pull"
    token = _get_bearer_token(spec.registry, scope)
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    url: str | None = f"https://{spec.registry}/v2/{spec.name}/tags/list"
    tags: list[str] = []
    while url:
        status, resp_headers, body = _http_get(url, headers=headers)
        if status != 200:
            raise RegistryError(f"tags list {url} returned {status}")
        try:
            data = json.loads(body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise RegistryError(f"tags response not JSON: {exc}") from exc
        tags.extend(data.get("tags") or [])
        # Registry v2 paginates via `Link` header: `<...>; rel="next"`
        link = resp_headers.get("link") or ""
        nxt = None
        for part in link.split(","):
            m = re.match(r'\s*<([^>]+)>\s*;\s*rel="next"', part)
            if m:
                nxt = m.group(1)
                break
        if nxt:
            url = urllib.parse.urljoin(url, nxt)
        else:
            url = None
    return sorted(set(tags))


# --------------------------------------------------------------------------- #
# Tag selection                                                               #
# --------------------------------------------------------------------------- #

_NUMS_RE = re.compile(r"\d+")


def _version_key(tag: str) -> tuple[int, ...]:
    """Sort key that orders ``26.03-py3`` > ``26.01-py3`` > ``25.11-py3`` etc.
    Falls back to lexicographic comparison for non-numeric tags."""
    nums = tuple(int(x) for x in _NUMS_RE.findall(tag))
    # Pad with zeros so (26, 3) > (26, 1)
    return nums or (0,)


def select_keep(tags: list[str], spec: ImageSpec) -> list[str]:
    """Return the de-duplicated list of tags we want to have locally, ordered
    newest → oldest. always_keep is preserved regardless of regex match."""
    pattern = re.compile(spec.tag_pattern)
    matches = [t for t in tags if pattern.search(t)]
    ranked = sorted(matches, key=_version_key, reverse=True)
    keep = list(dict.fromkeys(ranked[: max(0, spec.keep_latest)] + list(spec.always_keep)))
    return keep


# --------------------------------------------------------------------------- #
# Subprocess runner                                                           #
# --------------------------------------------------------------------------- #

class Runner:
    """Streams subprocess output to the service log, supports clean SIGTERM
    propagation for in-flight docker pull / docker save / ssh pipelines."""

    def __init__(self) -> None:
        self._current: subprocess.Popen[str] | None = None

    def run(self, cmd: list[str], *, env_extra: dict[str, str] | None = None,
            expect_success: bool = True, shell_pipeline: str | None = None) -> int:
        """Run ``cmd`` (list) OR a shell pipeline string (via ``bash -lc``).
        The shell form is used for ``docker save | ssh … docker load`` since
        Python-native piping with a remote command is painful."""
        env = os.environ.copy()
        if env_extra:
            env.update(env_extra)

        if shell_pipeline is not None:
            log.info("exec (sh): %s", shell_pipeline)
            proc = subprocess.Popen(
                ["/bin/bash", "-lc", shell_pipeline],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                env=env,
            )
        else:
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
            log.debug("non-success rc=%s (expected): %s", rc, cmd or shell_pipeline)
        return rc

    def terminate(self) -> None:
        proc = self._current
        if proc and proc.poll() is None:
            log.info("terminating child pid=%s", proc.pid)
            try:
                proc.terminate()
            except ProcessLookupError:
                return
            try:
                proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                log.warning("child pid=%s didn't exit on SIGTERM, SIGKILL'ing", proc.pid)
                proc.kill()


# --------------------------------------------------------------------------- #
# Docker helpers                                                              #
# --------------------------------------------------------------------------- #

def local_tags(repo: str) -> set[str]:
    """Return the set of tags currently pulled for ``repo`` on the local daemon."""
    try:
        out = subprocess.run(
            ["docker", "images", repo, "--format", "{{.Tag}}"],
            capture_output=True, text=True, check=True,
        ).stdout
    except subprocess.CalledProcessError as exc:
        log.warning("docker images %s failed: %s", repo, exc)
        return set()
    return {line.strip() for line in out.splitlines() if line.strip() and line.strip() != "<none>"}


def image_used_by_container(repo_tag: str) -> bool:
    """True if any (running or stopped) container references this image."""
    out = subprocess.run(
        ["docker", "ps", "-a", "--filter", f"ancestor={repo_tag}", "--format", "{{.ID}}"],
        capture_output=True, text=True, check=False,
    ).stdout
    return bool(out.strip())


def pull_image(runner: Runner, repo_tag: str) -> int:
    return runner.run(["docker", "pull", repo_tag])


def _local_image_id(repo_tag: str) -> str | None:
    try:
        out = subprocess.run(
            ["docker", "image", "inspect", "--format", "{{.Id}}", repo_tag],
            capture_output=True, text=True, check=True,
        ).stdout
        return out.strip() or None
    except subprocess.CalledProcessError:
        return None


def _peer_image_id(target: SyncTarget, repo_tag: str) -> str | None:
    ssh_opts = [
        "-o", "StrictHostKeyChecking=no", "-o", "BatchMode=yes",
        "-o", "UserKnownHostsFile=/dev/null", "-o", "LogLevel=ERROR",
    ]
    try:
        out = subprocess.run(
            ["ssh", *ssh_opts, f"{target.user}@{target.host}",
             "docker", "image", "inspect", "--format", "{{.Id}}", repo_tag],
            capture_output=True, text=True, check=True, timeout=30,
        ).stdout
        return out.strip() or None
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None


def sync_image_to_peer(runner: Runner, repo_tag: str, target: SyncTarget) -> int:
    """docker save | ssh <peer> docker load — uses bash shell pipeline.
    Fast-paths away the transfer entirely when the peer already has an image
    with the same content-addressed ID; `docker save` is ~25 GB of wasted I/O
    otherwise."""
    local_id = _local_image_id(repo_tag)
    peer_id = _peer_image_id(target, repo_tag)
    if local_id and peer_id and local_id == peer_id:
        log.info("peer %s already has %s (%s); skipping save/load",
                 target.label, repo_tag, local_id[:19])
        return 0
    ssh_opts = (
        "-o StrictHostKeyChecking=no -o BatchMode=yes "
        "-o UserKnownHostsFile=/dev/null -o LogLevel=ERROR"
    )
    pipeline = (
        f"set -o pipefail; "
        f"docker save {shlex_quote(repo_tag)} | "
        f"ssh {ssh_opts} {shlex_quote(target.user)}@{shlex_quote(target.host)} "
        f"docker load"
    )
    return runner.run([], shell_pipeline=pipeline)


def prune_image(runner: Runner, repo_tag: str) -> int:
    """docker image rm; non-fatal if the image is in use (we return the rc)."""
    return runner.run(["docker", "image", "rm", repo_tag], expect_success=False)


def shlex_quote(s: str) -> str:
    # Minimal quoting for bash -lc string interpolation.
    import shlex
    return shlex.quote(s)


# --------------------------------------------------------------------------- #
# Reconciliation                                                              #
# --------------------------------------------------------------------------- #

@dataclass
class BackoffTracker:
    failures: dict[str, int] = field(default_factory=dict)
    last_attempt: dict[str, float] = field(default_factory=dict)

    def ready(self, key: str, now: float) -> bool:
        n = self.failures.get(key, 0)
        if n == 0:
            return True
        wait = RETRY_BACKOFF[min(n - 1, len(RETRY_BACKOFF) - 1)]
        return (now - self.last_attempt.get(key, 0)) >= wait

    def succeed(self, key: str) -> None:
        self.failures.pop(key, None)
        self.last_attempt.pop(key, None)

    def fail(self, key: str, now: float) -> None:
        self.failures[key] = self.failures.get(key, 0) + 1
        self.last_attempt[key] = now


def reconcile_once(
    cfg: Config,
    state: StateStore,
    runner: Runner,
    backoff: BackoffTracker,
    stop_event: "StopEvent",
) -> None:
    """One full reconciliation pass across every configured image."""
    for spec in cfg.images:
        if stop_event.is_set():
            return

        # 1. Discover what's published upstream.
        try:
            all_tags = fetch_tags(spec)
        except Exception as exc:
            log.error("tag discovery failed for %s: %s", spec.repo, exc)
            state.set(
                f"{spec.repo}:@discovery",
                status=STATE_ERROR,
                reason=f"tag discovery: {exc}",
            )
            continue

        keep = select_keep(all_tags, spec)
        log.info("%s — upstream tags=%d, keeping %s", spec.repo, len(all_tags), keep)
        state.set(
            f"{spec.repo}:@discovery",
            status=STATE_READY,
            upstream_tag_count=len(all_tags),
            keep_tags=keep,
            last_discovered_at=time.time(),
            reason="tags fetched",
        )

        keep_set = set(keep)
        have = local_tags(spec.repo)

        # 2. Pull tags we don't yet have.
        for tag in keep:
            if stop_event.is_set():
                return
            key = f"{spec.repo}:{tag}"
            now = time.time()
            if tag in have:
                if state.get(key).get("status") != STATE_READY:
                    state.set(key, status=STATE_READY, reason="already present locally")
                # Still need to (re)sync to peers — see below.
            else:
                if not backoff.ready(key, now):
                    continue
                state.set(key, status=STATE_PULLING, reason="docker pull")
                rc = pull_image(runner, key)
                if stop_event.is_set():
                    return
                if rc != 0:
                    backoff.fail(key, now)
                    state.set(
                        key,
                        status=STATE_ERROR,
                        reason=f"docker pull rc={rc}",
                        failures=backoff.failures.get(key, 0),
                    )
                    continue
                have.add(tag)

            # 3. Replicate to peers (idempotent — docker load recognises layers it already has).
            sync_ok = True
            for peer in cfg.sync_targets:
                if stop_event.is_set():
                    return
                state.set(key, status=STATE_SYNCING, reason=f"save → {peer.label}")
                rc = sync_image_to_peer(runner, key, peer)
                if rc != 0:
                    sync_ok = False
                    backoff.fail(key, now)
                    state.set(
                        key,
                        status=STATE_ERROR,
                        reason=f"docker save|load → {peer.label} rc={rc}",
                        failures=backoff.failures.get(key, 0),
                    )
                    break
            if sync_ok:
                backoff.succeed(key)
                state.set(
                    key,
                    status=STATE_READY,
                    reason="present on all configured nodes" if cfg.sync_targets else "present locally",
                    failures=0,
                )

        # 4. Optional prune of stale tags.
        if spec.prune_stale:
            stale = have - keep_set
            for tag in sorted(stale):
                if stop_event.is_set():
                    return
                key = f"{spec.repo}:{tag}"
                if image_used_by_container(key):
                    state.set(key, status=STATE_READY, reason="in use by a container; not pruned")
                    continue
                rc = prune_image(runner, key)
                if rc == 0:
                    state.set(key, status=STATE_PRUNED, reason="pruned locally")
                else:
                    state.set(
                        key,
                        status=STATE_ERROR,
                        reason=f"docker image rm rc={rc}",
                    )


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
    parser = argparse.ArgumentParser(
        description="NGC / OCI image prefetch + peer-sync daemon."
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE)
    parser.add_argument("--once", action="store_true",
                        help="Run a single reconciliation pass and exit.")
    parser.add_argument("-v", "--verbose", action="count", default=0)
    args = parser.parse_args(argv)

    level = logging.DEBUG if args.verbose >= 2 else logging.INFO
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(message)s")

    for tool in ("docker", "ssh"):
        if shutil.which(tool) is None:
            print(f"ngc-image-sync: required tool '{tool}' not in PATH", file=sys.stderr)
            return 3

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
    log.info("ngc-image-sync started, config=%s state=%s", args.config, args.state)

    last_mtime = 0.0
    cfg: Config | None = None
    first_pass = True
    while not stop_event.is_set():
        try:
            mtime = args.config.stat().st_mtime
            if mtime != last_mtime:
                log.info("(re)loading config %s", args.config)
                cfg = Config.load(args.config)
                last_mtime = mtime
                log.info(
                    "config: %d image(s), %d sync target(s), poll every %ds",
                    len(cfg.images), len(cfg.sync_targets), cfg.poll_interval_sec,
                )
        except Exception as exc:
            log.error("failed to load config: %s", exc)
            if cfg is None:
                time.sleep(15)
                continue

        assert cfg is not None

        if first_pass and cfg.startup_delay_sec > 0:
            log.info("startup delay: sleeping %ds before first reconcile", cfg.startup_delay_sec)
            deadline = time.monotonic() + cfg.startup_delay_sec
            while not stop_event.is_set() and time.monotonic() < deadline:
                time.sleep(min(1.0, deadline - time.monotonic()))
            if stop_event.is_set():
                break
        first_pass = False

        started = time.time()
        state.set_top(last_poll_at=started)
        try:
            reconcile_once(cfg, state, runner, backoff, stop_event)
        except Exception:
            log.exception("reconciliation failed")
        finished = time.time()
        next_poll = finished + cfg.poll_interval_sec
        state.set_top(next_poll_at=next_poll)
        log.info("summary: %s — next poll in %ds", state.summary(), cfg.poll_interval_sec)

        if args.once:
            return 0

        deadline = time.monotonic() + cfg.poll_interval_sec
        while not stop_event.is_set() and time.monotonic() < deadline:
            time.sleep(min(10.0, deadline - time.monotonic()))

    log.info("ngc-image-sync exiting")
    return 0


if __name__ == "__main__":
    sys.exit(main())
