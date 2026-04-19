---
title: SSH alias convention for microscaler hosts
kind: concept
status: active
tags: [ssh, ops, convention, dev-hosts, sparks]
updated: 2026-04-19
sources:
  - ../entities/nvidia1.md
  - ../entities/nvidia2.md
  - ../entities/ms02.md
  - ../../docs/dev_hosts.md
---

# SSH alias convention (Mac → microscaler hosts)

Canonical Mac-side SSH aliases for every host the operator touches. The goal
is a `/etc/hosts`-free dev environment: the laptop moves between networks
(Starlink home, co-working, café, hotel) and should never depend on a local
resolver file to reach infra.

## Rules

1. **One file per host-group under `~/.ssh/config.d/`.** Today:
   - `~/.ssh/config.d/ms02` — dev workstation.
   - `~/.ssh/config.d/ms02-dev-tunnel` — ms02's Starlink-workaround tunnel.
   - `~/.ssh/config.d/sparks` — `nvidia1` + `nvidia2`.
2. **`HostName` is always an IP.** Never a short name that depends on
   `/etc/hosts` or mDNS. The alias itself is the operator-friendly label;
   the IP is the plumbing.
3. **Default alias = operator user `casibbald`.** Passwordless sudo, lives on
   every host, what Ansible uses. `ssh nvidia1`, `ssh ms02` — always
   `casibbald` unless suffixed.
4. **Secondary alias = runtime user, suffixed.** Use `<host>-runtime` for
   `nvidia` (Sparks; owns Ray/vLLM/hf-prefetch) and `<host>-root` for root
   (ms02; for `/etc` surgery). E.g. `ssh nvidia1-runtime`, `ssh ms02-root`.
5. **One ControlMaster socket per `(user, host, port)` tuple.** Shared across
   aliases that point at the same target — except `ms02-dev-tunnel`, which
   uses `tunnel-<user>@<host>:<port>` on purpose so the tunnel and the
   editor session can come and go independently.
6. **`ForwardAgent yes`** on the default-user aliases — `git push` from a
   remote Cursor terminal uses the Mac's SSH keys; no secrets ever land on
   the host.
7. **`AddressFamily inet`** — force IPv4 (repeats the global default for
   safety). IPv6 over Starlink has been unreliable in our tests.
8. **Sensible keepalives** — `ServerAliveInterval 30`, `TCPKeepAlive yes`.

## Current alias table

| Alias | IP | User | ControlMaster socket | Purpose |
|---|---|---|---|---|
| `ms02` | `192.168.1.189` | `casibbald` | `~/.ssh/cm/casibbald@192.168.1.189:22` | Cursor Remote-SSH, Ansible, everyday ssh |
| `ms02-root` | `192.168.1.189` | `root` | `~/.ssh/cm/root@192.168.1.189:22` | `/etc/` surgery |
| `ms02-dev-tunnel` | `192.168.1.189` | `casibbald` | `~/.ssh/cm/tunnel-casibbald@192.168.1.189:22` | Tilt UI + LocalForwards + SOCKS |
| `nvidia1` | `192.168.1.104` | `casibbald` | `~/.ssh/cm/casibbald@192.168.1.104:22` | Ansible, leader-Spark ops |
| `nvidia1-runtime` | `192.168.1.104` | `nvidia` | `~/.ssh/cm/nvidia@192.168.1.104:22` | Ray/vLLM/hf-prefetch inspection |
| `nvidia2` | `192.168.1.229` | `casibbald` | `~/.ssh/cm/casibbald@192.168.1.229:22` | Ansible, follower-Spark ops |
| `nvidia2-runtime` | `192.168.1.229` | `nvidia` | `~/.ssh/cm/nvidia@192.168.1.229:22` | Ray worker inspection |

## How this plays with Ansible

Ansible consults `~/.ssh/config` before the system resolver, so the inventory
names (`nvidia1`, `nvidia2`, `ms02` in `inventory/hosts.yml`) resolve through
these aliases. No `ansible_host` overrides needed, no DNS dependency, no
`/etc/hosts` dependency.

Caveats:

- ms02's inventory sets `ansible_user: root` but the Mac's default alias for
  `ms02` is `casibbald`. Ansible passes `-l root@ms02` on the wire so SSH's
  user gets overridden per-connection — the alias's `casibbald` default only
  kicks in for interactive `ssh ms02` calls. Both work.
- Sparks use `ansible_user: "{{ spark_ansible_user | default('casibbald') }}"`
  which matches the alias's default user — zero friction.

## How this plays with Cursor / VS Code Remote-SSH

Remote-SSH only lists hosts that have an explicit `Host <name>` entry in
`~/.ssh/config` (or its `Include`s). That's the *only* reason the dedicated
stanzas exist rather than letting `/etc/hosts` carry the name:

- `Cmd+Shift+P → Remote-SSH: Connect to Host…` picker shows `ms02`,
  `ms02-root`, `nvidia1`, `nvidia1-runtime`, `nvidia2`, `nvidia2-runtime`.
- The `*-runtime` aliases are mostly for terminal poking, not for opening
  folders — their `$HOME` is `/home/nvidia`, which has the HF cache and
  Docker env-files for the vLLM stack.

## Retiring / adding a host

1. Add or remove the stanza in the appropriate `~/.ssh/config.d/<group>` file.
2. Update the relevant `llmwiki/entities/<host>.md` → add/remove the **Mac
   SSH aliases** row.
3. If the host changes IP: update `HostName` in the stanza; inventory is
   agnostic (still just `nvidia1`/`nvidia2`/`ms02`).

## Related

- [starlink-wifi-lan-port-filter](./starlink-wifi-lan-port-filter.md) — why
  ms02 additionally has a tunnel alias with 22 `LocalForward`s.
- [ms02](../entities/ms02.md), [nvidia1](../entities/nvidia1.md),
  [nvidia2](../entities/nvidia2.md).
