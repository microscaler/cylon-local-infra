---
title: Starlink Wi-Fi ↔ LAN port filter (+ SSH tunnel workaround)
kind: concept
status: active
tags: [network, starlink, ssh, tunnel, dev-hosts]
updated: 2026-04-19
sources:
  - ../entities/starlink-router.md
  - ../runs/2026-04-19-starlink-tunnel-workaround.md
  - ../../docs/dev_hosts.md
---

# Starlink Wi-Fi ↔ LAN port filter

## Current posture (operator machines)

**SSH tunnel / `ms02-dev-tunnel` is retired from cylon-local-infra.** ms02
`ufw` + inventory allow **`192.168.1.0/24` → dev ports** (including **2049**,
**7000–12000**, …); the Mac uses **direct LAN URLs** and **`just ms02-lan-check`**.
`mac-provision` **removes** `~/.ssh/config.d/ms02-dev-tunnel` if it still exists.
The sections below document the **historical** Starlink filter and the tunnel
pattern for context only.

## Symptom

Mac on Starlink Wi-Fi and a Linux host on the same Starlink router's LAN-out
port are nominally on the same `192.168.1.0/24`, but the Mac can only reach
a narrow subset of TCP ports on the wired host:

- `ssh` (22) works forever.
- `ping` works — with suspiciously high RTT (~120 ms instead of sub-ms).
- Arbitrary TCP (Tilt 10348, dev ports 8000–11000, ad-hoc listeners) shows a
  mix of **silent drop** and **immediate RST** — with no packet visible on the
  wired host's NIC for most attempts.

This is independent of any software firewall on the Linux host: flushing
iptables / disabling ufw does not change the outcome.

## Diagnosis checklist

Run in this order; the first step that succeeds tells you where the block is:

1. `ping -c 4 <wired-host>` — if RTT >10 ms you're not on a flat LAN.
2. On wired host: `tcpdump -i <lan-nic> host <mac-ip>` while Mac runs
   `curl http://<wired-host>:<port>/`.
   - **No packets seen** → upstream router is dropping before delivery.
   - **SYN in, no reply** → host stack ignored it (listener-side issue, not
     network).
   - **SYN in, RST out** → host actively refused (service not listening / NAT
     hijack).
3. On wired host: `ufw disable; iptables -F; iptables -P INPUT ACCEPT`, retry.
   - Behaviour unchanged → firewall is ruled out, router is guilty.
4. Compare behaviour on port 22 vs. other ports. If 22 works but others
   don't, well-known-port whitelisting on the router is the smoking gun.

## Historical workaround — SSH ControlMaster + `LocalForward` (retired in-repo)

When port 22 was the only reliable path, multiplexing dev ports through SSH
worked. **This repo no longer ships** the `ms02-dev-tunnel` fragment or
`dev-tunnel-*` just recipes — use **LAN** access (see `docs/dev_hosts.md`). If you
ever need the pattern again (café / broken router), recreate a **personal**
`~/.ssh/config.d/*` stanza; do not expect Ansible to manage it.

**What we used to ship (2026-04):**

- `~/.ssh/config.d/ms02-dev-tunnel` — `LocalForward`s + `DynamicForward 1080`.
- `just dev-tunnel-up` / `dev-tunnel-down` / … in the top-level `justfile`.

Once up historically: `http://localhost:10348/` was Tilt, `https://localhost:38839`
was kube-apiserver **via loopback on the Mac** (today prefer **`https://192.168.1.189:38839`**
in kubeconfig when on the home LAN).

Proven on 2026-04-19: services were reachable over the tunnel while direct TCP
to the wired host's LAN IP still dropped — motivating the **ms02 firewall**
work that made tunnels unnecessary for home dev.

## Why not just use a VPN?

Could work (Tailscale, WireGuard) but adds an identity plane + NAT traversal
for a problem that's already solved in under a page of SSH config. When the
physical L2 problem is fixed (USB 2.5GbE adapter, better router rules, or
ms02 `ufw` + LAN routing), drop any personal forwards and use direct IPs — no
extra infrastructure to decommission.

## Retire criterion

`ping <wired-host>` sub-millisecond on the new wired link (and dev TCP ports
answer from the Mac) → **no tunnel needed**; keep a minimal personal SSH stanza
only for actual remote access scenarios.

## Related

- [starlink-router (entity)](../entities/starlink-router.md)
- [workspace-sync (concept)](./workspace-sync.md) — same Mac↔ms02 path but
  running over the router's nominal LAN (rsync over SSH, which is unaffected).
- [ms02 (entity)](../entities/ms02.md)
