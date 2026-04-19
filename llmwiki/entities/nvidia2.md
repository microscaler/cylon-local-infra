---
title: nvidia2 (follower Spark)
kind: entity
status: active
tags: [spark, host, follower]
updated: 2026-04-18
related: [entities/nvidia1.md, entities/ray-worker-service.md]
---

# nvidia2

Follower DGX Spark in the stacked pair.

| Field | Value |
|---|---|
| Inventory name | `nvidia2` |
| LAN IP | `192.168.1.229` |
| Hostname on host | `gx10-47b5` |
| GPU | NVIDIA GB10 (`GPU-872ea65d-ff88-5cd8-f726-fc74ca5f5c20`) |
| OS | Ubuntu 24.04 aarch64 |
| Interconnect IP | `169.254.37.109` on `enp1s0f0np0` |
| LAN interface (IPv6 disabled) | `enP7s7` (see `inventory/host_vars/nvidia2.yml`) |
| Ansible user | `casibbald` |
| Runtime user | `nvidia` |
| Role in cluster | Ray worker |
| Mac SSH aliases | `nvidia2` (casibbald), `nvidia2-runtime` (nvidia) — see `~/.ssh/config.d/sparks` |

## Host-specific quirks

- Same LAN IPv6 disable as `nvidia1` for ops consistency (manual sysctl applied
  2026-04; now persisted).
- Node id in the Ray session:
  `bed24016d9b6b1d1e173e420a1a123012fa6003f8775a7c37679c449`.

## Cached images

- `nvcr.io/nvidia/vllm:26.01-py3`.

## Cached models

- Pending audit (see [runs/2026-04-18-state-of-cluster.md](../runs/2026-04-18-state-of-cluster.md)).

## systemd units

- `ray-worker.service` — active (running since 2026-04-12), joined `169.254.102.149:6379`.
- `vllm-stacked.service` — not installed on follower (leader-only).
