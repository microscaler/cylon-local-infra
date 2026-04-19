---
title: ray-worker.service (systemd, nvidia2) — decommissioned
kind: entity
status: superseded
superseded_by: entities/ngc-vllm-image.md
superseded_on: 2026-04-18
tags: [systemd, ray, follower, decommissioned]
updated: 2026-04-18
related:
  - entities/ray-head-service.md
  - concepts/bare-metal-venv-stack.md
  - runs/2026-04-18-rip-out-bare-metal.md
---

> **Decommissioned 2026-04-18.** Replaced by `vllm-ngc-ray-worker-nvidia2`, a Ray worker
> **container** started by the `vllm_stacked_container` role. Historical details
> preserved below.


# ray-worker.service

Follower-side Ray worker, bare-metal (systemd, `/opt/vllm/venv`).

| Field | Value |
|---|---|
| Host | `nvidia2` |
| Command | `ray start --address 169.254.102.149:6379 --node-ip-address 169.254.37.109 --min-worker-port 10002 --max-worker-port 19999 --disable-usage-stats --block` |
| venv | `/opt/vllm/venv` |
| Status (2026-04-18) | active (running) since 2026-04-12 00:49:52 EEST; joined leader session. |

## Resource view (from raylet static list)

`node:169.254.37.109,1.0, accelerator_type:GB10, 1 GPU, 20 CPU, ~87 GiB memory,
object_store_memory ~37 GiB` — matches `nvidia1` symmetry.

## Interaction with NGC container path

Stopped/disabled by `roles/vllm_stacked_container/tasks/stop_bare_metal_systemd.yml`
when the container stack is enabled.
