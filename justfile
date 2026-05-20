# cylon-local-infra — operator command surface
#
# ms02 dev access is **direct LAN**: Hermes, Tilt, NFSv4, kind API (e.g. :38839),
# observability UIs, etc. `inventory/host_vars/ms02.yml` opens the needed TCP
# ports to `192.168.1.0/24` — no SSH LocalForward / SOCKS tunnel in this repo.
#
# See:
#   - docs/dev_hosts.md
#   - llmwiki/concepts/starlink-wifi-lan-port-filter.md (historical)
#   - DGX Spark: `just spark-provision` (canonical — full reconcile + assert)

set shell := ["bash", "-uc"]

# ms02 on the home LAN (matches `Host ms02` in ~/.ssh/config when HostName is this IP).
ms02_lan_ip := "192.168.1.189"
# NFS lands on ~/Workspace/remote (root:wheel stub from mac_workstation --tags dirs).
nfs_mac_real_mount := env_var("HOME") + "/Workspace/remote"
# Ephemeral mount for `just nfs-troubleshoot` (outside $HOME, root:wheel).
nfs_mac_probe_mount := "/private/tmp/cylon-nfs-ms02-probe"

default:
    @just --list

# ── ms02 LAN reachability (HTTP probes; no SSH tunnel) ─────────────────────

# HTTP-probe common ms02 services on the LAN. Green (2xx/3xx) = reachable; '000' = blocked or down.
ms02-lan-check:
    #!/usr/bin/env bash
    set -u
    B="{{ms02_lan_ip}}"
    probe() {
        local port="$1" label="$2" code
        code=$(curl -s -o /dev/null -w "%{http_code}" -m 3 "http://${B}:${port}/" 2>/dev/null || true)
        [[ -z "$code" ]] && code="000"
        printf "  %-5s  %-24s  http=%s  (http://${B}:${port}/)\n" "$port" "$label" "$code"
    }
    echo "Probing ms02 on LAN (${B}, 3s timeout each):"
    probe 9119  "Hermes Web UI"
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
    echo
    echo "Kubernetes API (kind host map; use this URL in kubeconfig server:): https://${B}:38839/"
    echo "See docs/dev_hosts.md if your config still points at https://127.0.0.1:38839."

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
# Mounts ms02:/home/casibbald/Workspace at ~/Workspace/remote (Ansible: root:wheel stub).
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
# NFSv4 is mounted **directly** to ms02 on the LAN (`{{ms02_lan_ip}}:2049`).
# Keep `playbooks/nfs_server.yml` applied so exports + `insecure` match macOS.
#
# Server-side prerequisite: `ansible-playbook playbooks/nfs_server.yml -l ms02`
# Mac-side prerequisite:    `ansible-playbook playbooks/mac_workstation.yml --ask-become-pass`
#
# See: llmwiki/concepts/workspace-mount-protocols.md

# Mount NFSv4 at ~/Workspace/remote (real_pt = nfs_mac_real_mount).
nfs-up:
    #!/usr/bin/env bash
    set -euo pipefail
    real_pt="{{nfs_mac_real_mount}}"
    # Source matches the export on ms02 (see inventory/host_vars/ms02.yml).
    ms02_ip="{{ms02_lan_ip}}"
    source="${ms02_ip}:/home/casibbald/Workspace"
    if mount | grep -q "on $real_pt "; then
        echo "✓ already mounted at $real_pt"
        exit 0
    fi
    if [[ -L "$real_pt" ]]; then
        echo "✗ $real_pt is still a symlink (old Shared layout). Run:"
        echo "  just nfs-down && ansible-playbook playbooks/mac_workstation.yml --tags dirs --ask-become-pass"
        exit 1
    fi
    if [ ! -d "$real_pt" ]; then
        echo "✗ $real_pt does not exist — run:  ansible-playbook playbooks/mac_workstation.yml --tags dirs --ask-become-pass"
        exit 1
    fi
    if ! nc -z -G 2 "$ms02_ip" 2049 >/dev/null 2>&1; then
        echo "✗ ${ms02_ip}:2049 not reachable — is nfs-kernel-server up on ms02?"
        echo "  Server: ansible-playbook playbooks/nfs_server.yml -l ms02"
        echo "  Client firewall on ms02 must allow 2049/tcp from 192.168.1.0/24 (host_vars/ms02.yml)."
        exit 1
    fi
    # Mount stub must be root:wheel (see mac_workstation role).
    mp_og=$(stat -f '%Su:%Sg' "$real_pt" 2>/dev/null || echo '')
    if [[ "$mp_og" != "root:wheel" ]]; then
        echo "✗ $real_pt must be root:wheel (got ${mp_og:-missing})"
        echo "  ansible-playbook playbooks/mac_workstation.yml --tags dirs --ask-become-pass"
        echo "  see:  just nfs-doctor"
        exit 1
    fi
    # NFSv4 client options (macOS mount_nfs):
    #   vers=4                 — force v4 (no v3 fallback to portmapper/mountd)
    #   rsize/wsize=1048576    — 1 MiB; NFSv4 negotiates down if server caps
    #   hard                   — block on server down rather than fail I/O with EIO
    #   noresvport             — macOS default; server export needs `insecure`
    #                            for LAN clients (see host_vars/ms02.yml)
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
    # macOS 15+ may stamp stub files with com.apple.provenance; some Tahoe builds
    # have been finicky about mounting over xattr-heavy trees — clear before mount.
    sudo xattr -cr "$real_pt" 2>/dev/null || true
    echo "mounting $source → $real_pt (NFSv4, trying noresvport first)"
    set +e
    sudo mount_nfs \
        -o vers=4,rsize=1048576,wsize=1048576,hard,noresvport,nfc \
        "$source" "$real_pt"
    mount_rc=$?
    set -e
    if [[ "$mount_rc" -ne 0 ]]; then
        echo
        echo "noresvport failed (exit $mount_rc) — retrying with resvport (common on some macOS builds)..."
        set +e
        sudo mount_nfs \
            -o vers=4,rsize=1048576,wsize=1048576,hard,resvport,nfc \
            "$source" "$real_pt"
        mount_rc=$?
        set -e
    fi
    if [[ "$mount_rc" -ne 0 ]]; then
        echo
        echo "✗ mount_nfs failed (exit $mount_rc). If you see 'Operation not permitted' on macOS:"
        echo "  1. System Settings → Privacy & Security → Full Disk Access → enable the app"
        echo "     that runs this shell (Terminal.app, iTerm, or Cursor — restart the app after)."
        echo "  2. Probe options + /private/tmp mount:  just nfs-troubleshoot"
        echo "  Server export: 'insecure' for noresvport clients (inventory/host_vars/ms02.yml)."
        echo "  Manual resvport only:  just nfs-up-resvport"
        exit "$mount_rc"
    fi
    echo "✓ mounted $real_pt"
    echo "inspect:  just nfs-status"

