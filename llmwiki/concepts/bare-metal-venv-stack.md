---
title: Bare-metal venv stack (systemd) — REMOVED
kind: concept
status: superseded
superseded_by: concepts/ngc-stacked-container-stack.md
superseded_on: 2026-04-18
tags: [systemd, venv, pip, vllm, ray, fragile, removed]
updated: 2026-04-18
related:
  - concepts/ngc-stacked-container-stack.md
  - concepts/transformers-huggingface-hub-mismatch.md
  - concepts/ipv6-asgi-hang.md
  - runs/2026-04-18-rip-out-bare-metal.md
---

> **REMOVED 2026-04-18.** The bare-metal `roles/vllm/` role, the
> `vllm-stacked.service` / `ray-head.service` / `ray-worker.service` / `vllm.service`
> systemd units, and the `/opt/vllm/venv` were removed from the repo and (in a
> follow-up run) the Spark hosts. Kept here as institutional memory — if you're
> thinking about bringing back pip-on-host, read
> [runs/2026-04-18-rip-out-bare-metal.md](../runs/2026-04-18-rip-out-bare-metal.md)
> first.


# Bare-metal venv stack

Original TP=2 path: three systemd units (`ray-head`, `ray-worker`, `vllm-stacked`) on
top of `/opt/vllm/venv` (Python 3.12). Implemented by `roles/vllm/`.

## Topology

- **Leader (`nvidia1`)**: `ray-head.service` + `vllm-stacked.service`.
- **Follower (`nvidia2`)**: `ray-worker.service`.
- Venv at `/opt/vllm/venv`, owned by `nvidia`. Pip-installed `vllm`, `ray`,
  `transformers` (from git main when `vllm_transformers_from_git: true`),
  `fastsafetensors`, `uvloop==0.21.0`, torch against `cu130`.
- Launcher `run-api-server-stacked.sh` sleeps 600 s before exec'ing vLLM to let Ray
  settle.

## Why we keep it in the repo

- Still useful when the NGC image doesn't carry the exact `vllm` / `transformers` /
  `fastsafetensors` mix a given model needs.
- Systemd ownership gives clean journalctl + restart-on-failure semantics.

## Why we've been drifting away from it (2026-04)

- **Pip drift**: each time we bump `vllm` or transformers-from-git we risk the
  [transformers-huggingface-hub-mismatch](./transformers-huggingface-hub-mismatch.md) or
  uvloop / asyncio / event-loop regressions.
- **Long feedback loop**: a bad pip install burns 10+ minutes per iteration.
- **Two `vllm` venvs drift**: even with Ansible, the two Sparks occasionally end up with
  subtly different resolver outcomes.
- **`HF_HUB_OFFLINE`** was added to dodge the [ipv6-asgi-hang](./ipv6-asgi-hang.md) —
  but only *after* we'd already lost a day to the problem.

## Current status

Unit on `nvidia1` is in a restart loop (counter 967). See
[entities/vllm-stacked-service.md](../entities/vllm-stacked-service.md) and
[runs/2026-04-18-state-of-cluster.md](../runs/2026-04-18-state-of-cluster.md).

## Re-enabling

```yaml
spark_provision_vllm_stack: true
spark_provision_vllm_stacked_container: false
```

Then `ansible-playbook playbooks/provision_sparks.yml --tags vllm_stack`. You will
need to decide what to do about the
[transformers-huggingface-hub-mismatch](./transformers-huggingface-hub-mismatch.md)
first.
