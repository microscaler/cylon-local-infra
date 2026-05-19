---
title: "2026-04-19 — Starlink Wi-Fi ↔ LAN port filter diagnosed; SSH tunnel workaround shipped"
kind: run
status: resolved
tags: [network, starlink, ssh, tunnel, dev-hosts, ms02]
updated: 2026-04-19
sources:
  - ../entities/starlink-router.md
  - ../concepts/starlink-wifi-lan-port-filter.md
  - ../../docs/dev_hosts.md
  - ../../justfile
---

# Starlink Wi-Fi ↔ LAN port filter — diagnosed + tunnel shipped

## Context

After standing up the kind cluster on `ms02` via `just dev-up` and binding
Tilt to `0.0.0.0:10348`, the Mac could not reach the Tilt UI from the LAN.
`ufw` was adjusted to permit `8000:11000/tcp` from `192.168.1.0/24`, listeners
confirmed on ms02, but Mac curl/nc still failed with a mix of "Connection
refused" and silent timeouts.

## Diagnosis

Conducted in-session, deliberately ruling out one layer at a time:

1. **ufw accepted the packets.** `iptables -L ufw-user-input -n -v` showed the
   `192.168.1.0/24 → multiport 8000:11000` rule incrementing by the exact
   number of SYN attempts from the Mac. So ufw is not the blocker.

2. **Tilt is IPv6-only at the HTTP socket.** `ss -tlnap4` returned nothing for
   `:10348`; `ss -tlnap6` showed the listener on `*:10348`. `bindv6only=0` so
   v4 should still work via v4-mapped addresses — enough to make this a
   contributing factor for the Tilt case but not the root cause (see next
   step).

3. **Any v4 listener on a dev port also fails.** A pure-v4 `python3
   socketserver.TCPServer(("0.0.0.0", 10349), …)` with `ss` confirming the
   bind still drew `Connection refused` from the Mac.

4. **Kill ufw entirely → still fails.** `iptables -F; iptables -X; iptables
   -P INPUT ACCEPT; ufw disable` and the Mac *still* got RST'd. Firewall
   ruled out.

5. **Tcpdump proves the router is the drop site.** On ms02 `tcpdump -i eno4
   host 192.168.1.130 and port 10349` captured **zero** packets while nc from
   Mac returned `Connection refused` — the RST cannot be coming from ms02
   because ms02 never received the SYN. The RST had to originate in the
   Starlink router between them.

6. **Latency sanity check.** `ping` RTT between Mac and ms02 was ~120 ms.
   On a flat /24 this is absurd; on a Wi-Fi-to-Ethernet bridge inside a
   consumer router it's the expected number.

7. **Control: port 22 always works.** Every `ssh root@ms02` during the
   session succeeded, even concurrently with the failed LAN probes —
   confirming the router whitelists well-known ports and filters the rest.

## Decision

Two parallel paths:

1. **Hardware fix (days).** Ordered USB-C → 2.5GbE adapter; plug Mac into the
   same switch as ms02, bypassing the Starlink router's Wi-Fi↔LAN bridge.
2. **Tunnel workaround (now).** Single multiplexed SSH connection over port
   22 with ~22 `LocalForward`s + SOCKS5, driven by a justfile.

## Implementation

New / changed:

- `~/.ssh/config.d/ms02-dev-tunnel` — Host stanza, ControlMaster, forwards.
- `~/.ssh/config` — added `Include ~/.ssh/config.d/*`.
- `cylon-local-infra/justfile` — `dev-tunnel-{up,down,status,check,restart,
  logs,config}` recipes, plus `sync-to-ms02` / `sync-path-to-ms02` wrappers.
- `cylon-local-infra/docs/dev_hosts.md` — operator guide with diagnosis,
  usage, and retire path.
- `llmwiki/entities/starlink-router.md` — symptom table.
- `llmwiki/concepts/starlink-wifi-lan-port-filter.md` — the pattern +
  workaround.

All shipped without adding shell scripts (per repo policy): justfile is the
command surface, Python only where the ansible playbooks already live.

## Verification

```text
$ just dev-tunnel-up
Master running (pid=56685)
Tilt UI:     http://localhost:10348/
Grafana:     http://localhost:3000/
...

$ just dev-tunnel-check
Probing dev-stack UIs via tunnel (3s timeout each):
  10348  Tilt UI                   http=200
   9001  MinIO console             http=200
   5001  Docker registry           http=200
  …

$ KUBECONFIG=~/.kube/config-ms02 kubectl get pods -n observability
NAME                              READY   STATUS    RESTARTS   AGE
grafana-688f5b7846-5mg85          1/1     Running   0          21m
jaeger-7f4c98ff75-b4lsd           1/1     Running   0          21m
loki-7d86ff5886-smbt2             1/1     Running   0          21m
otel-collector-6666b877cb-kvjmg   1/1     Running   0          20m
prometheus-fc9f86c7d-tlw5q        1/1     Running   0          21m
promtail-dzsdh                    1/1     Running   0          21m
```

Tilt UI reachable, kubectl reachable (the kind-shipped `https://127.0.0.1:
38839` kubeconfig URL happens to align with our `LocalForward 38839 localhost:
38839` — zero-touch).

Grafana/Prom/Jaeger NodePort probes via docker-proxy on ms02 itself time out
(`curl localhost:3000` from *on ms02* returns 000); that's a separate
kind-nodeport-routing issue unrelated to the Starlink problem, worked around
for now by `kubectl port-forward` through the already-working tunnel.

## Follow-ups

- [ ] When USB 2.5GbE adapter arrives: plug in, verify sub-ms ping, `just
      dev-tunnel-down`, park the SSH stanza.
- [ ] Separately investigate why ms02's `docker-proxy`→kind-nodeport path
      times out for `3000/9090/16686` etc. despite pods `1/1 Running`
      (likely a ClusterIP/endpoint/CNI issue inside the kind container, or
      the kind node's kube-proxy not matching the iptables-mode/rules).

## Update (2026-04 onward)

Home LAN + **ms02 `ufw`** (trusted `192.168.1.0/24`, **2049**, **7000–12000**, …)
made SSH port-forwards unnecessary for day-to-day dev. **cylon-local-infra**
removed **`dev-tunnel-*`** just recipes and the Ansible-managed
**`ms02-dev-tunnel`** fragment; **`mac-provision`** deletes that file if present.
Use **`just ms02-lan-check`**, **`just nfs-up`**, and kubeconfig **`server:`** pointed
at **`https://192.168.1.189:38839`** (or **`https://ms02:38839`**) — see current
**`docs/dev_hosts.md`**.