# Quick client-side checks when `just nfs-up` fails (EPERM, etc.).
nfs-doctor:
    #!/usr/bin/env bash
    set -uo pipefail
    real_pt="{{nfs_mac_real_mount}}"
    ms02_ip="{{ms02_lan_ip}}"
    echo "NFS client diagnostics"
    echo "  mount stub:   $real_pt"
    if [[ -L "$real_pt" ]]; then
        echo "  layout:       symlink → $(readlink "$real_pt") (run dirs tag after nfs-down to remove old layout)"
    elif [[ -d "$real_pt" ]]; then
        echo "  layout:       directory (expected)"
    elif [[ -e "$real_pt" ]]; then
        echo "  layout:       exists but is not a directory — fix manually"
    else
        echo "  layout:       missing — run mac_workstation --tags dirs"
    fi
    if [[ -e "$real_pt" ]]; then
        stat -f "  owner:group  %Su:%Sg  (expect root:wheel)" "$real_pt"
        stat -f "  mode         %OLp" "$real_pt"
    else
        echo "  ✗ $real_pt missing (mac_workstation has not created it on this Mac yet)"
    fi
    echo "  ${ms02_ip}:2049  $(nc -z -G 2 "$ms02_ip" 2049 >/dev/null && echo reachable || echo not reachable)"
    if command -v sudo >/dev/null; then
        if sudo -n true 2>/dev/null; then
            echo "  sudo:         non-interactive ok (cached NOPASSWD or similar)"
        else
            echo "  sudo:         may prompt (normal if NOPASSWD not installed yet)"
        fi
    fi
    echo
    if [[ ! -d "$real_pt" ]] || [[ -L "$real_pt" ]]; then
        echo "── Next step ─────────────────────────────────────────────────────────"
        echo "  just nfs-down   # if a stale /Users/Shared/... mount exists"
        echo "  ansible-playbook playbooks/mac_workstation.yml --tags dirs --ask-become-pass"
        echo "  just nfs-up"
        echo
    fi
    echo "If mount_nfs still says Operation not permitted:  just nfs-troubleshoot"
    echo "  macOS: System Settings → Privacy & Security → Full Disk Access → enable for your terminal app."

# Deep NFS client diagnostics: probe mount with noresvport vs resvport vs mount(8).
# Run on picolino when `just nfs-up` returns EPERM. Does not modify the real mount path
# except create/remove the probe directory under /private/tmp.
nfs-troubleshoot:
    #!/usr/bin/env bash
    set -uo pipefail
    real_pt="{{nfs_mac_real_mount}}"
    probe="{{nfs_mac_probe_mount}}"
    ms02_ip="{{ms02_lan_ip}}"
    source="${ms02_ip}:/home/casibbald/Workspace"
    echo "=== macOS ==="
    sw_vers 2>/dev/null || true
    uname -a
    echo
    echo "=== Layout (mount stub: $real_pt) ==="
    just nfs-doctor || true
    echo
    echo "=== Mount point xattrs (real path) ==="
    if [[ -e "$real_pt" ]]; then
        ls -le@ "$real_pt" 2>/dev/null | head -8 || true
        echo "  (xattr -l on README if present:)"
        xattr -l "$real_pt/README.md" 2>/dev/null | head -5 || true
    fi
    echo
    echo "=== Prepare probe dir: $probe ==="
    if mount | grep -q "on $probe "; then
        echo "unmounting stale probe..."
        sudo umount "$probe" 2>/dev/null || true
    fi
    sudo mkdir -p "$probe"
    sudo chown root:wheel "$probe"
    sudo chmod 755 "$probe"
    sudo xattr -cr "$probe" 2>/dev/null || true
    try_mount() {
        local label="$1" rc
        shift
        echo
        echo "── $label ──"
        "$@"
        rc=$?
        if [[ "$rc" -eq 0 ]]; then
            echo "  ✓ mount ok"
            ls "$probe" 2>/dev/null | head -8 || true
            sudo umount "$probe" 2>/dev/null || diskutil unmount force "$probe" 2>/dev/null || true
            return 0
        fi
        echo "  ✗ mount failed (exit $rc)"
        sudo umount "$probe" 2>/dev/null || true
        return 1
    }
    ok=0
    set +e
    try_mount "A: mount_nfs noresvport+nfc (matches just nfs-up)" \
        sudo mount_nfs -o vers=4,rsize=1048576,wsize=1048576,hard,noresvport,nfc "$source" "$probe" \
        && ok=1
    try_mount "B: mount_nfs resvport+nfc (nixCraft-style client port)" \
        sudo mount_nfs -o vers=4,rsize=1048576,wsize=1048576,hard,resvport,nfc "$source" "$probe" \
        && ok=1
    try_mount "C: mount_nfs minimal (vers=4,hard only)" \
        sudo mount_nfs -o vers=4,hard "$source" "$probe" \
        && ok=1
    try_mount "D: mount -t nfs noresvport (alternate front-end)" \
        sudo mount -t nfs -o vers=4,rsize=1048576,wsize=1048576,hard,noresvport,nfc "$source" "$probe" \
        && ok=1
    set -e
    while mount | grep -q "on $probe "; do
        sudo umount "$probe" 2>/dev/null || diskutil unmount force "$probe" 2>/dev/null || break
    done
    echo
    echo "=== Interpretation ==="
    if [[ "$ok" -eq 1 ]]; then
        echo "At least one probe mount succeeded. If only B worked, try:  just nfs-up-resvport"
        echo "If A works here but not on $real_pt, compare xattrs/quarantine on both paths."
    else
        echo "All probe mounts failed — likely macOS policy (Full Disk Access for Terminal),"
        echo "or server/export/firewall (on ms02: sudo exportfs -v; ss -lntp | grep 2049)."
    fi
    echo "Server check (run on ms02):  sudo exportfs -v | grep -F Workspace"

