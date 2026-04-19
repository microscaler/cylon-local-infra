---
title: macOS mDNS `.local` TLD trap (and why we use `.lan` for ms02 services)
kind: concept
status: active
tags: [macos, dns, mdns, networking, dev-hosts, kind]
updated: 2026-04-19
sources:
  - ../../docs/dev_hosts.md
  - ../../../msmctl_docs/KIND_HOSTS_FILE.md
---

# macOS mDNS `.local` TLD trap

## Symptom

A name like `registry.local` is listed in `/etc/hosts` pointing at
`127.0.0.1`, a tunnel (or a local service) is bound on that port, and yet
from the Mac:

```
$ curl -m 5 http://registry.local:5001/v2/
curl: (28) Resolving timed out after 3003 milliseconds
```

Meanwhile:

```
$ curl --resolve registry.local:5001:127.0.0.1 http://registry.local:5001/v2/
HTTP/1.1 200 OK
```

So the transport is fine — DNS resolution is the blocker.

## Root cause

macOS assigns `.local` to a dedicated **mDNS / Bonjour** resolver for service
discovery (AirPlay, AirPrint, printers, HomeKit). This resolver runs
*ahead of* `/etc/hosts` when `getaddrinfo` (curl, docker, kubectl, browsers,
most apps) is called for a `*.local` name:

```
$ scutil --dns | grep -B1 -A1 "domain   : local"
resolver #2
  domain   : local
  options  : mdns
```

Lookups multicast on the LAN, wait, time out, *then* fall through — and in
practice many clients give up at ~3 s before the fallback kicks in. Even when
the fallback does happen, the round-trip you just paid is pure latency on
every single cold request.

`dscacheutil -q host -a name registry.local` does answer correctly from
`/etc/hosts` — but that's a Directory Services query, not what
`getaddrinfo` uses. The mismatch is why "the hostname resolves" *and*
"curl times out" coexist, which is the thing that eats an afternoon to
debug the first time.

## Rule of thumb

Never use the `.local` suffix for any hostname you want to reach on macOS,
period. Use one of:

| TLD | Status | Notes |
|---|---|---|
| **`.lan`** | Unofficial but universal | Short, memorable, never mDNS-advertised. **Our choice.** |
| `.test` | RFC 2606 reserved for testing | RFC-blessed; good if you care about that |
| `.internal` | ICANN-reserved for private use | Good mental model for private infra |
| `.home.arpa` | RFC 8375 for home networks | Niche; longer to type |

## Convention (microscaler)

- `registry.lan` — the kind-registry on ms02 (port 5001 via SSH tunnel).
- `*.kind.lan` — per-service names routed through a future kind ingress
  controller. Wildcards are not honoured by `/etc/hosts`, so add explicit
  entries as services land: `127.0.0.1 grafana.kind.lan jaeger.kind.lan ...`

Server-side *nothing changes* — kind's internal containerd still uses
`localhost:5001` / `kind-registry:5000`. The `.lan` suffix is purely
operator-facing on the Mac.

## Caveats

- **Wildcards in `/etc/hosts` do not work.** Even though macOS's file reader
  accepts a line like `127.0.0.1 *.kind.lan`, it's treated as a literal
  name match. If you want true `*.kind.lan` expansion, you need a local DNS
  server (`dnsmasq`) plus `/etc/resolver/kind.lan`. For two-digit numbers
  of services, explicit per-service entries are simpler.
- **`.lan` is not officially reserved.** The IETF tabled a draft that would
  have reserved it (`draft-chapin-rfc6762bis`) but it never shipped. In
  practice no registrar issues `.lan` TLD names, and resolvers treat it as
  local. Low risk, high convenience.
- **If you later run an internet-facing service under your own domain**
  (e.g. `microscaler.dev`), don't mix `.lan` into it — keep the split clean.

## Fixing an already-polluted `/etc/hosts`

Typical bad state (what we had):

```
127.0.0.1  *.kind.local kind.local
127.0.0.1  registry.local
```

Renamed to:

```
127.0.0.1  *.kind.lan kind.lan
127.0.0.1  registry.lan
```

Then flush the resolver cache (optional; usually picks up on next request):

```
sudo dscacheutil -flushcache
sudo killall -HUP mDNSResponder
```

Verify with `dscacheutil -q host -a name registry.lan` (should return
`127.0.0.1`) and `curl -sI http://registry.lan:5001/v2/` (should return
`200 OK` if the tunnel is up).

## Related

- [ssh-alias-convention](./ssh-alias-convention.md) — paired concept:
  operator-friendly names that don't depend on `/etc/hosts` or DNS at all.
- [starlink-wifi-lan-port-filter](./starlink-wifi-lan-port-filter.md) — why
  the tunnel exists in the first place (the thing that forwards
  `registry.lan:5001` from Mac loopback to ms02 loopback).
- [`docs/dev_hosts.md`](../../docs/dev_hosts.md) § kind ingress / registry —
  operator-side usage.
- [`msmctl_docs/KIND_HOSTS_FILE.md`](../../../msmctl_docs/KIND_HOSTS_FILE.md) —
  public-facing kind hosts-file guide (now points at `.lan`).
