# Remote dev — Mac as thin client, ms02 as workstation

**Objective:** edit code in Cursor on the Mac, but have all clones, docker
builds, Rust `target/` dirs, `tilt` artifacts, node_modules, and kind
traffic land on **ms02**. The Mac stays light; ms02 does the work.

## TL;DR

| Surface       | Primary? | What it is                                | Where UI lives | Where files live | Where builds run |
|---------------|----------|-------------------------------------------|----------------|------------------|------------------|
| Remote-SSH    | **yes**  | Cursor's bundled `open-remote-ssh` ext    | Mac            | ms02             | ms02             |
| SSHFS mount   | secondary| Finder/grep/tree convenience from the Mac | Mac            | ms02             | **do not build against this** |
| Cloud Agents  | disabled | Cursor-cloud-driven agents on ms02        | (cursor.com)   | ms02             | ms02             |

The `cursor_agent_worker` role + systemd unit is preserved in the repo but
`cursor_agent_worker_provision: false` in `group_vars/dev_hosts.yml`. Flip
that flag later if you want cursor.com/agents to execute on ms02.

## Remote-SSH (primary path)

### One-time

1. `~/.ssh/config` on the Mac has an `ms02` entry (you already do — this
   is the same alias the justfile recipes use).
2. Cursor → Extensions → confirm **Open Remote - SSH** (publisher
   `anysphere`) is installed + enabled. If not, install from Extensions.
3. ms02 has the `dev_workstation` role applied:
   ```
   ansible-playbook playbooks/dev_hosts.yml -l ms02 --tags dev_workstation
   ```
   That ensures inotify headroom, `~/Workspace/microscaler/`, and the CLI
   packages a Cursor terminal expects (`git`, `tmux`, `build-essential`, …).

### Every time

1. In Cursor: `Cmd+Shift+P` → `Remote-SSH: Connect to Host…` → `ms02`.
   A new window opens, bottom-left status bar turns green: `SSH: ms02`.
   First-ever connection auto-installs `~/.cursor-server/` on ms02 (takes ~30 s).
2. `File → Open Folder` → `/home/casibbald/Workspace/microscaler` → Open.
3. Build inside that window's terminal (`Ctrl+\`` → `tilt up`, `cargo build`,
   `docker compose up`, …). Nothing hits the Mac.

### What this buys you

- inotify watchers on ms02's real filesystem — instant, reliable.
- rust-analyzer / tsserver / pylance / gopls run as `cursor-server` child
  processes on ms02, using ms02's CPU + RAM. The Mac stays cool.
- Git over SSH from the Cursor terminal uses ms02's GitHub SSH key, not the
  Mac's. (If you haven't got one on ms02 yet, generate + add at first clone.)
- Docker / Tilt / kind all use ms02's daemon; image layers stay there.
- `~/.cursor-server/` is the only new footprint on ms02 (~200 MB). No
  reverse impact on the Mac.

### When it's not enough

If you want a cursor.com-launched background agent to do the same work
(without a Cursor window open), that's a separate product — Cloud Agents
with a self-hosted worker. The `cursor_agent_worker` role is ready to
enable for that; it's disabled by default because day-to-day dev doesn't
need it.

## SSHFS (secondary convenience)

Use case: you want Finder, `rg`, `fzf`, or a quick `ls ~/Workspace/remote`
from the Mac's terminal without an SSH session. **Do not build against
this mount** — every read traverses SSH; `cargo build`, `tilt up`, and
`npm install` across the mount are painfully slow and unreliable.

### Layout on the Mac

Post-move, `~/Workspace/` is the single root you open in Cursor:

```
~/Workspace/
├── local/          # Mac-native clones (this repo lives under local/cylon-local-infra)
├── remote/         # SSHFS mount of ms02:~/Workspace  (populated by `just sshfs-up`)
└── Workspace_old/  # archived pre-move contents, kept as a safety net
```

Cursor sees `local/` and `remote/` as siblings in the tree. Edit Mac-side
stuff under `local/`; do anything build-heavy under `remote/` via Cursor
Remote-SSH to ms02 (or the `just ms02-*` helpers) so the compile lands on
ms02's real filesystem, not the FUSE mount.

