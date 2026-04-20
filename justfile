# cylon-local-infra — operator command surface
#
# Primary use today: `dev-tunnel-*` recipes to keep an SSH tunnel open between
# this Mac and ms02 while the Starlink router filters LAN traffic on the ports
# our dev stack needs. Retires when the USB 2.5GbE adapter arrives.
#
# See:
#   - docs/dev_hosts.md
#   - ~/.ssh/config.d/ms02-dev-tunnel
#   - llmwiki/concepts/starlink-wifi-lan-port-filter.md

set shell := ["bash", "-uc"]

# SSH alias defined in ~/.ssh/config.d/ms02-dev-tunnel
tunnel_host := "ms02-dev-tunnel"
# ControlMaster socket — matches the ControlPath in the SSH config stanza.
# Prefixed `tunnel-` to keep it distinct from the plain `Host ms02` alias used
# by Cursor Remote-SSH, so `dev-tunnel-down` doesn't kill the editor session.
# %h in the ControlPath expands to the HostName from the SSH config (the IP
# 192.168.1.189), not the alias — that's deliberate: aliases move, IPs mostly
# don't.
tunnel_sock := env_var("HOME") + "/.ssh/cm/tunnel-casibbald@192.168.1.189:22"
# Where `ssh -f` writes the "connection established" confirmation.
tunnel_log  := "/tmp/ms02-dev-tunnel.log"

default:
    @just --list

# ── Tunnel lifecycle ────────────────────────────────────────────────────────

# Start the SSH tunnel in the background (idempotent).
dev-tunnel-up:
    #!/usr/bin/env bash
    set -euo pipefail
    if ssh -O check {{tunnel_host}} 2>/dev/null; then
        echo "✓ tunnel already up ({{tunnel_host}})"
        exit 0
    fi
    : > {{tunnel_log}}
    # -N = no remote command; -f = background after auth; ExitOnForwardFailure
    # aborts if any port is already bound locally.
    ssh -N -f {{tunnel_host}} 2>>{{tunnel_log}}
    sleep 1
    ssh -O check {{tunnel_host}}
    echo
    echo "Tilt UI:     http://localhost:10348/"
    echo "Grafana:     http://localhost:3000/"
    echo "Prometheus:  http://localhost:9090/"
    echo "Jaeger:      http://localhost:16686/"
    echo "MinIO:       http://localhost:9001/   (s3 on :9000)"
    echo "kube-apiserver:  https://localhost:38839   (matches ms02 kubeconfig verbatim)"
    echo "SOCKS5:      127.0.0.1:1080   (catch-all for un-mapped ports)"

# Stop the SSH tunnel + remove the ControlMaster socket.
dev-tunnel-down:
    #!/usr/bin/env bash
    set -euo pipefail
    if ssh -O check {{tunnel_host}} 2>/dev/null; then
        ssh -O exit {{tunnel_host}} 2>&1 || true
        echo "✓ tunnel stopped"
    else
        echo "tunnel was not running"
    fi
    rm -f "{{tunnel_sock}}"

# Show whether the ControlMaster is up + list all forwarded ports that are
# actually bound on the Mac right now.
dev-tunnel-status:
    #!/usr/bin/env bash
    set -euo pipefail
    if ssh -O check {{tunnel_host}} 2>/dev/null; then
        echo "✓ tunnel UP  (socket: {{tunnel_sock}})"
    else
        echo "✗ tunnel DOWN"
        exit 1
    fi
    echo
    echo "Listening ports on the Mac (from the tunnel):"
    lsof -nP -iTCP -sTCP:LISTEN 2>/dev/null \
        | awk '$1 == "ssh" {print "  " $9}' \
        | sort -u

