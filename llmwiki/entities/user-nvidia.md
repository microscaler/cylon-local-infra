---
title: user:nvidia (runtime user)
kind: entity
status: active
tags: [user, sudoers, nvidia]
updated: 2026-04-18
sources: [../../docs/PRD-nvidia-user-exo-transition.md, ../../docs/MIGRATION-root-to-nvidia.md]
---

# user:nvidia

Runtime user on both Sparks for GPU work (Ray, vLLM, NCCL). Not an Ansible login.

| Field | Value |
|---|---|
| Primary group | `nvidia` |
| Supplementary groups | `sudo, docker, video, render, systemd-journal` |
| Sudo rule | NOPASSWD for `apt` / `apt-get` / `dpkg`, `systemctl`, `journalctl` only |
| Home | `/home/nvidia` |
| Typical bare-metal artifacts | `/opt/vllm/venv`, `/opt/vllm/run-api-server-stacked.sh`, `~/.cache/huggingface`, `~/nccl`, `~/nccl-tests` |

## Why not root

Per `docs/PRD-nvidia-user-exo-transition.md`: aligning with NVIDIA docs, EXO future
work, and basic least-privilege. GPU workloads **must** run as `nvidia`, never `root`.

## Why not the Ansible user

`casibbald` has full NOPASSWD sudo, which is fine for provisioning but a poor fit for
running long-lived processes that drop keys, tokens, and cache into the home dir.

## Gotcha

Ansible `ansible_user=nvidia` would require widening sudo beyond the current
`NVIDIA_PKG` / `NVIDIA_SVC` / `NVIDIA_LOG` aliases. Don't — keep `casibbald` as the
Ansible login and drop to `nvidia` via `become_user` for runtime tasks.
