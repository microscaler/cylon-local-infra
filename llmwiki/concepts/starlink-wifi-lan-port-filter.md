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

## Workaround — SSH ControlMaster + `LocalForward`

Since port 22 is unaffected, multiplex every dev-stack port through a single
SSH channel. The Mac's `127.0.0.1` becomes a clone of the wired host's
`127.0.0.1`.

Files (for this repo):

- `~/.ssh/config.d/ms02-dev-tunnel` — Host stanza with ~22 `LocalForward`s
  covering Tilt 10348, kube-apiserver 38839, the kind NodePort host-bindings
  (3000/9090/16686/...), MinIO, Postgres, Redis, Docker registry, plus a
  `DynamicForward 1080` SOCKS5 catch-all.
- `~/.ssh/config` — `Include ~/.ssh/config.d/*`.
- `cylon-local-infra/justfile` — `dev-tunnel-{up,down,status,check,restart,
  logs,config}` recipes.

Operator commands:

```bash
just dev-tunnel-up      # ssh -N -f ms02-dev-tunnel, idempotent
just dev-tunnel-check   # HTTP probe every UI through the tunnel
just dev-tunnel-down    # ssh -O exit, deletes ControlMaster socket
```

Once up: `http://localhost:10348/` is Tilt, `https://localhost:38839` is the
kube-apiserver (matches ms02's kubeconfig verbatim — no rewrite needed),
`curl --socks5 127.0.0.1:1080 ...` is the escape hatch for un-mapped ports.

Proven on 2026-04-19: Tilt UI, MinIO console, Docker registry and `kubectl
get pods -n observability` all reachable from the Mac over the tunnel, while
direct TCP to the same ports on the wired host's LAN IP continues to drop.

## Why not just use a VPN?

Could work (Tailscale, WireGuard) but adds an identity plane + NAT traversal
for a problem that's already solved in under a page of SSH config. When the
physical L2 problem is fixed (USB 2.5GbE adapter, ETA days), we just
`dev-tunnel-down` and carry on — no extra infrastructure to decommission.

## Retire criterion

`ping <wired-host>` sub-millisecond on the new wired link → tear down the
tunnel, keep the SSH stanza parked for future café/hotel scenarios.

## Related

- [starlink-router (entity)](../entities/starlink-router.md)
- [workspace-sync (concept)](./workspace-sync.md) — same Mac↔ms02 path but
  running over the router's nominal LAN (rsync over SSH, which is unaffected).
- [ms02 (entity)](../entities/ms02.md)
