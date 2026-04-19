---
title: ray-head.service (systemd, nvidia1) — decommissioned
kind: entity
status: superseded
superseded_by: entities/ngc-vllm-image.md
superseded_on: 2026-04-18
tags: [systemd, ray, leader, decommissioned]
updated: 2026-04-18
related:
  - entities/ray-worker-service.md
  - entities/vllm-stacked-service.md
  - concepts/bare-metal-venv-stack.md
  - runs/2026-04-18-rip-out-bare-metal.md
---

> **Decommissioned 2026-04-18.** Replaced by a Ray head **container** started by the
> `vllm_stacked_container` role: `docker run -d --restart unless-stopped --name
> vllm-ngc-ray-head --network host --gpus all … ray start --block --head …`. Historical
> details kept below.


# ray-head.service

Leader-side Ray head, bare-metal (systemd, `/opt/vllm/venv`).

| Field | Value |
|---|---|
| Host | `nvidia1` |
| Command | `ray start --head --port 6379 --node-ip-address 169.254.102.149 --min-worker-port 10002 --max-worker-port 19999 --dashboard-host=127.0.0.1 --disable-usage-stats --block` |
| venv | `/opt/vllm/venv` (Python 3.12) |
| GCS | `169.254.102.149:6379` |
| Dashboard | `127.0.0.1:8265` (loopback-only) |
| Status (2026-04-18) | active (running) for 6 days — session `session_2026-04-12_00-49-55_016368_2205`. |

## Relationship to vllm-stacked

`vllm-stacked.service` has `Requires=ray-head.service` — Ray must be up before vLLM.
When pivoting to the NGC container stack we disable **both** units and let Docker run
its own Ray.

## ray_reset_state note

`vllm` role has a `ray_reset_state.yml` task that clears stale
`/tmp/ray/session_*` dirs before a fresh stack start (toggled by
`vllm_ray_reset_state_before_stack`). Helpful after a long-running head has been
killed uncleanly — otherwise workers sometimes re-join the dead session.