# Same as nfs-up but use resvport instead of noresvport (try when nixCraft EPERM workaround applies).
nfs-up-resvport:
    #!/usr/bin/env bash
    set -euo pipefail
    real_pt="{{nfs_mac_real_mount}}"
    ms02_ip="{{ms02_lan_ip}}"
    source="${ms02_ip}:/home/casibbald/Workspace"
    if mount | grep -q "on $real_pt "; then
        echo "✓ already mounted at $real_pt"
        exit 0
    fi
    [[ -L "$real_pt" ]] && { echo "✗ $real_pt is a symlink — run dirs tag after nfs-down"; exit 1; }
    [[ -d "$real_pt" ]] || { echo "✗ missing $real_pt"; exit 1; }
    nc -z -G 2 "$ms02_ip" 2049 >/dev/null || { echo "✗ ${ms02_ip}:2049 closed"; exit 1; }
    [[ "$(stat -f '%Su:%Sg' "$real_pt")" == "root:wheel" ]] || { echo "✗ $real_pt not root:wheel"; exit 1; }
    sudo xattr -cr "$real_pt" 2>/dev/null || true
    echo "mounting $source → $real_pt (resvport — if this works, prefer over noresvport on this Mac)"
    set +e
    sudo mount_nfs -o vers=4,rsize=1048576,wsize=1048576,hard,resvport,nfc "$source" "$real_pt"
    mount_rc=$?
    set -e
    if [[ "$mount_rc" -ne 0 ]]; then
        echo
        echo "✗ mount_nfs failed (exit $mount_rc). Same fixes as 'just nfs-up' (Full Disk Access for this terminal app, then retry)."
        echo "  just nfs-troubleshoot"
        exit "$mount_rc"
    fi
    echo "✓ mounted"

# Unmount the NFS share.
nfs-down:
    #!/usr/bin/env bash
    set -euo pipefail
    real_pt="{{nfs_mac_real_mount}}"
    legacy_shared="/Users/Shared/cylon-ms02-workspace"
    probe_pt="{{nfs_mac_probe_mount}}"
    # Loop umount to clean up any stacked mounts (can happen if an earlier
    # mount attempt used `bg` and retried in the background while we
    # re-issued the mount command manually).
    for mount_pt in "$real_pt" "$legacy_shared" "$probe_pt"; do
        while mount | grep -q "on $mount_pt "; do
            sudo umount "$mount_pt" 2>/dev/null || diskutil unmount force "$mount_pt"
        done
    done
    echo "✓ unmounted (checked $real_pt, $legacy_shared, $probe_pt)"

# Show mount status + quick readability check.
nfs-status:
    #!/usr/bin/env bash
    set -u
    real_pt="{{nfs_mac_real_mount}}"
    if ! mount | grep -q "on $real_pt "; then
        echo "✗ NOT mounted at $real_pt"
        exit 1
    fi
    # Count mount entries — should be 1. >1 means a stacked mount (fix via nfs-reconnect).
    count=$(mount | grep -c "on $real_pt ")
    if [ "$count" -gt 1 ]; then
        echo "⚠ $count mounts stacked at $real_pt — run:  just nfs-reconnect"
    else
        echo "✓ mounted $real_pt"
    fi
    mount | grep "on $real_pt " | sed 's/^/   /'
    echo
    echo "Top-level entries (first 15):"
    ls "$real_pt" 2>/dev/null | head -15

# NFS metadata throughput smoke test. Run after mounting to confirm the mount
# is reasonable (dir walk should complete in seconds on LAN NFS;
# minutes would indicate a problem — check `mount` + `just nfs-status`).
#
# Deliberately NOT `set -o pipefail`: the `find | head -N` pipeline closes
# early (head exits at N lines), which sends SIGPIPE upstream to find and
# returns 141. pipefail would propagate that as a recipe failure, but the
# timings above SIGPIPE are what we actually care about.
nfs-bench:
    #!/usr/bin/env bash
    set -u
    real_pt="{{nfs_mac_real_mount}}"
    if ! mount | grep -q "on $real_pt "; then
        echo "✗ $real_pt is not mounted — run 'just nfs-up' first"
        exit 1
    fi
    echo "=== dir walk (depth 3) ==="
    time (find "$real_pt" -maxdepth 3 -type d 2>/dev/null | wc -l)
    echo
    # Collect filenames first, THEN stat — decouples metadata collection
    # from head-driven pipe closure so the timing reflects only the stat
    # phase (no SIGPIPE noise in the exit code).
    echo "=== stat storm (first 500 regular files) ==="
    files=$(find "$real_pt" -type f 2>/dev/null | head -500 || true)
    time (echo "$files" | xargs stat -f "%N" >/dev/null 2>&1)
    echo
    echo "=== sequential read (largest file in root, capped at 64 MiB) ==="
    target=$(find "$real_pt" -maxdepth 2 -type f -size +1M 2>/dev/null | head -1 || true)
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

# ── DGX Spark — ONE canonical provision path ──────────────────────────────────
#
# Daily driver (only these two — everything else is runtime ops or escape hatches):
#
#   just spark-provision              # playbooks/provision_sparks.yml end-to-end + spark_assert
#   just spark-provision-recreate     # same + force vLLM container recreate (Ray or torchrun)
#
# Escape hatch (tags/extra-vars after `--`):
#   just spark-provision -- --skip-tags apt
#   just spark-provision -- -e spark_provision_assert=false   # reconcile without assert gate
#
# Partial tag runs MUST include spark_assert for the phases you touch, e.g.:
#   just spark-provision -- --tags hf_prefetch,spark_assert
#
# Retired: spark-vllm-provision, cutover_roce.yml, refresh_hf_prefetch.yml — use spark-provision*.
#
# Prereqs: SSH to nvidia1/nvidia2; QSFP interconnect up.
# Leader API: export SPARK_VLLM_API=http://192.168.1.104:8000

spark-vllm-api := env_var_or_default('SPARK_VLLM_API', 'http://192.168.1.104:8000')

# Canonical — full end-to-end reconcile + state assert.
spark-provision *extra:
    #!/usr/bin/env bash
    set -euo pipefail
    cd '{{ justfile_directory() }}'
    ansible-playbook playbooks/provision_sparks.yml -l sparks ${extra[@]+"${extra[@]}"}

