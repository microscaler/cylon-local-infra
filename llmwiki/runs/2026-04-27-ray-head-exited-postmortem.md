---
title: 2026-04-27 — Ray head Exited after manual stop + reboot (postmortem); justfile + role hardening; Ray dashboard on LAN
kind: run
status: shipped
date: 2026-04-27
hosts: [nvidia1, nvidia2]
tags: [ray, vllm, docker, justfile, postmortem, dashboard, hf-offline, cgraph-timeout, lan]
related:
  - ../concepts/restart-unless-stopped-after-manual-stop.md
  - ../concepts/ngc-stacked-container-stack.md
  - ../concepts/ipv6-asgi-hang.md
  - ../concepts/ray-cgraph-timeout.md
  - ../runs/2026-04-19-fp8-stack-cutover.md
  - ../entities/nvidia1.md
---

# Postmortem: Ray head Exited after manual stop + reboot

Operator returned to the cluster after a Spark host reboot and found
`vllm-ngc-ray-head` in `Exited (1)` despite `--restart unless-stopped`.
Diagnosis traced it to a **role bug** combined with **expected Docker semantics
around manual stops**, not a Ray crash. Fix landed alongside a set of operator
recipes the lifecycle was missing, plus a separate follow-on improvement
exposing the Ray dashboard on the LAN.

## Timeline (EEST, all on 2026-04-27)

| Time | Event |
|---|---|
| 14:23:13 | nvidia1 boot (boot id `f2f411ed…`). |
| 14:28:31 | Ansible-driven `docker stop vllm-ngc-ray-head` issued (operator-side `just spark-vllm-stop` calls `ansible nvidia1 -m shell -a 'docker stop vllm-ngc-ray-head'`). dockerd marks `hasBeenManuallyStopped=true`. |
| 14:28:36 | Container `6e37814a…` exits 1 (Ray sees SIGTERM, `gcs_server.cc:639 ... death reason = EXPECTED_TERMINATION`; raylet `received SIGTERM. Existing local drain request = None`). |
| 14:30:37 | Operator-side ansible runs `docker run -d --name vllm-ngc-ray-head …` — collides with the still-present Exited container (name already in use). The Exited container is **not** removed; new `docker run` is a no-op for the head. |
| 14:41:47 | Same `docker run` retry, same collision. |
| 14:42:21 | nvidia1 reboots (operator-driven). |
| 14:42:48 | nvidia1 boot (boot id `5acc5b4f…`). `--restart unless-stopped` deliberately **does not** re-launch a container with `hasBeenManuallyStopped=true` — head stays Exited across the reboot. |
| 14:53 (~) | Operator notices `:8080` / `:8000` not bound, starts diagnosis. |
| 14:55 (~) | `docker start vllm-ngc-ray-head` brings the head back; `ray status` reports 2 active nodes / 2.0 GPU / 219 GiB memory. |
| 15:13–15:18 | `vllm serve` boots inside the head container on the cached weights (Qwen3.6-35B-A3B-FP8 + parsers + chat template). Engine init completes at 15:17:53 (+165 s from launch). |
| 15:18+ | API server **does not** advance to `Application startup complete`; `:8000` never binds. Diagnosed but **not fixed in this run** — see "Follow-on issue: API server hang". |
| ~15:30 | Ray dashboard `--dashboard-host=0.0.0.0` work shipped (separate change in same session). |

## Symptom

`docker ps -a --filter name=vllm-ngc`:

```
6e37814ad34c   nvcr.io/nvidia/vllm:26.03-py3   "/bin/bash -c 'ray s…"   5 days ago   Exited (1) 25 minutes ago             vllm-ngc-ray-head
```

Inside the captured Ray session logs (`docker cp vllm-ngc-ray-head:/tmp/ray /tmp/ray-head-dump`):

- `gcs_server.out`: `gcs_node_manager.cc:639: ... death reason = EXPECTED_TERMINATION, death message = received SIGTERM`
- `raylet.out`: `main.cc:1109: received SIGTERM. Existing local drain request = None`
- `ray_process_exit.log`, `gcs_server.err`, `raylet.err`, `monitor.err` — all empty.

## Root cause

Two compounding issues:

