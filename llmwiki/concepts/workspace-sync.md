---
title: Workspace sync (Mac → ms02, additive)
kind: concept
status: active
tags: [ms02, rsync, sync, workspace, dev-host]
updated: 2026-04-19
related:
  - entities/ms02.md
  - runs/2026-04-19-workspace-sync.md
sources:
  - ../../playbooks/sync_workspace.yml
  - ../../docs/docker-dev-host.md
---

# Workspace sync — Mac controller → ms02, additive

## Problem

The operator's Mac has limited disk; `ms02` has ~1.8 TB. We periodically rsync
`~/Workspace` from the Mac to `ms02`. When we tidy the Mac to reclaim space,
we do **not** want those local deletions to propagate — `ms02` should be a
superset of everything we've ever had locally.

Default rsync semantics (without `--delete`) give us exactly that:

- Local additions / modifications → pushed to `ms02`
- Local deletions → **ignored** (remote copy is kept)
- Remote-only files (e.g. things we've already cleaned off the Mac) → preserved

## The playbook

[`playbooks/sync_workspace.yml`](../../playbooks/sync_workspace.yml) ships this
as a first-class, idempotent-ish operation. Key guarantees:

- **Never passes `--delete`.** There is no "mirror" mode anywhere in the
  playbook; a future `sync_workspace_mirror.yml` would be a separate file
  with a confirmation gate.
- **Auto-detects rsync flavour.** macOS ships *openrsync* at
  `/usr/bin/rsync` (protocol 29) which lacks some GNU flags
  (`--info=progress2`, `--chown`, `--human-readable`). The playbook probes
  `/opt/homebrew/bin/rsync` → `/usr/local/bin/rsync` → `/usr/bin/rsync` and
  picks the first it finds, then applies a full or reduced flag set
  accordingly. `brew install rsync` is the cleanest upgrade but not
  required.
- **Belt-and-braces chown.** `ansible_user` on `ms02` is `root`; the dev
  account is `casibbald`. When GNU rsync is available we pass
  `--chown=casibbald:casibbald`; otherwise a post-sync `chown -R` on the
  destination covers the openrsync case.
- **Non-UTF8 filename tolerance.** Some files on the Mac have historical
  non-UTF8 byte sequences in their names. Ansible's strict UTF8 response
  deserializer refuses to handle these, so rsync's full stdout goes to a
  local log file; only a `tr -cd '[:print:][:space:]'`-filtered tail is
  returned to Ansible. Full output at
  `/tmp/sync_workspace-<host>.rsync.log` for operator inspection.
- **Honours inventory connection info across `delegate_to: localhost`.**
  Ansible_host / ansible_user are captured as facts on `ms02` *before*
  any delegated task, so the rsync destination always ends up as
  `root@ms02:/home/casibbald/Workspace/`, not `root@localhost:…` (the latter
  is what happens if you try to read `ansible_host` inside a localhost-
  delegated task).

## Usage

```bash
# preview — recommended first every time
ansible-playbook playbooks/sync_workspace.yml -l ms02 \
  -e workspace_sync_dry_run=true

# real sync
ansible-playbook playbooks/sync_workspace.yml -l ms02

# a different tree
ansible-playbook playbooks/sync_workspace.yml -l ms02 \
  -e workspace_sync_src="$HOME/Pictures/" \
  -e workspace_sync_dest=/home/casibbald/Backups/Pictures/

# extra rsync flags: bandwidth cap, more excludes, etc.
ansible-playbook playbooks/sync_workspace.yml -l ms02 \
  -e '{"workspace_sync_extra_rsync_opts":["--bwlimit=2000","--exclude=foo/"]}'
```

## Default excludes

These are skipped on both ends to avoid uselessly comparing volatile build
artefacts. Excluding them does **not** cause rsync to delete them remotely —
`--delete` is never set.

```
.DS_Store            __pycache__/        *.pyc           *.pyo
*.swp                *.swo               .venv/          node_modules/
target/              .terraform/         .next/          .pytest_cache/
.mypy_cache/         .ruff_cache/        .tox/           .cache/
.ipynb_checkpoints/
```

Override the whole list by passing `-e workspace_sync_excludes=[…]`, or
append with `workspace_sync_extra_rsync_opts: ["--exclude=…"]`.

## Invariants worth keeping

- **No `--delete` anywhere in this file.** If someone adds it, the playbook
  loses its one real guarantee.
- **Source trailing slash is load-bearing.** `~/Workspace/` copies the
  contents; `~/Workspace` (no slash) would create `Workspace/Workspace/` on
  the remote. Keep the slash.
- **`delegate_to: localhost` + hostvars capture.** Any future task that uses
  the rsync destination must use `_workspace_sync_remote_{host,user}` rather
  than `ansible_host` / `ansible_user` directly.
- **Post-sync `chown -R`.** Even when GNU rsync is available, running it
  again is cheap and handles the one case where a file existed from an
  earlier openrsync run.

## First deploy + operational notes

See [runs/2026-04-19-workspace-sync.md](../runs/2026-04-19-workspace-sync.md)
for the first real run against `ms02`:

- 551,438 files in `~/Workspace`, 67.4 GB total
- 52,799 files identified as needing transfer (= ~8.45 GB of delta)
- File-list generation: ~13 s; file-list transfer: ~28 s
- Two real bugs caught + fixed on first dry-run
  1. openrsync rejected `--info=progress2` / `--chown` / `--human-readable`
  2. `delegate_to: localhost` leaked `ansible_host=localhost` into the rsync dest
- Real run rsync'd over LAN in ~(tbd, filled in after run); no remote files
  removed.
