---
title: nvidia1 (leader Spark)
kind: entity
status: active
tags: [spark, host, leader]
updated: 2026-04-18
related: [entities/nvidia2.md, entities/ray-head-service.md, entities/vllm-stacked-service.md]
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

- **LAN IPv6 black hole** — broken IPv6 default on `enP7s7` causes `httpx` to stall
  during vLLM ASGI init (generation-config fetch). Mitigation:
  `sysctl net.ipv6.conf.enP7s7.disable_ipv6=1`, persisted via
  `roles/spark_provision/tasks/lan_ipv6_sysctl.yml` and `host_vars/nvidia1.yml`.
  See [concepts/ipv6-asgi-hang.md](../concepts/ipv6-asgi-hang.md).
- **Ray head has been up 6+ days** as of 2026-04-18 (PID 2205, session dir
  `/tmp/ray/session_2026-04-12_00-49-55_016368_2205`). Its GCS is `169.254.102.149:6379`.

## Cached images

- `nvcr.io/nvidia/vllm:26.01-py3` (≈14 GB on-disk).
- `nginx:latest` (leftover / unrelated).

## Cached models (`/home/nvidia/.cache/huggingface/hub`)

- `models--google--gemma-4-31B-it`
- `models--TinyLlama--TinyLlama-1.1B-Chat-v1.0`

## systemd units

- `ray-head.service` — active (running).
- `vllm-stacked.service` — **failing** restart loop (see
  [entities/vllm-stacked-service.md](./vllm-stacked-service.md) and
  [runs/2026-04-18-state-of-cluster.md](../runs/2026-04-18-state-of-cluster.md)).

## Endpoints

- `http://nvidia1:8080/v1` — bare-metal vLLM OpenAI API (currently down).
- `http://nvidia1:8000/v1` — NGC container vLLM (planned; not yet up).
- Ray dashboard: `127.0.0.1:8265` (local to the host).