# Full reconcile + recreate vLLM containers for the active stack only (vllm_stack_kind in inventory).
spark-provision-recreate:
    #!/usr/bin/env bash
    set -euo pipefail
    cd '{{ justfile_directory() }}'
    stack="$(awk '/^vllm_stack_kind:/ {print $2; exit}' inventory/group_vars/sparks.yml | tr -d ' \"')"
    stack="${stack:-ray}"
    extra=()
    if [[ "$stack" == "torchrun" ]]; then
      extra=(-e vllm_torchrun_stacked_recreate=true)
      echo ">>> vllm_stack_kind=torchrun — recreating torchrun only (Ray will be torn down)"
    else
      extra=(-e vllm_stacked_container_recreate=true)
      echo ">>> vllm_stack_kind=ray — recreating Ray only (torchrun will be torn down)"
    fi
    ansible-playbook playbooks/provision_sparks.yml -l sparks "${extra[@]}"

# Dry-run full provision (check mode).
spark-provision-check:
    #!/usr/bin/env bash
    set -euo pipefail
    cd '{{ justfile_directory() }}'
    ansible-playbook playbooks/provision_sparks.yml -l sparks --check --diff

# ── Spark model / Hermes workflows (call canonical provision internally) ─────
#   just spark-model-status
#   just spark-model-status --json
#   just spark-model-status ansible-prefetch
#   just spark-model-status prefetch-once --ssh-host nvidia1
#   just spark-model-status ansible-vllm --recreate
#   just spark-model-cutover --recreate
#   just spark-stack-observe
spark-model-status *args:
    #!/usr/bin/env bash
    set -euo pipefail
    cd '{{ justfile_directory() }}'
    python3 scripts/spark_model_status.py "$@"

# Full local workflow: inventory edit → spark_model_status cutover → full provision + assert.
# Equivalent to: just spark-provision-recreate (after HF prefetch steps in cutover).
# Edit inventory/group_vars/sparks.yml first (hf_prefetch_models, vllm_default_model).
spark-model-cutover *args:
    #!/usr/bin/env bash
    set -euo pipefail
    cd '{{ justfile_directory() }}'
    python3 scripts/spark_model_status.py cutover "$@"

# Patch Hermes agent .env on ms02 (inventory hermes_* vars). See playbooks/sync_hermes_ms02.yml.
spark-hermes-sync *args:
    #!/usr/bin/env bash
    set -euo pipefail
    cd '{{ justfile_directory() }}'
    python3 scripts/spark_model_status.py sync-hermes "$@"

# SSH to leader: docker, ports, ray status, vllm-serve.log, /v1/models, /metrics (see docs/provision_sparks.md).
spark-stack-observe *args:
    #!/usr/bin/env bash
    set -euo pipefail
    cd '{{ justfile_directory() }}'
    python3 scripts/spark_model_status.py observe "$@"

spark-model-status-test:
    #!/usr/bin/env bash
    set -euo pipefail
    cd '{{ justfile_directory() }}/scripts'
    python3 -m unittest test_spark_model_status.py -v

# Model cutover: edit sparks.yml → `just spark-model-cutover --recreate` (full provision + assert).
# Equivalent to: spark-provision-recreate after hf prefetch steps.

# ── Spark runtime ops (not provision — read-only / recovery) ────────────────

spark-vllm-torchrun-ps:
    #!/usr/bin/env bash
    set -euo pipefail
    cd '{{ justfile_directory() }}'
    ansible sparks -m shell -a 'docker ps -a --filter name=vllm-ngc-torchrun'

spark-vllm-torchrun-status:
    #!/usr/bin/env bash
    set -euo pipefail
    cd '{{ justfile_directory() }}'
    echo "=== curl :8000/v1/models on leader (torchrun rank 0) ==="
    ansible nvidia1 -m shell -a \
      'curl -fsS --max-time 15 http://127.0.0.1:8000/v1/models | head -c 2000'

# Leader `/v1/models` via Ansible SSH (Mac does not need to be on the Sparks' LAN).
spark-vllm-status:
    #!/usr/bin/env bash
    set -euo pipefail
    cd '{{ justfile_directory() }}'
    echo "=== curl http://127.0.0.1:8000/v1/models inside leader container host ==="
    ansible nvidia1 -m shell -a \
      'curl -fsS --max-time 15 http://127.0.0.1:8000/v1/models | head -c 2000'

# Quick probe from *this machine* to the leader LAN IP (handy when your laptop
# is on the same Ethernet/Wi-Fi as the Sparks). Uses SPARK_VLLM_API.
spark-vllm-lan-probe:
    #!/usr/bin/env bash
    set -euo pipefail
    base='{{ spark-vllm-api }}'
    echo "GET ${base%/}/v1/models"
    curl -fsS --max-time 15 "${base%/}/v1/models" | head -c 2000
    echo

# Print + open the Ray dashboard URL (LAN). Default is nvidia1:8265; override
# with SPARK_RAY_DASHBOARD or SPARK_LEADER_LAN_IP. Requires the head container
# to have been (re)created with `--dashboard-host=0.0.0.0` (set in
# inventory/group_vars/sparks.yml + role defaults).
spark-vllm-dashboard:
    #!/usr/bin/env bash
    set -euo pipefail
    : "${SPARK_LEADER_LAN_IP:=192.168.1.104}"
    : "${SPARK_RAY_DASHBOARD:=http://${SPARK_LEADER_LAN_IP}:8265}"
    echo "Ray dashboard: ${SPARK_RAY_DASHBOARD}"
    if command -v open >/dev/null; then
      open "${SPARK_RAY_DASHBOARD}"
    elif command -v xdg-open >/dev/null; then
      xdg-open "${SPARK_RAY_DASHBOARD}"
    fi

# Show NGC Ray container state on both Sparks (head + worker, any state).
spark-vllm-ps:
    #!/usr/bin/env bash
    set -euo pipefail
    cd '{{ justfile_directory() }}'
    ansible nvidia1 -m shell -a 'docker ps -a --filter name=vllm-ngc-ray-head'
    ansible nvidia2 -m shell -a 'docker ps -a --filter name=vllm-ngc-ray-worker'