# Probe every forwarded UI/API port. Prints one line per port with HTTP code.
# Green (2xx/3xx) = reachable; '000' = tunnel up but backend not listening yet
# (e.g. Tilt still pulling images, Grafana not rolled out).
dev-tunnel-check:
    #!/usr/bin/env bash
    set -u
    if ! ssh -O check {{tunnel_host}} 2>/dev/null; then
        echo "tunnel is DOWN — run 'just dev-tunnel-up' first" >&2
        exit 1
    fi
    probe() {
        local port="$1" label="$2" code
        code=$(curl -s -o /dev/null -w "%{http_code}" -m 3 "http://localhost:${port}/" 2>/dev/null || true)
        [[ -z "$code" ]] && code="000"
        printf "  %-5s  %-24s  http=%s\n" "$port" "$label" "$code"
    }
    echo "Probing dev-stack UIs via tunnel (3s timeout each):"
    probe 10348 "Tilt UI"
    probe 3000  "Grafana"
    probe 9090  "Prometheus"
    probe 16686 "Jaeger"
    probe 4040  "Pyroscope"
    probe 3100  "Loki"
    probe 9001  "MinIO console"
    probe 8080  "BRRTRouter"
    probe 8000  "Gateway (PW API)"
    probe 5001  "Docker registry"

# Tail whatever ssh has emitted (connection errors, keepalive drops, etc).
dev-tunnel-logs:
    @tail -f {{tunnel_log}}

# Quick sanity: show the SSH alias's effective config (--host, keepalives,
# forwards) that ssh -G would use. Handy when debugging "why isn't port X
# forwarded?".
dev-tunnel-config:
    @ssh -G {{tunnel_host}} 2>/dev/null | grep -E "^(hostname|user|port|controlmaster|controlpath|serveraliveinterval|exitonforwardfailure|localforward|dynamicforward) " | sort

# Restart: down then up. Use after adding/removing forwards in the SSH config.
dev-tunnel-restart: dev-tunnel-down dev-tunnel-up

# ── Workspace sync (thin wrapper for the ansible playbook) ──────────────────

# Push ~/Workspace/microscaler/ from Mac → ms02 additively (no deletions).
# Use after editing shared-kind-cluster or other microscaler repos locally.
sync-to-ms02:
    ansible-playbook playbooks/sync_workspace.yml -l ms02

# Push a single subtree to ms02 (e.g. just sync-path-to-ms02 shared-kind-cluster/)
sync-path-to-ms02 path:
    ansible-playbook playbooks/sync_workspace.yml -l ms02 \
        -e workspace_sync_src="$HOME/Workspace/microscaler/{{path}}" \
        -e workspace_sync_dest=/home/casibbald/Workspace/microscaler/{{path}}

# ── Remote dev: Mac → ms02 ──────────────────────────────────────────────────
#
# Dual-track "Mac editor / ms02 compute" pattern. See docs/remote-dev.md.
#
# PRIMARY:    Remote-SSH from local Cursor (anysphere.open-remote-ssh bundled).
#             Command palette → `Remote-SSH: Connect to Host…` → ms02. cursor-
#             server runs on ms02; files/terminal/builds/tilt all execute
#             there. Nothing in this justfile is strictly required for that
#             flow — ssh itself is enough.
#
# SECONDARY:  SSHFS mount for Finder/grep/tree convenience. Read/write, but
#             do NOT build against the mount — use the Remote-SSH terminal
#             (or `just ms02-ssh -t …`) for builds. The mount is for quick
#             file-level browsing from Mac tools, not as a replacement for
#             a real on-ms02 file system.

# Mac-side install prerequisites for SSHFS. Requires Homebrew + cask, and a
# one-time Security & Privacy → "Allow" for Benjamin Fleischer's kernel ext.
sshfs-install:
    #!/usr/bin/env bash
    set -euo pipefail
    if ! command -v brew >/dev/null; then
        echo "Homebrew not found. Install from https://brew.sh first." >&2
        exit 1
    fi
    brew install --cask macfuse
    brew install gromgit/fuse/sshfs-mac
    echo
    echo "macFUSE may prompt for a kernel-extension approval."
    echo "System Settings → Privacy & Security → 'Allow' → reboot if asked."
    echo "Then run: just sshfs-up"

