---
title: Starlink router (Gen3)
kind: entity
status: active
tags: [network, router, starlink, blocker]
updated: 2026-04-19
sources: [../runs/2026-04-19-starlink-tunnel-workaround.md]
---

# Starlink router

The consumer Starlink Gen3 router sits between the operator's Mac (Wi-Fi) and
`ms02` (wired via the router's LAN-out port to a switch). Both devices receive
`192.168.1.0/24` leases so *look* like same-LAN peers, but the router enforces
a **Wi-Fi ↔ LAN port filter** that is the root cause of the 2026-04-19
dev-host reachability outage.

## Observed behaviour

| From | To | Port | Result |
|---|---|---|---|
| Mac Wi-Fi (`192.168.1.130`) | ms02 wired (`192.168.1.189`) | 22 (SSH) | ✓ works |
| " | " | ICMP (ping) | ✓ works (~120 ms RTT — that's the giveaway) |
| " | " | 8080 (BRRTRouter via kind) | RST returned to Mac, no packet on ms02 eno4 |
| " | " | 10348 (Tilt UI) | SYN arrives once, retransmits silently dropped |
| " | " | 10349 (ad-hoc test listener) | same — drops regardless of ufw/iptables state on ms02 |

`tcpdump -i eno4` on ms02 with `iptables -F; iptables -P INPUT ACCEPT; ufw
disable` still showed the same packet loss, definitively locating the drop
**inside the router**, not on ms02.

`ping` RTT of ~120 ms between two hosts supposedly on the same `/24` is itself
diagnostic — a flat wired LAN is sub-millisecond; 120 ms is the Wi-Fi air
interface + router bridging + queuing.

## Implications

- SSH on port 22 works indefinitely — so the tunnel workaround is viable.
- Any listener on ms02 that must be reached from the Mac needs either:
  - A `LocalForward` via the tunnel (see `starlink-wifi-lan-port-filter`).
  - Or the Mac on the same wired L2 (USB 2.5GbE adapter ordered 2026-04-19).
- `ufw` config is still kept correct (`firewall_trusted_lan_tcp_ports:
  ["8000:11000"]`) so the moment we're flat-L2 nothing else changes.

## Retire

This entity will be marked **superseded** when the USB 2.5GbE adapter is in
place and sub-ms ping is verified — the Starlink router will still exist, but
will stop being part of the dev-host reachability path.