# Start the Ray *head* container on nvidia1. Idempotent: `docker start` is a
# no-op if already running. Only valid when inventory vllm_stack_kind=ray —
# use `just spark-provision` for torchrun.
spark-vllm-head-start:
    #!/usr/bin/env bash
    set -euo pipefail
    cd '{{ justfile_directory() }}'
    stack="$(awk '/^vllm_stack_kind:/ {print $2; exit}' inventory/group_vars/sparks.yml | tr -d ' \"')"
    if [[ "${stack:-ray}" == "torchrun" ]]; then
      echo "✗ inventory vllm_stack_kind=torchrun — use just spark-provision, not spark-vllm-head-start"
      exit 1
    fi
    ansible nvidia1 -m shell -a \
      'docker start vllm-ngc-ray-head || true; docker ps -a --filter name=vllm-ngc-ray-head'

# Restart the Ray *head* container on nvidia1 (graceful stop + start).
spark-vllm-head-restart:
    #!/usr/bin/env bash
    set -euo pipefail
    cd '{{ justfile_directory() }}'
    ansible nvidia1 -m shell -a \
      'docker restart vllm-ngc-ray-head; docker ps -a --filter name=vllm-ngc-ray-head'

# Start the Ray *worker* container on nvidia2 only — recovery after a power loss
# left `vllm-ngc-ray-worker-nvidia2` in `Exited` state (safe to re-run).
spark-vllm-worker-start:
    #!/usr/bin/env bash
    set -euo pipefail
    cd '{{ justfile_directory() }}'
    ansible nvidia2 -m shell -a \
      'docker start vllm-ngc-ray-worker-nvidia2 || true; docker ps -a --filter name=vllm-ngc-ray-worker-nvidia2'

# Restart the Ray *worker* container on nvidia2.
spark-vllm-worker-restart:
    #!/usr/bin/env bash
    set -euo pipefail
    cd '{{ justfile_directory() }}'
    ansible nvidia2 -m shell -a \
      'docker restart vllm-ngc-ray-worker-nvidia2; docker ps -a --filter name=vllm-ngc-ray-worker-nvidia2'

# Start the whole NGC stack: head first (nvidia1), then worker (nvidia2). Use
# after `just spark-vllm-stop` or a reboot. For a fresh provision (image pull,
# env diff, etc.) use `just spark-provision` or `just spark-provision-recreate`.
spark-vllm-start: spark-vllm-head-start spark-vllm-worker-start

# Restart head + worker. Does NOT restart the detached `vllm serve` process —
# follow with `just spark-provision` (or a manual `docker exec ...`)
# to bring the API server back. Ray cluster state is rebuilt by the daemons.
spark-vllm-restart: spark-vllm-head-restart spark-vllm-worker-restart

# --- DGX OS / firmware lifecycle ---
#
# Both Sparks have shown abrupt power-offs (see
# llmwiki/runs/2026-04-27-ray-head-exited-postmortem.md and
# llmwiki/runs/2026-04-29-cluster-recovery-and-26.04-rollback.md).
# Planned reboots are cheaper than crash-driven ones; these recipes give us
# clean stop-reboot-start choreography.

# Show whether either Spark wants a reboot (e.g. after `apt upgrade` lands a
# package like `nvidia-spark-limits` that updates /etc/security/limits.d/*).
spark-reboot-required:
    #!/usr/bin/env bash
    set -euo pipefail
    cd '{{ justfile_directory() }}'
    ansible sparks -b -m shell -a \
      'if [ -f /var/run/reboot-required ]; then echo "REBOOT NEEDED"; cat /var/run/reboot-required.pkgs 2>/dev/null; else echo "no reboot needed"; fi'

# Apt-upgrade both Sparks (mode=safe per inventory). Idempotent. Note: the
# `spark_apt` role had a tag-propagation bug that we fixed 2026-04-29; if
# `--tags apt` ever stops running the inner tasks, fall back to:
#   ansible sparks -b -m apt -a 'update_cache=yes upgrade=safe force_apt_get=yes'
spark-apt-upgrade:
    #!/usr/bin/env bash
    set -euo pipefail
    cd '{{ justfile_directory() }}'
    ansible-playbook playbooks/provision_sparks.yml --tags apt -l sparks

# Reboot **both** Sparks at once (parallel). Stops vllm + ray containers
# cleanly first so the API doesn't observe a half-cluster, then `reboot`s
# both hosts in parallel (since with TP=2 we lose service either way — no
# meaningful "rolling" pattern). After hosts come back:
#   - Docker `--restart unless-stopped` brings the head + worker containers back
#   - `vllm serve` is a `docker exec -d` payload and does NOT survive — run
#     `just spark-vllm-api-restart` (or `just spark-provision-recreate`) to relaunch.
#
# Use this when an apt upgrade has flagged "*** System restart required ***"
# (see `just spark-reboot-required`), or before a maintenance window.
spark-reboot:
    #!/usr/bin/env bash
    set -euo pipefail
    cd '{{ justfile_directory() }}'
    echo ">>> stopping containers cleanly first"
    ansible nvidia1 -m shell -a 'docker stop vllm-ngc-ray-head 2>/dev/null || true'
    ansible nvidia2 -m shell -a 'docker stop vllm-ngc-ray-worker-nvidia2 2>/dev/null || true'
    echo ">>> rebooting both Sparks (in parallel; ssh sessions will drop)"
    ansible sparks -b -m shell -a 'shutdown -r +0 "spark-reboot via just spark-reboot"' --forks 2
    echo ">>> waiting for sparks to come back online (up to 5 min each)..."
    ansible sparks -m wait_for_connection -a 'delay=20 timeout=300'
    echo ">>> hosts back. After this finishes:"
    echo "    just spark-vllm-ps         # confirm head + worker Up"
    echo "    just spark-vllm-api-restart # relaunch vllm serve in head"

# --- vLLM autoupgrade daemon (vllm-stack-autoupgrade.service) ---
#
# Stopped on 2026-04-29 after a failed promotion to nvcr.io/nvidia/vllm:26.04-py3
# (image PATH change + role's `bash -c` not finding `ray`). Role now uses
# `bash -lc` so 26.04+ is forward-compatible — but only takes effect after
# the next `just spark-provision-recreate`. Until then, leave the daemon stopped.

