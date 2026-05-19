---
title: nvidia1 (leader Spark)
kind: entity
status: active
tags: [spark, host, leader]
updated: 2026-04-27
related:
  - entities/nvidia2.md
  - entities/ngc-vllm-image.md
  - concepts/ngc-stacked-container-stack.md
  - concepts/restart-unless-stopped-after-manual-stop.md
  - runs/2026-04-27-ray-head-exited-postmortem.md
---

# nvidia1

Leader DGX Spark in the stacked pair.

| Field | Value |
|---|---|
| Inventory name | `nvidia1` |
| LAN IP | `192.168.1.104` |
| Hostname on host | `gx10-e1ce` |
| GPU | NVIDIA GB10 (`GPU-18921ed7-42b8-1bcd-5c0d-c8e633c4bbbc`) |
| OS | Ubuntu 24.04 aarch64 |
| Interconnect IP | `169.254.102.149` on `enp1s0f0np0` |
| LAN interface (IPv6 disabled) | `enP7s7` (see `inventory/host_vars/nvidia1.yml`) |
| Ansible user | `casibbald` (passwordless sudo) |
| Runtime user | `nvidia` (targeted NOPASSWD for apt + systemctl + journalctl) |
| Role in cluster | Ray head, vLLM OpenAI API leader |
| Mac SSH aliases | `nvidia1` (casibbald), `nvidia1-runtime` (nvidia) — see `~/.ssh/config.d/sparks` |

## Known host-specific quirks

- **LAN IPv6 black hole (historical)** — broken IPv6 default on `enP7s7`
  used to stall `httpx` during vLLM ASGI init. Mitigation persisted via
  `roles/spark_provision/tasks/lan_ipv6_sysctl.yml` and `host_vars/nvidia1.yml`.
  See [concepts/ipv6-asgi-hang.md](../concepts/ipv6-asgi-hang.md). As of
  2026-04-27 both `curl -4` and `curl -6` to `huggingface.co` succeed, and
  `HF_HUB_OFFLINE` is no longer set in `vllm_distributed_extra_env`.
- **`--restart unless-stopped` + manual `docker stop` = stays Exited**
  across host reboots. Bit us 2026-04-27. Operator surface and role both
  hardened — see
  [concepts/restart-unless-stopped-after-manual-stop.md](../concepts/restart-unless-stopped-after-manual-stop.md)
  and
  [runs/2026-04-27-ray-head-exited-postmortem.md](../runs/2026-04-27-ray-head-exited-postmortem.md).

## Cached images

- `nvcr.io/nvidia/vllm:26.03-py3` (≈14 GB on-disk; current serving image
  per [entities/ngc-vllm-image.md](./ngc-vllm-image.md)).
- `nvcr.io/nvidia/vllm:26.02-py3` (kept by `ngc-image-sync.service`,
  fallback).

## Cached models (`/home/nvidia/.cache/huggingface/hub`)

- `models--Qwen--Qwen3.6-35B-A3B`
- `models--Qwen--Qwen3.5-35B-A3B-FP8` — current serving target as of
  2026-04-27 (see
  [runs/2026-04-19-fp8-stack-cutover.md](../runs/2026-04-19-fp8-stack-cutover.md)).
- `models--Qwen--Qwen3-Coder-30B-A3B-Instruct`
- `models--TinyLlama--TinyLlama-1.1B-Chat-v1.0` — smoke model.
- `models--google--gemma-4-31B-it` — historical, not currently served.

## Containers (NGC stacked-container stack)

- `vllm-ngc-ray-head` — Ray head + `vllm serve` exec'd in.
  `--restart unless-stopped`; lifecycle managed via `just spark-vllm-*`
  recipes. See
  [concepts/ngc-stacked-container-stack.md](../concepts/ngc-stacked-container-stack.md).

The legacy systemd units (`ray-head.service`, `vllm-stacked.service`,
`vllm.service`) are stopped + disabled by the role on every run; their
entity pages are kept as institutional memory only.

## systemd units (host-level, leader-only daemons)

- `hf-prefetch.service` — see
  [entities/hf-prefetch-service.md](./hf-prefetch-service.md).
- `ngc-image-sync.service` — see
  [entities/ngc-image-sync-service.md](./ngc-image-sync-service.md).
- `vllm-stack-autoupgrade.service` — see
  [entities/vllm-stack-autoupgrade-service.md](./vllm-stack-autoupgrade-service.md).

## Endpoints

- `http://nvidia1:8000/v1/*` — vLLM OpenAI API (LAN, allow-listed in
  `firewall_trusted_lan_tcp_ports`). Per the 2026-04-19 throughput run:
  `--max-model-len 262144 --gpu-memory-utilization 0.80
  --max-num-batched-tokens 16384 --max-num-seqs 128 --kv-cache-dtype fp8
  --attention-backend flashinfer --enable-prefix-caching --reasoning-parser
  qwen3 --tool-call-parser qwen3_coder` (see
  [runs/2026-04-19-fp8-stack-cutover.md](../runs/2026-04-19-fp8-stack-cutover.md)).
- `http://192.168.1.104:8265/` — **Ray dashboard, LAN-reachable** as of
  2026-04-27. Bound on `0.0.0.0:8265` inside the head container, gated to
  the trusted LAN by ufw. Operator: `just spark-vllm-dashboard`.
- `vllm/metrics` — Prometheus scrape target on `:8000/metrics`; consumed
  by `vllm-stack-autoupgrade.service`'s quiet-window gate.