# Mount ms02:/home/casibbald/Workspace at ~/Workspace/remote on the Mac.
#
# Layout the operator lives in:
#   ~/Workspace/local/        — Mac-native clones (this repo lives here)
#   ~/Workspace/remote/       — SSHFS view of ms02:~/Workspace
#   ~/Workspace/Workspace_old — archived old ~/Workspace contents
#
# Cursor opens ~/Workspace as the workspace root, so both `local/` and
# `remote/` sit side by side in the file tree. Use local/ for Mac-side
# edits (small scripts, infra, anything fast); use remote/ for real
# builds-on-ms02 via Cursor Remote-SSH or the `just ms02-*` helpers.
# Flags chosen for editor-friendly browsing:
#   reconnect, ServerAliveInterval, auto_cache  → survive link blips
#   defer_permissions                           → macOS Finder shows files
#   noappledouble, noapplexattr                 → no ._DS_Store spray on ms02
#   volname                                     → friendly mount name in Finder
sshfs-up:
    #!/usr/bin/env bash
    set -euo pipefail
    local_mount="$HOME/Workspace/remote"
    remote="ms02:/home/casibbald/Workspace"
    if mount | grep -q "on $local_mount "; then
        echo "✓ already mounted at $local_mount"
        exit 0
    fi
    mkdir -p "$local_mount"
    if ! command -v sshfs >/dev/null; then
        echo "sshfs not installed. Run 'just sshfs-install' first." >&2
        exit 1
    fi
    sshfs "$remote" "$local_mount" \
        -o reconnect \
        -o ServerAliveInterval=15 \
        -o ServerAliveCountMax=3 \
        -o auto_cache \
        -o defer_permissions \
        -o noappledouble \
        -o noapplexattr \
        -o volname=ms02-workspace
    echo "✓ mounted $remote at $local_mount"
    echo "Browse in Finder: open $local_mount"

# Unmount the SSHFS share. Uses umount -f because macOS sometimes leaves a
# stale mount after a sleep cycle; the -f is safe for a userspace FUSE mount.
sshfs-down:
    #!/usr/bin/env bash
    set -euo pipefail
    local_mount="$HOME/Workspace/remote"
    if mount | grep -q "on $local_mount "; then
        umount -f "$local_mount" 2>/dev/null || diskutil unmount force "$local_mount"
        echo "✓ unmounted $local_mount"
    else
        echo "not mounted"
    fi

# Show mount status + quick readability check (list top-level entries).
sshfs-status:
    #!/usr/bin/env bash
    set -u
    local_mount="$HOME/Workspace/remote"
    if ! mount | grep -q "on $local_mount "; then
        echo "✗ NOT mounted"
        exit 1
    fi
    echo "✓ mounted $local_mount"
    echo
    echo "Top-level entries (first 15):"
    ls "$local_mount" 2>/dev/null | head -15

# Reconnect cycle. Use after sleep-cycle or network change leaves the mount
# in a "Transport endpoint not connected" zombie state.
sshfs-reconnect: sshfs-down sshfs-up