# Show daemon status + state.json (current_image, candidate_tag, errors).
spark-autoupgrade-status:
    #!/usr/bin/env bash
    set -euo pipefail
    cd '{{ justfile_directory() }}'
    ansible nvidia1 -m shell -a 'sudo systemctl is-active vllm-stack-autoupgrade; echo; sudo cat /var/lib/vllm-stack-autoupgrade/state.json'

# Stop the autoupgrade daemon (graceful: no in-flight cutover).
spark-autoupgrade-disable:
    #!/usr/bin/env bash
    set -euo pipefail
    cd '{{ justfile_directory() }}'
    ansible nvidia1 -b -m systemd -a 'name=vllm-stack-autoupgrade state=stopped enabled=false'

# Re-enable the autoupgrade daemon. Only do this AFTER a fresh recreate has
# rolled the role's `bash -lc` fix into the running container's argv (so
# `docker inspect` captures the safer command), otherwise the daemon may
# replay the broken `bash -c` spec on the next image bump.
spark-autoupgrade-enable:
    #!/usr/bin/env bash
    set -euo pipefail
    cd '{{ justfile_directory() }}'
    ansible nvidia1 -b -m systemd -a 'name=vllm-stack-autoupgrade state=started enabled=true daemon_reload=true'

# --- Kernel pin (roles/spark_kernel) ---
#
# Runs the spark_kernel role only. Picks up `spark_kernel_pin` from
# host_vars/group_vars and writes GRUB_DEFAULT, apt-mark holds, and
# masks the NVIDIA OTA service as configured. Idempotent.
#
# Currently bisecting GX10 abrupt-power-off:
#   - nvidia1: pinned to 6.17.0-1008-nvidia (one HWE step back, EXPERIMENT)
#   - nvidia2: pinned to 6.17.0-1014-nvidia (current HWE, CONTROL)
#
# See:
#   - roles/spark_kernel/README.md
#   - llmwiki/runs/2026-05-01-nvidia2-abrupt-power-off-vllm-long-context.md

# Show running kernel + installed kernels + holds + OTA service state on both Sparks.
spark-kernel-status:
    #!/usr/bin/env bash
    set -euo pipefail
    cd '{{ justfile_directory() }}'
    ansible sparks -i inventory/hosts.yml -b -m shell -a '
        echo "running:    $(uname -r)"
        echo "installed:"
        dpkg-query -W -f="  \${Status}\t\${Package} \${Version}\n" "linux-image-*-nvidia" 2>/dev/null | awk -F"\t" "/ok installed/{print \$2}"
        echo "holds:"
        ( apt-mark showhold | grep -E "^(linux-image|linux-headers|linux-modules)-" | sed "s/^/  /" ) || echo "  (none)"
        echo "GRUB_DEFAULT:"
        grep "^GRUB_DEFAULT=" /etc/default/grub | sed "s/^/  /"
        echo "GRUB_TIMEOUT_STYLE / TIMEOUT:"
        grep -E "^GRUB_TIMEOUT(_STYLE)?=" /etc/default/grub | sed "s/^/  /"
        echo "nvidia-spark-run-apt-upgrade-once.service:"
        ( systemctl is-enabled nvidia-spark-run-apt-upgrade-once.service 2>&1 | sed "s/^/  enabled=/" ) || true
        ( systemctl is-active  nvidia-spark-run-apt-upgrade-once.service 2>&1 | sed "s/^/  active= /" ) || true
    '

# Apply roles/spark_kernel on both Sparks (or a specific host with `host=nvidia1`).
# Picks up `spark_kernel_pin` from inventory.
spark-kernel-apply host="sparks":
    #!/usr/bin/env bash
    set -euo pipefail
    cd '{{ justfile_directory() }}'
    ansible-playbook playbooks/provision_sparks.yml --tags kernel -l '{{ host }}'

# Dry-run the kernel role. Shows what GRUB_DEFAULT / holds / OTA mask
# would change, without writing.
spark-kernel-check host="sparks":
    #!/usr/bin/env bash
    set -euo pipefail
    cd '{{ justfile_directory() }}'
    ansible-playbook playbooks/provision_sparks.yml --tags kernel -l '{{ host }}' --check --diff

# Ad-hoc one-off pin (overrides inventory). Useful for "let me boot 6.11
# on nvidia1 just for one provision pass without editing host_vars".
# Example:
#   just spark-kernel-pin host=nvidia1 ver=6.11.0-1014-nvidia
# To unpin (release GRUB_DEFAULT change for THIS run only):
#   just spark-kernel-pin host=nvidia1 ver=
spark-kernel-pin host ver:
    #!/usr/bin/env bash
    set -euo pipefail
    cd '{{ justfile_directory() }}'
    ansible-playbook playbooks/provision_sparks.yml --tags kernel \
      -l '{{ host }}' -e 'spark_kernel_pin={{ ver }}'

# Lock host GPU graphics clocks (roles/spark_gpu_clock). Default 2000 MHz from inventory.
spark-gpu-clock-apply host="sparks":
    #!/usr/bin/env bash
    set -euo pipefail
    cd '{{ justfile_directory() }}'
    ansible-playbook playbooks/provision_sparks.yml --tags spark_gpu_clock -l '{{ host }}'

# Show current GPU clocks + lock unit state on Sparks.
spark-gpu-clock-status host="sparks":
    #!/usr/bin/env bash
    set -euo pipefail
    cd '{{ justfile_directory() }}'
    ansible '{{ host }}' -m shell -a '
      echo "=== nvidia-smi clocks ==="
      nvidia-smi -q -d CLOCK | sed -n "/GPU 0000/,/^$/p" | head -20
      echo "=== lock unit ==="
      systemctl is-enabled cylon-gpu-clock-lock.service 2>&1 || true
    ' -become

# Toggle GRUB menu visibility (5s timeout when on). Useful while bisecting
# kernels — lets you override at the console without flashing inventory.
# Examples:
#   just spark-kernel-show-menu on    # menu visible 5s
#   just spark-kernel-show-menu off   # restore hidden 0s
spark-kernel-show-menu state="on":
    #!/usr/bin/env bash
    set -euo pipefail
    cd '{{ justfile_directory() }}'
    case '{{ state }}' in
      on|true|yes|1)  v=true ;;
      off|false|no|0) v=false ;;
      *) echo "usage: just spark-kernel-show-menu on|off"; exit 2 ;;
    esac
    ansible-playbook playbooks/provision_sparks.yml --tags kernel \
      -e "spark_kernel_show_grub_menu=$v"

