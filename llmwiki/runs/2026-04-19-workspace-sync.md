---
title: 2026-04-19 — first Workspace sync (Mac → ms02, additive)
kind: run
status: active
outcome: success
tags: [ms02, rsync, sync, workspace, first-run]
updated: 2026-04-19
related:
  - concepts/workspace-sync.md
  - entities/ms02.md
sources:
  - ../../playbooks/sync_workspace.yml
---

# First Workspace sync — Mac → ms02, additive

## Context

Moving day-to-day development onto `ms02` now that we've been reclaiming disk
on the Mac by deleting local-only files. The ask:

> We have previously done an rsync of the MacOS `~/Workspace` to ms02, but
> I have recently deleted files locally on the Mac to try save space. We need
> to resync preserving the remote if local files have been deleted.

Added `playbooks/sync_workspace.yml` that wraps an additive rsync with
operator-friendly ergonomics (dry-run, overrides, auto-detect rsync flavour).

## Bugs caught on dry-run (both fixed in the same file)

### 1. macOS `/usr/bin/rsync` is *openrsync*, not GNU rsync

```
/usr/bin/rsync --version  →  openrsync: protocol version 29
```

Rejects several GNU-only flags: `--info=progress2`, `--human-readable`, and
(our real problem) **`--chown`**.

**Fix**: probe the controller for rsync in order of preference
(`/opt/homebrew/bin/rsync` → `/usr/local/bin/rsync` → `/usr/bin/rsync`),
classify as GNU vs openrsync by parsing `--version`, apply full or reduced
flag set accordingly. For the openrsync case, run a post-sync `chown -R` on
the destination (second task in the playbook) so ownership is correct
regardless.

### 2. `delegate_to: localhost` leaks `ansible_host=localhost` into the rsync dest

First dry-run command argv included `root@localhost:/home/casibbald/Workspace/`
— because inside a localhost-delegated task Ansible resolves `ansible_host`
for *localhost*, not for the inventory host. Rsync would have happily pushed
to the Mac's own loopback.

**Fix**: capture the inventory target's connection info on a *non*-delegated
task at the top of the play:

```yaml
- name: Capture remote (ms02) connection details
  ansible.builtin.set_fact:
    _workspace_sync_remote_host: "{{ ansible_host | default(inventory_hostname) }}"
    _workspace_sync_remote_user: "{{ ansible_user | default('root') }}"
```

and use `_workspace_sync_remote_{host,user}` in the rsync argv. That's a
pattern worth remembering for any future playbook that delegates rsync /
scp / ssh to localhost.

### 3. Ansible strict-UTF8 deserializer chokes on raw rsync stdout

One of the 551k filenames on the Mac has a non-UTF8 byte sequence (Apple
legacy). Ansible 2.20+ `MODULE_STRICT_UTF8_RESPONSE` refuses to decode any
module stdout that contains one.

**Fix**: redirect the rsync command's full stdout/stderr to a local log
file (`/tmp/sync_workspace-<host>.rsync.log`); only return a sanitized,
bounded summary to Ansible, filtered through `LC_ALL=C tr -cd
'[:print:][:space:]'`. Full rsync trace remains on disk for operator
inspection.

## Final run

```bash
ansible-playbook playbooks/sync_workspace.yml -l ms02
```

| Metric | Value |
|---|---|
| Total files on Mac `~/Workspace` | **551,438** |
| Total size on Mac | 67.4 GB |
| Files transferred | **52,799** |
| Transferred file size (logical) | 8.45 GB |
| Data actually on wire | ~3.4 GB (rsync delta + compression) |
| Speedup ratio | **18.7×** |
| File-list generation | 12.3 s |
| File-list transfer | 18.6 s |
| Total duration | ~21 min |
| Mean throughput | ~2.9 MB/s |
| Remote files deleted | **0** |
| Remote-only files preserved | yes — remote is larger than source (see below) |

## Post-sync remote state (the important bit)

```
ssh root@ms02 'du -sh /home/casibbald/Workspace; ls -1 /home/casibbald/Workspace | wc -l'
   → 115G
   → 79
```

Mac source: 67.4 GB, 76 top-level dirs.
ms02 target: **115 GB, 79 top-level dirs**.

The remote is **~48 GB larger** than the Mac source, and has 3 more top-level
directories. That's the 48 GB of material the operator deleted on the Mac
to reclaim space — **all preserved on ms02 as intended**.

Ownership of the synced tree: `casibbald:casibbald` end-to-end (via the
post-sync `chown -R` in the playbook).

## Invariants the operator can rely on

- **No `--delete` anywhere.** Grep confirms zero occurrences in the playbook.
  If a future edit adds one, reviewers should block it — the whole point of
  this playbook is an additive sync. A "mirror" variant would be a separate
  file with an explicit confirmation gate.
- **Full rsync log always at `/tmp/sync_workspace-<host>.rsync.log`**, even
  on failures. Search it with `grep`, `zless`, whatever — Ansible never
  truncates it.

## Follow-ups

- [ ] Operator may want `brew install rsync` on the Mac so GNU rsync is
      detected next time (gets `--info=progress2`, `--human-readable`,
      `--chown=` inline, slightly faster checksum algos). Works without, but
      nicer with.
- [ ] Consider a scheduled flavour — a launchd .plist on the Mac that runs
      `ansible-playbook playbooks/sync_workspace.yml -l ms02` nightly. Would
      let the operator stop thinking about manual invocations entirely.
- [ ] If `~/Workspace` grows past what any single rsync run can comfortably
      handle, split per-project into targeted runs
      (`-e workspace_sync_src=$HOME/Workspace/microscaler/`).