### Install (Mac-side, one-time)

```
just sshfs-install        # macfuse + sshfs-mac via Homebrew
```

macFUSE needs a kernel-extension approval — System Settings → Privacy &
Security → "Allow", reboot if prompted.

### Daily

```
just sshfs-up             # mount ms02:~/Workspace at ~/Workspace/remote
just sshfs-status         # verify
just sshfs-down           # unmount
just sshfs-reconnect      # fix "Transport endpoint not connected" after Mac wakes
```

Finder: `open ~/Workspace/remote`.

> The mountpoint lives inside `~/Workspace/` so Cursor's single open
> folder (`~/Workspace`) sees both the Mac-local checkouts (`local/`)
> and the ms02 view (`remote/`) in one tree. The LAN round-trip to ms02
> over 10GbE is fast enough that casual reads (`rg`, `open`, Finder
> preview) feel native; heavy operations still go through a Remote-SSH
> terminal.

### Known rough edges

- macOS sleep → wake sometimes leaves the mount in a zombie state. Run
  `just sshfs-reconnect`.
- Finder may spray `._DS_Store` files — the mount flags already include
  `noappledouble`/`noapplexattr` to mitigate; if one slips through, drop
  it from ms02 with `find ~/Workspace -name '._*' -delete`.
- Editors pointed at the mount will feel sluggish and mis-fire file
  watchers. Don't do it. Use Remote-SSH for actual editing.

## Terminal-only helpers

When you don't want a Cursor window at all — just a quick shell or a
one-liner on ms02:

```
just ms02-shell                             # interactive, cd'd to ~/Workspace/microscaler
just ms02 'cd shared-kind-cluster && tilt up'
```

These use the same `ms02` SSH alias and TTY-forward, so Ctrl-C behaves.

## How the pieces interact

```
  Mac                                     ms02
  ───────────────────────────────         ──────────────────────────────
  Cursor UI (renders)                     ~/.cursor-server/ (Cursor's indexer,
     │                                       rust-analyzer, terminal shell,
     │  Remote-SSH (multiplexed SSH)         file watchers, LSPs, git)
     ├─────────────────────────────────>  /home/casibbald/Workspace/
     │                                       microscaler/
     │                                          ├─ cylon-local-infra/
     │                                          ├─ shared-kind-cluster/
     │                                          └─ …
     │  SSHFS (secondary, browse only)
     │
  ~/Workspace/remote ──────────────────>  same path via FUSE
     │
  Terminal / Finder
```

The Mac's own filesystem never sees `target/`, image layers, tilt
caches, or build artifacts. Only Cursor's UI state + `~/.cursor-server`'s
download cache live there.

## Troubleshooting

- **`Remote-SSH: Connect to Host…` missing from palette** — Extensions panel
  → install/enable `Open Remote - SSH` (publisher `anysphere`). Reload
  Cursor.
- **`cursor-server` install hangs on first connect** — `ssh ms02 'rm -rf
  ~/.cursor-server && ls ~/.vscode-server 2>/dev/null'`. Reconnect. If
  `~/.vscode-server` exists from a prior VSCode session and is corrupt,
  remove it too.
- **inotify ENOSPC mid-session** — `dev_workstation` role already sets
  `fs.inotify.max_user_watches=524288`. If you still hit the ceiling,
  bump `dev_workstation_inotify_max_user_watches` in
  `group_vars/dev_hosts.yml` and re-apply the role.
- **Builds slow inside the Remote-SSH terminal** — that's ms02 CPU / IO,
  not Cursor. Profile on ms02 directly.

## Related

- `roles/dev_workstation/` — applies the ms02-side prerequisites.
- `roles/cursor_agent_worker/` — disabled, kept for later Cloud-Agent use.
- `playbooks/dev_hosts.yml` — full ms02 provision entry point.
- `playbooks/sync_workspace.yml` + `just sync-to-ms02` — Mac → ms02 one-way
  rsync for when you've made edits locally (rare now that Remote-SSH is
  primary, but still handy for shared-kind-cluster / infra subtrees).