# --- Observability (roles/spark_observability) ---
#
# Push-based: node_exporter (incl. textfile: rasdaemon + ic_probe) + dcgm-exporter + vLLM /metrics → otel-agent → ms02:4317;
# journald → promtail → ms02:3100. See:
#   - roles/spark_observability/README.md
#   - llmwiki/concepts/sparks-observability-pipeline.md

# Show systemd units + listeners (node_exporter, dcgm, otel, promtail + two textfile timers) on Sparks.
spark-observability-status:
    #!/usr/bin/env bash
    set -euo pipefail
    cd '{{ justfile_directory() }}'
    ansible sparks -i inventory/hosts.yml -b -m shell -a '
        for u in node_exporter dcgm-exporter otel-agent promtail rasdaemon-textfile.timer ic-probe-textfile.timer; do
          state=$(systemctl is-active "$u" 2>&1)
          enabled=$(systemctl is-enabled "$u" 2>&1)
          printf "  %-30s active=%-12s enabled=%s\n" "$u" "$state" "$enabled"
        done
        echo "listeners (loopback exporters):"
        ss -tlnp 2>/dev/null | awk "/127.0.0.1:(9100|9400|9080)/ || /:8000 / { printf \"  %s\n\", \$4 }" | sort -u
    '

# Apply observability stack (included in full spark-provision; use for hotfix reruns).
spark-observability-apply host="sparks":
    #!/usr/bin/env bash
    set -euo pipefail
    cd '{{ justfile_directory() }}'
    ansible-playbook playbooks/provision_sparks.yml --tags spark_obs,spark_assert -l '{{ host }}'

# Dry-run the observability role.
spark-observability-check host="sparks":
    #!/usr/bin/env bash
    set -euo pipefail
    cd '{{ justfile_directory() }}'
    ansible-playbook playbooks/provision_sparks.yml --tags spark_obs -l '{{ host }}' --check --diff

# End-to-end probe: confirm Sparks → ms02 path is alive.
# 1. local exporter HTTP probes on each Spark
# 2. ms02 Prometheus has the `up{cluster="cylon-sparks"}` series
# 3. ms02 Loki has labels for the cluster
# Cross-check kind extraPortMappings (kind-config.yaml on ms02) against actual
# NodePort allocations in the kind cluster. Catches the silent-broken-portmap
# class of issues where ms02:<port> appears to listen (docker-proxy on 0.0.0.0)
# but actually forwards into the kind container to a NodePort that doesn't exist
# → 'connection refused' from the LAN. Bit us 2026-05-01 (otel-collector, loki,
# prometheus, grafana, jaeger were all ClusterIP and the kind extraPortMappings
# pointed nowhere). See llmwiki/concepts/sparks-observability-pipeline.md.
ms02-cluster-portmap-check:
    #!/usr/bin/env bash
    set -euo pipefail
    # Pull kind extraPortMappings + NodePort allocations from ms02, diff locally.
    portmap=$(ssh ms02 'awk "/^[[:space:]]*-[[:space:]]*containerPort:/{cp=\$NF} /^[[:space:]]*hostPort:/{print cp, \$NF}" /home/casibbald/Workspace/microscaler/shared-kind-cluster/kind-config.yaml')
    # JSON + jq for the cluster side (jsonpath's nested-array contexts are unreliable).
    nodeports=$(ssh ms02 'kubectl get svc -A -o json' \
      | jq -r '.items[] | select(.spec.type=="NodePort") | .metadata.namespace as $ns | .metadata.name as $n | .spec.ports[] | "\(.nodePort) \($ns)/\($n)"')
    printf "%-12s  %-10s  %-34s  %s\n" "kind:port" "host:port" "backing Service" "status"
    printf "%-12s  %-10s  %-34s  %s\n" "----------" "---------" "----------------------------------" "------"
    ok=0; missing=0
    while read -r cp hp; do
      [ -z "${cp:-}" ] && continue
      backing=$(awk -v p="$cp" '$1==p {print $2; exit}' <<< "$nodeports")
      if [ -z "$backing" ]; then
        printf "%-12s  %-10s  %-34s  MISSING — ms02:%s will refuse\n" "$cp" "$hp" "(none)" "$hp"
        missing=$((missing+1))
      else
        printf "%-12s  %-10s  %-34s  OK\n" "$cp" "$hp" "$backing"
        ok=$((ok+1))
      fi
    done <<< "$portmap"
    echo
    printf "Summary: %d OK  %d MISSING\n" "$ok" "$missing"
    if [ "$missing" -gt 0 ]; then
      echo
      echo "Each MISSING row = kind extraPortMapping with no backing NodePort Service."
      echo "Fix in shared-kind-cluster/k8s/<ns>/<svc>.yaml by setting:"
      echo "  spec:"
      echo "    type: NodePort"
      echo "    ports:"
      echo "      - port: <containerPort>"
      echo "        nodePort: <containerPort>     # must match"
      exit 1
    fi

# Probe the spark observability pipeline end-to-end.
spark-observability-probe:
    #!/usr/bin/env bash
    set -euo pipefail
    cd '{{ justfile_directory() }}'
    echo ">>> 1. local exporter HTTP probes on each Spark"
    ansible sparks -i inventory/hosts.yml -m shell -a '
        for tgt in 127.0.0.1:9100 127.0.0.1:9400 127.0.0.1:8000; do
          code=$(curl -s -o /dev/null -w "%{http_code}" -m 3 "http://${tgt}/metrics" || echo 000)
          printf "  %-22s http=%s\n" "$tgt/metrics" "$code"
        done
    '
    echo
    echo ">>> 1b. interconnect textfile gauges (spark_ic_*) exported by node_exporter"
    ansible sparks -i inventory/hosts.yml -m shell -a '
        out="$(curl -sf -m 3 http://127.0.0.1:9100/metrics || true)"
        n=$(printf "%s\n" "$out" | awk "/^spark_ic_/ {c++} END {print c+0}")
        printf "  spark_ic_* lines=%s sample:\n" "$n"
        printf "%s\n" "$out" | grep -E "^spark_ic_ping_ok|^spark_ic_rdma_link_up" | head -n 6 || true
    '
    echo
    echo ">>> 2. ms02 Prometheus has cluster=cylon-sparks scrape targets"
    code=$(curl -sG -o /tmp/prom-probe.json -w "%{http_code}" -m 5 \
      'http://192.168.1.189:9090/api/v1/query' \
      --data-urlencode 'query=up{cluster="cylon-sparks"}' || echo 000)
    echo "  http=$code"
    if [ "$code" = "200" ]; then jq -r '.data.result[] | "  \(.metric.host)\t\(.metric.job)\tup=\(.value[1])"' /tmp/prom-probe.json 2>/dev/null || cat /tmp/prom-probe.json; fi
    echo
    echo ">>> 4. ms02 Loki has labels for cluster=cylon-sparks (recent 1h)"
    code=$(curl -sG -o /tmp/loki-probe.json -w "%{http_code}" -m 5 \
      'http://192.168.1.189:3100/loki/api/v1/query' \
      --data-urlencode 'query={cluster="cylon-sparks"}' \
      --data-urlencode 'limit=1' || echo 000)
    echo "  http=$code"
    if [ "$code" = "200" ]; then jq -r '.data.result | length as $n | "  streams=\($n)"' /tmp/loki-probe.json 2>/dev/null || cat /tmp/loki-probe.json; fi

