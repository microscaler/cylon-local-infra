---
title: vllm-stacked.service (systemd) — decommissioned
kind: entity
status: superseded
superseded_by: entities/ngc-vllm-image.md
superseded_on: 2026-04-18
tags: [systemd, vllm, ray, leader, decommissioned]
updated: 2026-04-18
related:
  - entities/ray-head-service.md
  - concepts/transformers-huggingface-hub-mismatch.md
  - concepts/bare-metal-venv-stack.md
  - runs/2026-04-18-rip-out-bare-metal.md
---

> **Decommissioned 2026-04-18.** Unit and launcher removed from both Sparks as part of
> the bare-metal rip-out. The containerised replacement is
> `vllm-ngc-ray-head` running `vllm serve` inside
> `nvcr.io/nvidia/vllm:26.01-py3` — see
> [concepts/ngc-stacked-container-stack.md](../concepts/ngc-stacked-container-stack.md).
> Historical details preserved below.


# vllm-stacked.service

systemd unit on the leader Spark (`nvidia1`) that runs the bare-metal vLLM OpenAI API
with tensor-parallel=2 across the Ray cluster.

| Field | Value |
|---|---|
| Path | `/etc/systemd/system/vllm-stacked.service` |
| Launcher | `/opt/vllm/run-api-server-stacked.sh` |
| `User=` | `nvidia` |
| `WorkingDirectory=` | `/home/nvidia` |
| `After=` / `Requires=` | `ray-head.service` |
| `TimeoutStartSec` / `TimeoutStopSec` | 3600 / 3600 |
| `Restart=` / `RestartSec=` | on-failure / 20 |

## Launcher behavior

`run-api-server-stacked.sh` sets up `LD_LIBRARY_PATH` for CUDA 12.8 + torch libs,
exports socket-NCCL env (`NCCL_SOCKET_IFNAME=enp1s0f0np0`, `NCCL_IB_DISABLE=1`, …),
**sleeps 600s** to let Ray settle, then:

```
/opt/vllm/venv/bin/python -m vllm.entrypoints.openai.api_server \
  --host 0.0.0.0 --model google/gemma-4-31B-it --port 8080 \
  --load-format fastsafetensors --tensor-parallel-size 2 \
  --distributed-executor-backend ray --max-model-len 32768
```

## Current state (2026-04-18)

- **Active**: `active (running)` — but only because it's still inside the `sleep 600`.
- **Restart counter**: 967 (as of 2026-04-18 23:52 EEST).
- **Failure mode**: `ImportError: cannot import name 'is_offline_mode' from
  'huggingface_hub'` at `vllm.entrypoints.openai.api_server` import time. See
  [concepts/transformers-huggingface-hub-mismatch.md](../concepts/transformers-huggingface-hub-mismatch.md).

## Interaction with the NGC container path

When `spark_provision_vllm_stacked_container: true`, the container role **stops and
disables** this unit via `tasks/stop_bare_metal_systemd.yml` to avoid port + Ray
contention. Do not run both paths simultaneously.

## How to tail (from the Mac)

```
ssh casibbald@nvidia1 'sudo journalctl -u vllm-stacked -f'
```
