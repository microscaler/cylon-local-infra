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
# SECONDARY:  NFSv4 mount at ~/Workspace/remote (see `nfs-*` recipes below)
#             for Finder/grep/tree convenience. Read/write, but do NOT build
#             against the mount — use the Remote-SSH terminal (or
#             `just ms02-shell`) for builds. The mount is for quick file-level
#             browsing from Mac tools, not a replacement for a real on-ms02
#             filesystem.
#
# HISTORICAL: SSHFS was the original mount protocol here (`sshfs-*` recipes
#             lived in this section). Retired 2026-04-20 — NFSv4 with
#             READDIRPLUS + TCP streaming beats SSHFS's per-file stat storms
#             badly enough that the side-by-side bench became uninteresting.
#             Git history + llmwiki/concepts/workspace-mount-protocols.md
#             have the full transition details if you need to reconstruct it.

# ── NFSv4 mount at ~/Workspace/remote ───────────────────────────────────────
#
# Mounts ms02:/home/casibbald/Workspace at ~/Workspace/remote on the Mac.
# Superseded SSHFS at the same path on 2026-04-20.
#
# Layout the operator lives in:
#   ~/Workspace/local/        — Mac-native clones (this repo lives here)
#   ~/Workspace/remote/       — NFSv4 view of ms02:~/Workspace
#
# Cursor opens ~/Workspace as the workspace root, so both `local/` and
# `remote/` sit side by side in the file tree. Use local/ for Mac-side
# edits (small scripts, infra, anything fast); use remote/ for browsing
# files that are built/run on ms02 via Cursor Remote-SSH or `just ms02-*`
# helpers. Don't build against the mount — run builds on ms02.
#
# Pre-wired-LAN phase: NFS traffic rides the SSH ControlMaster tunnel via
# `LocalForward 2049` — see ~/.ssh/config.d/ms02-dev-tunnel. So `nfs-up`
# requires `just dev-tunnel-up` first. Once the USB 2.5GbE adapter lands
# and Starlink Wi-Fi↔LAN filtering is out of the way, the source can flip
# from `127.0.0.1:...` to `ms02:...` with no other client-side change
# (server export already covers both 127.0.0.1/32 and 192.168.1.0/24).
#
# Server-side prerequisite: `ansible-playbook playbooks/nfs_server.yml -l ms02`
# Mac-side prerequisite:    `ansible-playbook playbooks/mac_workstation.yml --ask-become-pass`
#
# See: llmwiki/concepts/workspace-mount-protocols.md

# Mount NFSv4 at ~/Workspace/remote over the SSH LocalForward:2049 tunnel.
nfs-up:
    #!/usr/bin/env bash
    set -euo pipefail
    mount_pt="$HOME/Workspace/remote"
    # Source matches the export on ms02 (see inventory/host_vars/ms02.yml).
    # Using 127.0.0.1 so the SSH LocalForward:2049 tunnel carries the traffic.
    source="127.0.0.1:/home/casibbald/Workspace"
    if mount | grep -q "on $mount_pt "; then
        echo "✓ already mounted at $mount_pt"
        exit 0
    fi
    if [ ! -d "$mount_pt" ]; then
        echo "✗ $mount_pt does not exist — run:  just mac-provision"
        exit 1
    fi
    # Tunnel check: NFS needs port 2049 on loopback, which the ControlMaster
    # tunnel provides via LocalForward. If the forward isn't up, fail loudly
    # rather than produce an opaque NFS timeout.
    if ! nc -z -G 2 127.0.0.1 2049 >/dev/null 2>&1; then
        echo "✗ 127.0.0.1:2049 not reachable — SSH LocalForward:2049 is down"
        echo "  Run:  just dev-tunnel-up"
        echo "  And confirm ~/.ssh/config.d/ms02-dev-tunnel has 'LocalForward 2049 localhost:2049'"
        exit 1
    fi
    # NFSv4 client options (macOS mount_nfs):
    #   vers=4                 — force v4 (no v3 fallback to portmapper/mountd)
    #   rsize/wsize=1048576    — 1 MiB; NFSv4 negotiates down if server caps
    #   hard                   — block on server down rather than fail I/O with EIO
    #   noresvport             — use an unprivileged source port; the SSH
    #                            LocalForward rewrites it anyway, and the
    #                            127.0.0.1/32 export has `insecure` to accept
    #                            non-privileged source ports
    #   nfc                    — normalize filenames to NFC (Unicode) so
    #                            Mac-created names don't break on ext4
    # Deliberately NOT set (the "why" matters for future-us):
    #   nolocks + locallocks   — these two conflict, passing both returns
    #                            EINVAL from mount_nfs; NFSv4 uses in-protocol
    #                            locking so specifying either is unnecessary
    #   rdirplus               — Linux-only option, rejected by macOS mount_nfs
    #   intr                   — Linux-only, rejected by macOS mount_nfs
    #   bg                     — backgrounding on EINVAL leaves zombie retries
    #                            fighting with new mount attempts; fail-fast
    #                            is much easier to debug
    echo "mounting $source → $mount_pt (via SSH LocalForward:2049)"
    sudo mount_nfs \
        -o vers=4,rsize=1048576,wsize=1048576,hard,noresvport,nfc \
        "$source" "$mount_pt"
    echo "✓ mounted $mount_pt"
    echo "inspect:  just nfs-status"

