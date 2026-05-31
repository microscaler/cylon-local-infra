---
title: "2026-05-31 — ms02 Hostinger reverse SSH tunnel (away-from-LAN access)"
kind: run
status: active
tags: [network, ssh, tunnel, dev-hosts, ms02, hostinger]
updated: 2026-05-31
sources:
  - ../entities/ms02.md
  - ../entities/srv1719193.md
  - ../../docs/ms02-reverse-tunnel.md
  - ../../playbooks/ms02_reverse_tunnel.yml
---

# ms02 → Hostinger reverse SSH tunnel

## Context

Operator away from home LAN for ~1 week. Need reliable SSH (and Cursor
Remote-SSH) to **ms02** without inbound home-router port forwarding. Public
VPS **`srv1719193.hstgr.cloud`** (`76.13.1.95`) already had Mac + ms02
`casibbald` Ed25519 keys in `root@` authorized_keys.

## Solution

**Outbound** reverse tunnel from ms02 to the VPS — no home NAT changes.

| Layer | Component |
|-------|-----------|
| VPS | `GatewayPorts clientspecified` sshd drop-in; port **22002** → ms02 `:22` |
| ms02 | `ms02-reverse-tunnel.service` (autossh, `Restart=always`, enabled) |
| Mac | `Host ms02-away` in `~/.ssh/config.d/ms02-via-hostinger` |

Ansible: `playbooks/ms02_reverse_tunnel.yml` + roles `tunnel_hub/`,
`ms02_reverse_tunnel/`. Operator surface: `just ms02-reverse-tunnel-{up,status}`.

## Resilience

- **systemd** owns the tunnel (`Restart=always`, `StartLimitIntervalSec=0`,
  `After=network-online.target`).
- **autossh** supervises ssh (`-M 0`, `AUTOSSH_GATETIME=0`).
- **SSH keepalives**: `ServerAliveInterval=30`, `CountMax=3` (~90s dead detect).
- **ConnectTimeout=30** — fail fast when internet/VPS unreachable.

Survives reboot (`enabled`) and internet blips (systemd retries every 10s).

## Usage

```bash
# From away
ssh ms02-away
# or
ssh -p 22002 casibbald@76.13.1.95

# Verify
just ms02-reverse-tunnel-status
ssh ms02-root 'systemctl status ms02-reverse-tunnel'
```

## Also in this session

- **vLLM cold start**: `Wait for /v1/models` retries are normal (~2 min for
  Qwen3.6-35B-A3B-FP8 MoE); not a failure.
- **Triton cache**: `vllm_stacked_container_triton_cache_invalidate` default
  flipped to **`false`** (stale-cache investigation closed; saves ~30–90s warmup).
- **nvidia2 RoCE GID**: `spark_nccl_ib_gid_index` corrected **4 → 3** per live
  `show_gids` (2026-05-27).

## Follow-ups

- [ ] Optional: fail2ban or firewall allow-list on VPS `:22002` if brute-force noise.
- [ ] Re-run `just ms02-reverse-tunnel-up` after VPS sshd or ms02 key rotation.