# Disable Spotlight / Time Machine on ~/Workspace/remote so macOS doesn't try
# to index or back up 100+ remote dirs over the FUSE link (would saturate SSH
# and spin ms02 fans for no benefit — all the ground truth lives on ms02).
#
# Belt + braces:
#   1. `mdutil -X` (disable + unregister Spotlight for this path). Works on
#      FUSE volumes; if state was "unknown indexing state" already, this is a
#      no-op but idempotent.
#   2. Add to Spotlight Privacy via `defaults`. Survives mount cycles because
#      Spotlight keys off the absolute path, not the volume UUID.
#   3. `tmutil addexclusion` — tell Time Machine to skip the mount. (Only
#      meaningful if TM includes $HOME, but harmless either way.)
#
# Requires sudo for #1 (mdutil). If the sudo prompt doesn't fire, your
# cached creds are good; otherwise you'll be asked for your Mac password.
spotlight-exclude-remote:
    #!/usr/bin/env bash
    set -uo pipefail
    local_mount="$HOME/Workspace/remote"
    if [ ! -d "$local_mount" ]; then
        echo "✗ $local_mount does not exist — mount it first (just sshfs-up)"
        exit 1
    fi
    echo "==> 1/3  mdutil -X (disable + unregister Spotlight on the mount)"
    # Expected on FUSE mounts: "Could not resolve .../.Spotlight-V100" — that
    # just means macOS never bothered to create an index store here, which is
    # exactly the state we want. We still run the command to be explicit.
    sudo mdutil -X "$local_mount" 2>&1 | sed 's/^/   /' || true
    echo
    echo "==> 2/3  Spotlight Privacy list (~/Library/Preferences/com.apple.spotlight.plist)"
    if defaults read com.apple.spotlight Exclusions 2>/dev/null | grep -qF "\"$local_mount\""; then
        echo "   already excluded: $local_mount"
    else
        defaults write com.apple.spotlight Exclusions -array-add "$local_mount"
        echo "   added to Exclusions: $local_mount"
        # Nudge mds to pick up the change. Safe — it's the Spotlight daemon,
        # not anything we depend on.
        sudo killall mds 2>/dev/null || true
    fi
    echo
    echo "==> 3/3  tmutil addexclusion -p (path-based Time Machine skip)"
    # -p writes into /Library/Preferences/com.apple.TimeMachine.plist instead
    # of an xattr on the mount. Required for FUSE/sshfs paths — the default
    # (xattr) form fails with EINVAL because FUSE can't honor the Time Machine
    # exclusion xattr. -p works regardless of underlying filesystem and
    # survives unmount/remount cycles.
    sudo tmutil addexclusion -p "$local_mount" 2>&1 | sed 's/^/   /' || true
    echo
    echo "==> verify"
    mdutil -s "$local_mount" 2>&1 | sed 's/^/   /' || true
    tmutil isexcluded "$local_mount" 2>&1 | sed 's/^/   /' || true

# Reverse of spotlight-exclude-remote — if you ever DO want indexing back.
spotlight-include-remote:
    #!/usr/bin/env bash
    set -uo pipefail
    local_mount="$HOME/Workspace/remote"
    sudo mdutil -i on "$local_mount" || true
    # Rebuild the Exclusions array without our path. defaults has no remove-one
    # for array elements, so filter via plutil → awk is the least-bad option.
    current=$(defaults read com.apple.spotlight Exclusions 2>/dev/null || echo "()")
    if echo "$current" | grep -qF "\"$local_mount\""; then
        # Delete and re-add all other entries.
        others=$(echo "$current" | awk -v drop="$local_mount" '
            /^[[:space:]]*"/ {
                gsub(/^[[:space:]]*"|"[[:space:]]*,?[[:space:]]*$/, "", $0)
                if ($0 != drop) print $0
            }
        ')
        defaults delete com.apple.spotlight Exclusions 2>/dev/null || true
        while IFS= read -r p; do
            [ -n "$p" ] && defaults write com.apple.spotlight Exclusions -array-add "$p"
        done <<< "$others"
        sudo killall mds 2>/dev/null || true
        echo "removed from Exclusions: $local_mount"
    else
        echo "not in Exclusions, nothing to do"
    fi
    sudo tmutil removeexclusion -p "$local_mount" 2>&1 || true
    mdutil -s "$local_mount" 2>&1 || true

# ── Remote dev: quick ms02 shell helpers ────────────────────────────────────

# Interactive shell on ms02 in the workspace dir. For when you want to run
# a one-off `tilt up`, `cargo test`, or `docker ps` without opening a
# Cursor Remote-SSH window.
ms02-shell:
    ssh -t ms02 'cd ~/Workspace/microscaler && exec $SHELL -l'

# One-off command on ms02 (quoted). Example:
#   just ms02 'cd ~/Workspace/microscaler/shared-kind-cluster && tilt up'
ms02 +cmd:
    ssh -t ms02 '{{cmd}}'