# Unmount the NFS share.
nfs-down:
    #!/usr/bin/env bash
    set -euo pipefail
    mount_pt="$HOME/Workspace/remote"
    # Loop umount to clean up any stacked mounts (can happen if an earlier
    # mount attempt used `bg` and retried in the background while we
    # re-issued the mount command manually).
    while mount | grep -q "on $mount_pt "; do
        sudo umount "$mount_pt" 2>/dev/null || diskutil unmount force "$mount_pt"
    done
    echo "✓ unmounted $mount_pt"

# Show mount status + quick readability check.
nfs-status:
    #!/usr/bin/env bash
    set -u
    mount_pt="$HOME/Workspace/remote"
    if ! mount | grep -q "on $mount_pt "; then
        echo "✗ NOT mounted"
        exit 1
    fi
    # Count mount entries — should be 1. >1 means a stacked mount (fix via nfs-reconnect).
    count=$(mount | grep -c "on $mount_pt ")
    if [ "$count" -gt 1 ]; then
        echo "⚠ $count mounts stacked at $mount_pt — run:  just nfs-reconnect"
    else
        echo "✓ mounted $mount_pt"
    fi
    mount | grep "on $mount_pt " | sed 's/^/   /'
    echo
    echo "Top-level entries (first 15):"
    ls "$mount_pt" 2>/dev/null | head -15

# NFS metadata throughput smoke test. Run after mounting to confirm the mount
# is reasonable (dir walk should complete in seconds over SSH-tunneled NFS,
# minutes would indicate a problem — check `just dev-tunnel-status` first).
#
# Deliberately NOT `set -o pipefail`: the `find | head -N` pipeline closes
# early (head exits at N lines), which sends SIGPIPE upstream to find and
# returns 141. pipefail would propagate that as a recipe failure, but the
# timings above SIGPIPE are what we actually care about.
nfs-bench:
    #!/usr/bin/env bash
    set -u
    nfs_mount="$HOME/Workspace/remote"
    if ! mount | grep -q "on $nfs_mount "; then
        echo "✗ $nfs_mount is not mounted — run 'just nfs-up' first"
        exit 1
    fi
    echo "=== dir walk (depth 3) ==="
    time (find "$nfs_mount" -maxdepth 3 -type d 2>/dev/null | wc -l)
    echo
    # Collect filenames first, THEN stat — decouples metadata collection
    # from head-driven pipe closure so the timing reflects only the stat
    # phase (no SIGPIPE noise in the exit code).
    echo "=== stat storm (first 500 regular files) ==="
    files=$(find "$nfs_mount" -type f 2>/dev/null | head -500 || true)
    time (echo "$files" | xargs stat -f "%N" >/dev/null 2>&1)
    echo
    echo "=== sequential read (largest file in root, capped at 64 MiB) ==="
    target=$(find "$nfs_mount" -maxdepth 2 -type f -size +1M 2>/dev/null | head -1 || true)
    if [ -n "$target" ]; then
        echo "reading: $target"
        time dd if="$target" of=/dev/null bs=1m count=64 2>&1 | tail -2
    else
        echo "(no file >1 MiB found within depth 2 — skipping)"
    fi

# Reconnect cycle for the NFS mount. Use after sleep/wake or a network blip
# that left the mount in a stale state, or to clean up stacked mounts.
nfs-reconnect: nfs-down nfs-up

# ── Mac-side Ansible provisioning ───────────────────────────────────────────
#
# Runs the local-connection mac_workstation playbook. Ensures NFS mount point
# exists, Spotlight exclusions are in place, and /etc/nfs.conf has the NFSv4
# idmap domain set so file ownership maps correctly (Mac UID 501 ↔ ms02 UID
# 1000, resolved as `casibbald@microscaler.lan` on the wire).
#
# Uses --ask-become-pass because a handful of tasks need sudo (mdutil -X,
# lineinfile against /etc/nfs.conf). Expected sudo prompts: at most once per
# run; cached creds cover the rest.

# Provision the Mac (this machine). Safe to re-run.
mac-provision:
    ansible-playbook playbooks/mac_workstation.yml --ask-become-pass

# Dry-run the Mac provisioning — see what would change without touching disk.
mac-provision-check:
    ansible-playbook playbooks/mac_workstation.yml --check --diff --ask-become-pass

# Apply ONLY the sudoers NOPASSWD drop-in. Handy on first bootstrap — run this
# once, enter your password, and thereafter every mount_nfs/umount/mdutil/
# tmutil/killall-mds invocation from this justfile is silent. The full
# mac-provision playbook also installs this (it runs mac_sudoers first),
# but this recipe exists for fast iteration on the sudoers rule list.
mac-sudoers:
    ansible-playbook playbooks/mac_workstation.yml --tags sudoers --ask-become-pass

# Dry-run just the sudoers rules. Shows the diff vs what's currently in
# /etc/sudoers.d/cylon-local-infra-ops (or confirms it would be created).
mac-sudoers-check:
    ansible-playbook playbooks/mac_workstation.yml --tags sudoers --check --diff --ask-become-pass

# Provision the NFS server on ms02. Safe to re-run.
nfs-server-provision:
    ansible-playbook playbooks/nfs_server.yml -l ms02

# Dry-run the NFS server provisioning.
nfs-server-check:
    ansible-playbook playbooks/nfs_server.yml -l ms02 --check --diff

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
        echo "✗ $local_mount does not exist — mount it first (just nfs-up)"
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