# Stop all vLLM stack containers (Ray + torchrun) on both Sparks.
spark-vllm-stop:
    #!/usr/bin/env bash
    set -euo pipefail
    cd '{{ justfile_directory() }}'
    ansible sparks -m shell -a '
      docker stop $(docker ps -q --filter name=vllm-ngc-ray --filter name=vllm-ngc-torchrun 2>/dev/null) 2>/dev/null || true
      docker ps -a --filter name=vllm-ngc-ray --filter name=vllm-ngc-torchrun
    '
    echo "✓ stopped Ray + torchrun containers"

# Remove the inactive stack and leave only vllm_stack_kind from inventory running.
# Use when Ray and torchrun are both up (port/NCCL thrash).
spark-vllm-stack-teardown:
    #!/usr/bin/env bash
    set -euo pipefail
    cd '{{ justfile_directory() }}'
    ansible-playbook playbooks/provision_sparks.yml -l sparks \
      --tags vllm_ngc_stack,vllm_torchrun_stack,spark_assert \
      -e spark_provision_assert=false \
      --skip-tags apt,kernel,docker,firewall,hf,ngc,spark_obs,spark_wifi

# Docker logs for the Ray head container (bootstrap); for API server stdout see
# spark-vllm-logs-serve.
spark-vllm-logs:
    #!/usr/bin/env bash
    set -euo pipefail
    cd '{{ justfile_directory() }}'
    ansible nvidia1 -m shell -a 'docker logs --tail 120 vllm-ngc-ray-head 2>&1'

# Kill the in-container `vllm serve` (model load is slow; engine-core teardown
# may take ~10s). Useful when the API server is wedged after engine init but
# Ray itself is healthy. Follow with `just spark-provision` to relaunch
# the API; cached weights / torch.compile mean the second start is much faster.
spark-vllm-api-kill:
    #!/usr/bin/env bash
    set -euo pipefail
    cd '{{ justfile_directory() }}'
    ansible nvidia1 -m shell -a \
      'docker exec vllm-ngc-ray-head bash -lc "pkill -f \"[v]llm serve\" || true; sleep 2; pgrep -af [v]llm.serve || echo no_vllm_serve_running"'

# Kill the API server then re-run the role's API-start step (no Ray restart).
# Active stack follows inventory `vllm_stack_kind` (ray | torchrun).
spark-vllm-api-restart:
    #!/usr/bin/env bash
    set -euo pipefail
    cd '{{ justfile_directory() }}'
    stack_kind=$(grep -E '^vllm_stack_kind:' inventory/group_vars/sparks.yml | awk '{print $2}' | tr -d ' ')
    if [[ "${stack_kind}" == torchrun ]]; then
      echo ">>> stack_kind=torchrun — reconcile torchrun containers (no Ray pkill)"
      ansible-playbook playbooks/provision_sparks.yml -l sparks --tags vllm_torchrun_stack
    else
      ansible nvidia1 -m shell -a \
        'docker exec vllm-ngc-ray-head bash -lc "pkill -f \"[v]llm serve\" || true"'
      sleep 5
      ansible-playbook playbooks/provision_sparks.yml -l sparks --tags vllm_ngc_stack
    fi

# Tail detached vLLM API logs on the leader (Ray: vllm serve; torchrun: api_server).
# Reads `vllm_stack_kind` from inventory; exits 0 when log not created yet.
spark-vllm-logs-serve:
    #!/usr/bin/env bash
    set -euo pipefail
    cd '{{ justfile_directory() }}'
    stack_kind=$(grep -E '^vllm_stack_kind:' inventory/group_vars/sparks.yml | awk '{print $2}' | tr -d ' ')
    if [[ "${stack_kind}" == torchrun ]]; then
      container=vllm-ngc-torchrun-nvidia1
      log=/root/vllm-torchrun.log
      proc_grep='torchrun|vllm.entrypoints.openai.api_server'
      restart_hint='just spark-vllm-api-restart   # or: just spark-provision --tags vllm_torchrun_stack'
    else
      container=vllm-ngc-ray-head
      log=/root/vllm-serve.log
      proc_grep='[v]llm serve'
      restart_hint='just spark-vllm-api-restart'
    fi
    ansible nvidia1 -m shell -a \
      "if ! docker inspect ${container} >/dev/null 2>&1; then
         echo \"Container ${container} not found (stack_kind=${stack_kind})\";
         docker ps -a --filter name=vllm-ngc || true;
         echo \"Relaunch with: ${restart_hint}\";
         exit 0;
       fi;
       docker exec ${container} bash -lc \"
         if pgrep -af '${proc_grep}' >/dev/null 2>&1; then
           echo '=== vLLM API process (${stack_kind}) ===';
           pgrep -af '${proc_grep}';
           echo;
         else
           echo 'vLLM API is not running in ${container}';
           echo 'Relaunch with: ${restart_hint}';
           echo;
         fi;
         if [[ -f ${log} ]]; then
           echo '=== tail ${log} ===';
           tail -n 80 ${log};
         else
           echo 'No ${log} yet (created when stack starts via provision)';
         fi
       \""