1. **Docker's `--restart unless-stopped` semantics**: a container that was
   manually stopped (`hasBeenManuallyStopped=true` in the daemon's state)
   is **not** relaunched on docker daemon start / system reboot. This is
   the documented behaviour and what differentiates `unless-stopped` from
   `always`. The 14:28:31 `docker stop` (issued by `just spark-vllm-stop`)
   set that flag; the 14:42 reboot did not clear it. Concept page:
   [restart-unless-stopped-after-manual-stop](../concepts/restart-unless-stopped-after-manual-stop.md).

2. **Role bug in `roles/vllm_stacked_container/tasks/main.yml`**: the head's
   start path was `docker run -d --name vllm-ngc-ray-head …` only — no
   fallback for the case where a previously-stopped container with that name
   already exists. The worker had this fallback ("Start stopped Ray worker
   container (reuse existing name)") since 2026-04-19; the head did not.
   So the Ansible re-runs at 14:30 and 14:41 each tried `docker run` with a
   name conflict, the existing Exited container was untouched, and the head
   stayed stopped.

Both errors are **state errors, not crashes**. Ray itself was healthy in
both directions: it shut down cleanly under SIGTERM, and `docker start`
brought it back without any reseat or reset.

## Diagnosis path

The Ray session logs are inside the (stopped) container at `/tmp/ray`.
`docker cp` works on stopped containers, so we lifted the entire session
directory to the host and read it offline:

```bash
mkdir -p /tmp/ray-head-dump && rm -rf /tmp/ray-head-dump/*
docker cp vllm-ngc-ray-head:/tmp/ray /tmp/ray-head-dump/
SDIR=/tmp/ray-head-dump/ray/$(ls -1t /tmp/ray-head-dump/ray/session_* | head -1)
tail -100 "$SDIR/logs/raylet.out"
tail -80  "$SDIR/logs/gcs_server.out"
```

Cross-checked against the previous boot's journal to confirm the SIGTERM
came from the Ansible operator path, not from inside the container or from
docker's housekeeping:

```bash
sudo journalctl -b -1 --no-pager 2>&1 \
  | grep -iE "vllm-ngc-ray-head|docker.*stop|sigterm" \
  | tail -20
# Apr 27 14:28:31 gx10-e1ce python3.12[5558]: ansible-ansible.legacy.command Invoked with
#   _raw_params=docker stop vllm-ngc-ray-head 2>/dev/null || true
# Apr 27 14:28:36 gx10-e1ce dockerd[2512]: ... hasBeenManuallyStopped=true ...
```

## Fix

`roles/vllm_stacked_container/tasks/main.yml` — added a "Start stopped Ray
head container (reuse existing name)" task before the `docker run` block,
mirroring the existing worker pattern. Updated the `docker run` `when:` to
short-circuit when `docker start` succeeded:

```yaml
- name: Start stopped Ray head container (reuse existing name)
  ansible.builtin.command:
    cmd: docker start "{{ vllm_stacked_container_ray_head_name }}"
  register: vllm_stacked_container_head_docker_start
  when:
    - inventory_hostname == (groups['sparks'] | sort | first)
    - not (vllm_stacked_container_recreate | bool)
    - vllm_stacked_container_head_running.rc == 0
    - (vllm_stacked_container_head_running.stdout | default("") | trim) == "false"
  failed_when: false
  changed_when: vllm_stacked_container_head_docker_start.rc == 0

- name: Start Ray head container (NGC image, host network)
  # ...
  when:
    - inventory_hostname == (groups['sparks'] | sort | first)
    - vllm_stacked_container_recreate | bool
      or vllm_stacked_container_head_running.rc != 0
      or (
        (vllm_stacked_container_head_running.stdout | default("") | trim) == "false"
        and (
          vllm_stacked_container_head_docker_start is not defined
          or vllm_stacked_container_head_docker_start.rc != 0
        )
      )
```

Verified live: `docker start vllm-ngc-ray-head` brought the cluster back
to 2 nodes / 2 GPU without any other changes; the inserted Ansible task is
a no-op when the head is already running.

## Operator surface added (justfile)

The session also exposed a gap: `just spark-vllm-stop` existed but
`just spark-vllm-{head,worker}-{start,restart}` did not. Added 10 recipes
to `justfile`:

| Recipe | Purpose |
|---|---|
| `spark-vllm-ps` | `docker ps -a` for head + worker on both Sparks. |
| `spark-vllm-head-start` | `docker start vllm-ngc-ray-head` (idempotent). |
| `spark-vllm-head-restart` | `docker restart vllm-ngc-ray-head`. |
| `spark-vllm-worker-restart` | `docker restart vllm-ngc-ray-worker-nvidia2`. |
| `spark-vllm-start` | head then worker (recipe deps). |
| `spark-vllm-restart` | head + worker restart. |
| `spark-vllm-api-kill` | `pkill -f "[v]llm serve"` inside the head — Ray stays up, weights + torch.compile cache stay warm. |
| `spark-vllm-api-restart` | kill + re-run `--tags vllm_ngc_stack` — second start is much faster (cached). |
| `spark-vllm-dashboard` | print + open the Ray dashboard URL on the LAN. |

`spark-vllm-restart` does **not** re-launch the detached `vllm serve` —
follow with `just spark-vllm-provision` (or `spark-vllm-api-restart`) to
bring the API back. Comment in the recipe makes that explicit.

## Follow-on improvement: Ray dashboard on the LAN

Earlier in the session a Ray log line surfaced

```
INFO worker.py:2004 -- Connected to Ray cluster.
View the dashboard at http://127.0.0.1:8265
```

The dashboard was bound to localhost only inside the head container — not
reachable from operator hosts on the home LAN. Shipped:

- `roles/vllm_stacked_container/defaults/main.yml`:
  `vllm_stacked_container_dashboard_host: "0.0.0.0"`,
  `vllm_stacked_container_dashboard_port: 8265`.
- `roles/vllm_stacked_container/tasks/main.yml`: head's `ray start --head`
  now passes `--dashboard-host=0.0.0.0 --dashboard-port=8265`.
- `inventory/group_vars/sparks.yml`: 8265 added to
  `firewall_trusted_lan_tcp_ports` (LAN-only — same posture as `:8000`,
  not in the global `firewall_allow_tcp_ports`).
- `justfile`: `spark-vllm-dashboard` opens
  `http://${SPARK_LEADER_LAN_IP:-192.168.1.104}:8265/`.

`ray start` flags are baked into the running container's command, so
applying this needs a head **recreate** (`-e
vllm_stacked_container_recreate=true`). Plain `docker restart` won't pick
up the new flags.

## Other inventory hygiene from this session

- **Removed `HF_HUB_OFFLINE: "1"`** from `vllm_distributed_extra_env` in
  `inventory/group_vars/sparks.yml`. It had reappeared from an earlier
  edit; both `curl -4` and `curl -6` to `huggingface.co` from nvidia1
  return `HTTP/2 200` after this session's reboot, so the offline gate
  is no longer needed and would actively interfere with first-time hub
  cache symlink resolution. See
  [concepts/ipv6-asgi-hang.md](../concepts/ipv6-asgi-hang.md) for the
  history.
- **Kept `RAY_CGRAPH_get_timeout: "900"`** in
  `vllm_distributed_extra_env`. Documented in
  [concepts/ray-cgraph-timeout.md](../concepts/ray-cgraph-timeout.md);
  the default 300 s killed our prior `chat/completions` runs. Setting
  was added earlier in the session and persists.

## Follow-on issue: API server hangs post-engine-init (NOT fixed here)

After the head was restarted and `just spark-vllm-provision` re-launched
`vllm serve`, the API never bound `:8000`. Process state when probed
~6 min after launch:

- `vllm serve` PID 1671 alive (S sleeping, 64 threads, RSS ≈1.4 GiB,
  wchan `poll_schedule_timeout`).
- `EngineCore` PID 1806 alive (wchan `wait_woken`).
- Engine init line `core.py:282 init engine ... took 77.46 seconds` at
  +165 s from launch.
- After that, **no** APIServer-tagged log lines. No traceback, no
  exception, no SIGTERM.
- Host `ss -lntp`: no `:8000` listener (Ray's `:6379` and `:8265` were
  bound).

Compared the running argv against the known-good
[2026-04-19 FP8 cutover](./2026-04-19-fp8-stack-cutover.md) and
[256k throughput run](./2026-04-19-qwen3-throughput-and-256k.md):
config matches, plus `--chat-template /vllm-patches/unsloth.jinja`. The
2026-04-19 cutover ran cold-start to `:8000 LISTEN` in ~4 minutes; we
were past 6 with no progress.

Most likely cause per wiki precedent: stale ibverbs MR/QP state from the
5-day-old container being repeatedly stopped+started (instead of fully
recreated) plus `buff/cache` not dropped — `2026-04-19-fp8-stack-cutover`
explicitly notes the wedge mode and the workaround:
*"`drop_caches` was run on both Sparks before each cutover attempt —
recovers ~4–5 GiB of buff/cache that vLLM's `free` probe (not
`available`) wouldn't otherwise see"*, and *"not-fully-released ibverbs
state from prior containers, the probe-time free ceiling dropped to
~103 GiB"*. The known recovery procedure is **recreate** the head +
worker (fresh `docker run`), not `docker start` of an old container.

Recommended next-session sequence (recorded so we don't relearn):

```bash
just spark-vllm-api-kill
ansible sparks -b -m shell -a 'sync; echo 3 > /proc/sys/vm/drop_caches'
just spark-vllm-provision-recreate
```

The role bug fix above means the recreate path will produce a
known-clean head, and the dashboard exposure means the next-launched
head will already be reachable from the LAN.

## Files changed

- `roles/vllm_stacked_container/tasks/main.yml` — head `docker start`
  fallback; updated `when:` on the `docker run` head start; head's
  `ray start --head` now passes `--dashboard-host` / `--dashboard-port`.
- `roles/vllm_stacked_container/defaults/main.yml` — added
  `vllm_stacked_container_dashboard_host` (default `0.0.0.0`) and
  `vllm_stacked_container_dashboard_port` (default `8265`).
- `roles/vllm_stacked_container/README.md` — documented the new
  variables; flagged the recreate-needed caveat for `ray start` flag
  changes.
- `inventory/group_vars/sparks.yml` — removed `HF_HUB_OFFLINE: "1"`;
  added 8265 to `firewall_trusted_lan_tcp_ports`.
- `justfile` — 10 new `spark-vllm-*` recipes (see table above).

`ansible-playbook playbooks/provision_sparks.yml --syntax-check` and
`just --summary` both pass; `roles/vllm_stacked_container` lints clean.

## Verification (in-session)

```
just spark-vllm-ps
# nvidia1 | CHANGED | rc=0 >>
# 6e37814ad34c   nvcr.io/nvidia/vllm:26.03-py3   "/bin/bash -c 'ray s…"   5 days ago   Up 17 seconds   vllm-ngc-ray-head
# nvidia2 | CHANGED | rc=0 >>
# 1e320c87b43d   nvcr.io/nvidia/vllm:26.03-py3   "/bin/bash -c 'ray s…"   5 days ago   Up 16 seconds   vllm-ngc-ray-worker-nvidia2

ssh nvidia1 'docker exec vllm-ngc-ray-head ray status'
# Active: 2 nodes
# Resources: 0.0/40.0 CPU, 2.0/2.0 GPU (reserved in placement groups), 0B/219.09GiB memory
```

## Open follow-ups

- [ ] Run the recreate + drop_caches sequence to clear the wedged
      `vllm serve` and confirm `:8000` binds. File a one-paragraph
      follow-up run page noting wall time vs the 2026-04-19 baseline.
- [ ] After the recreate, verify the LAN-bound dashboard:
      `curl -sI http://192.168.1.104:8265/` from picolino should return
      `200 OK`; `ss -lntp | grep ':8265 '` on nvidia1 should show
      `0.0.0.0:8265`.
- [ ] If the API hang reproduces on a clean recreate, file a new
      concept page (likely a sibling of
      [ipv6-asgi-hang](../concepts/ipv6-asgi-hang.md) but with a
      different root cause) and bisect the parser/chat-template flags
      against the 2026-04-19 known-good config.
- [ ] Consider folding `drop_caches` into the role (gated by a new
      `vllm_stacked_container_drop_caches_before_recreate` default,
      off by default — operator opts in for cutover-grade runs).
