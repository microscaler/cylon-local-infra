---
title: user:casibbald (Ansible operator)
kind: entity
status: active
tags: [user, sudoers, operator]
updated: 2026-04-18
---

# user:casibbald

Ansible operator account on Sparks and `ms02`. Has full NOPASSWD sudo (default rule
when the sudoers role sees no explicit `rule:`).

| Field | Value |
|---|---|
| Role | Ansible login + interactive ops |
| Groups | `sudo, docker, video, render` (Sparks) |
| SSH | operator's Mac public key in `~/.ssh/authorized_keys` |
| `ansible_become` | `true` (`sudo`) |

## Use

- Run `ansible-playbook` against the Sparks from the operator's Mac (and from CI
  eventually).
- `ssh casibbald@nvidia1` for interactive probing.

## Don't

- Run long-lived GPU processes as `casibbald`. Drop to `nvidia`
  ([user-nvidia](./user-nvidia.md)) for those.
