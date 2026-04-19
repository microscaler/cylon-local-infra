---
title: Rename kind/registry host entries from `.local` to `.lan`
kind: run
status: success
date: 2026-04-19
hosts: [mac]
tags: [macos, dns, mdns, kind, registry, dev-hosts]
related:
  - ../concepts/macos-mdns-local-tld-trap.md
  - ../concepts/ssh-alias-convention.md
  - ../concepts/starlink-wifi-lan-port-filter.md
  - ../runs/2026-04-19-starlink-tunnel-workaround.md
  - ../../docs/dev_hosts.md
  - ../../../msmctl_docs/KIND_HOSTS_FILE.md
---

# Rename kind/registry host entries from `.local` to `.lan`

## Context

After shipping the SSH tunnel workaround for the Starlink Wi-Fi↔LAN port
filter ([starlink-tunnel-workaround](./2026-04-19-starlink-tunnel-workaround.md)),
the Mac could reach `ms02:5001` over the forwarded loopback — but only by
IP or by `curl --resolve`. Using the friendly name `registry.local` from
`/etc/hosts` stalled for ~3 s and then returned `NXDOMAIN`.

Diagnosis ([macos-mdns-local-tld-trap](../concepts/macos-mdns-local-tld-trap.md)):
macOS routes `.local` through Bonjour/mDNS before `/etc/hosts` because
`scutil --dns` registers a dedicated resolver for it:

```
resolver #2
  domain   : local
  options  : mdns
```

`getaddrinfo` (what curl / docker / browsers call) goes through the
multicast path and gives up before the hosts-file fallback fires.
`dscacheutil -q host -a name registry.local` answered correctly — but
that's a different API, so "the hostname resolves" *and* "curl times out"
coexisted.

## Decision

Rename from `.local` → `.lan`. Alternatives considered and rejected:

| Option | Why not |
|---|---|
| `.test` (RFC 2606) | RFC-blessed but feels like a test-only hint; longer. |
| `.internal` (ICANN private-use) | Fine, but `.lan` is punchier and just as safe. |
| `.home.arpa` (RFC 8375) | Semantically accurate but verbose. |
| Install `dnsmasq` + `/etc/resolver/kind.lan` | Heavier than needed for a handful of per-service entries. Keep in pocket for real wildcard resolution. |

`.lan` wins on ergonomics, is never mDNS-advertised, and no registrar
issues TLD names under it.

## Change

Operator updated `/etc/hosts` on the Mac:

```diff
-127.0.0.1  *.kind.local kind.local
-127.0.0.1  registry.local
+127.0.0.1  *.kind.lan kind.lan
+127.0.0.1  registry.lan
```

Wiki + docs were already aligned with `.lan` earlier in the session —
this run closes the loop by verifying resolution end-to-end.

## Verification

```
$ dscacheutil -q host -a name registry.lan
name: registry.lan
ip_address: 127.0.0.1

$ curl -sI --connect-timeout 3 -m 5 http://registry.lan:5001/v2/
HTTP/1.1 200 OK
Content-Length: 2
Content-Type: application/json; charset=utf-8
Docker-Distribution-Api-Version: registry/2.0
X-Content-Type-Options: nosniff
```

Previously (for comparison):

```
$ curl -m 5 http://registry.local:5001/v2/
curl: (28) Resolving timed out after 3003 milliseconds
```

Transport hadn't changed — same SSH tunnel, same kind-registry — only the
resolver path flipped from mDNS-first to hosts-file-first.

## Caveats captured

- **No wildcards in `/etc/hosts`.** `127.0.0.1 *.kind.lan kind.lan` matches
  the literal `*.kind.lan` and the literal `kind.lan`, nothing else:
  ```
  $ dscacheutil -q host -a name grafana.kind.lan
  (empty — does not resolve)
  ```
  When the kind ingress controller lands we'll either enumerate
  per-service entries (simpler, what
  [`msmctl_docs/KIND_HOSTS_FILE.md`](../../../msmctl_docs/KIND_HOSTS_FILE.md)
  recommends today) or switch to `dnsmasq` + `/etc/resolver/kind.lan`.
- **Linux + Windows are unaffected.** They resolve `/etc/hosts` before any
  resolver, so the rename is purely a Mac workaround. Container-internal
  names (`kind-registry:5000`, `localhost:5001`) are unchanged.

## Follow-ups

- None blocking. Existing SSH tunnel (`cylon-local-infra/justfile`
  `dev-tunnel-up`) already forwards port 5001, so `registry.lan:5001`
  Just Works now that the resolver is out of the way.
- When ingress arrives: add explicit `grafana.kind.lan` / `prometheus.kind.lan`
  / etc. entries to `/etc/hosts` and `LocalForward 80 localhost:80` +
  `LocalForward 443 localhost:443` to `~/.ssh/config.d/ms02-dev-tunnel`
  (already documented in `docs/dev_hosts.md` § "Staged for when a kind
  ingress controller lands").

## Outcome

`registry.lan:5001` reachable from the Mac by name, no mDNS stall, no
tunnel changes required. Convention codified across wiki + repo docs
+ msmctl docs.
