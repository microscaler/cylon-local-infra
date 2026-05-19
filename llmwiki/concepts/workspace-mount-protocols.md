# workspace-mount-protocols

**Why is SSHFS slow, and what's the right replacement for the `ms02:~/Workspace` → `Mac:~/Workspace/remote` mount?**

Sibling of [workspace-sync](./workspace-sync.md) (the additive rsync Mac → ms02 push path) and [starlink-wifi-lan-port-filter](./starlink-wifi-lan-port-filter.md) (the network-layer constraint that decides everything for the next few weeks).

## TL;DR

- **Today (home LAN, Mac ↔ ms02)**: **NFSv4** at **`~/Workspace/remote`** is the live secondary path (read/browse from the Mac; heavy builds stay on ms02 via Remote-SSH). Single port **2049/tcp**, `just nfs-up` / `just nfs-down`. SSHFS was retired 2026-04-20; git history has the old recipes.
- **Network**: If the Starlink Wi-Fi↔LAN filter blocks 2049, fix the path (wired to the same LAN as ms02, or router rule) — tunneling NFS over SSH is possible but rarely worth it vs using Remote-SSH for compute.
- **`noresvport` vs `resvport` on macOS**: `just nfs-up` tries **`noresvport` first**, then **automatically retries with `resvport`** if the first `mount_nfs` fails (some macOS builds return **Operation not permitted** for `noresvport` even with a correct server export). Use **`just nfs-up-resvport`** to force privileged client ports only. ms02 exports use **`insecure`** so non-privileged source ports work for `noresvport` clients.
- **Don't pick SMB** unless a future non-dev Mac user needs Finder tag support. On a pure development workflow SMB's wins are irrelevant and its macOS-client quirks (Spotlight spam, stale OpLocks, Sonoma+ perf regressions) are load-bearing.

Measured baseline (2026-04-20, Mac Wi-Fi → Starlink → ms02 wired):

| Operation                                  | SSHFS `~/Workspace/remote` | Local `~/Workspace/local`    | Ratio |
| ------------------------------------------ | -------------------------- | ---------------------------- | ----- |
| `find -type d -maxdepth 3` (entry count)   | 2,832 dirs in **14.50 s**  | 164 dirs in **30 ms**        | —     |
| Normalized per-entry latency               | **5.12 ms/entry**          | **0.18 ms/entry**            | **~28× slower** |

28× per-op latency *is* the editor-pause problem. Rust-analyzer's scan phase, `git status`, `vscode`/`cursor` recursive tree build, `eza --tree`, and Tilt's initial file-fingerprint all do O(N) stat() in a tight loop — at 5 ms each, 10k files is 50 s of wall-clock before any work begins.

## Why SSHFS on macOS is the worst case

macFUSE-SSHFS has four compounding penalties versus native NFS/SMB:

1. **Every syscall is a userspace RPC round-trip.** The kernel hits macFUSE, which marshals to the `sshfs` userspace daemon, which marshals to SFTP over SSH. Every `stat()` is ≥1 RTT. On our link that's LAN ~0.8 ms + Starlink Wi-Fi hop ~3–8 ms + SSHD thread wakeup ~0.5 ms ≈ **5 ms floor per op** — and we're hitting it exactly.
2. **SFTP has no batched/dirent stat.** There is no equivalent of NFS `READDIRPLUS` (which returns names + attributes in one RPC). SFTP `readdir` returns names, then one `lstat` RPC per entry follows. Directory enumeration is fundamentally O(N) round-trips.
3. **SSH is single-stream, single-cipher.** No multiplexed concurrent requests like NFSv4 or SMB3 multi-channel. Everything serializes through one TCP connection with one crypto context. Apple Silicon's SSH on aarch64 does ~80–120 MB/s ceiling for bulk throughput and isn't the bottleneck for us — latency is.
4. **macFUSE kernel round-trip + scheduling.** Even with `auto_cache` the macFUSE layer adds per-op overhead vs the kernel NFS/SMB clients that live in-kernel with direct page-cache integration.

The existing mount options (`reconnect, auto_cache, defer_permissions, noappledouble, noapplexattr, volname`) already do the sensible Mac-side hygiene. What's missing for a quick SSHFS tune-up:

```
-o kernel_cache        # cache in Mac's page cache, not just user-space
-o entry_timeout=60    # how long a dentry lookup is valid (default 1s)
-o attr_timeout=60     # how long stat() results are cached (default 1s)
-o cache_timeout=60
-o compression=no      # aarch64 vs aarch64 over LAN, compression hurts
-o Ciphers=aes128-gcm@openssh.com  # hardware-accelerated, ~2× software AES
```

Downside: cache invalidation becomes a risk if Tilt on ms02 rewrites a file while the Mac has it cached — editor sees stale content. For our editor-reads-only workflow that's usually tolerable.

## Option A: NFSv4 (recommended for post-Starlink)

**Linux side (ms02)** — one export block, one daemon restart:

```
# /etc/exports
/home/casibbald/Workspace  192.168.1.0/24(rw,sync,no_subtree_check,sec=sys,fsid=0)
```

```bash
sudo exportfs -ra
sudo systemctl enable --now nfs-server
```

**Mac side** — no third-party software, no kernel extensions, no macFUSE. **Canonical:** `just nfs-up` (tries **noresvport**, then **resvport**). Manual `mount_nfs` must match the stub layout in **`mac_workstation`** (`~/Workspace/remote` as **root:wheel**). Avoid copy-pasting old examples that use Linux-only or conflicting flags (`intr`, `rdirplus`, `nolocks`+`locallocks` — see `justfile`).

```bash
# Manual noresvport (if you must not use just) — replace IP if needed:
sudo mount_nfs -o vers=4,rsize=1048576,wsize=1048576,hard,noresvport,nfc \
  192.168.1.189:/home/casibbald/Workspace "$HOME/Workspace/remote"
# If that returns Operation not permitted, same line with resvport instead of noresvport, or:  just nfs-up-resvport
```

Or add to `/etc/auto_master` + `/etc/auto_nfs` for autofs / Finder sidebar integration (keep the same option set as `just nfs-up`).

### Why NFSv4 wins

- **Kernel client, no macFUSE.** No kernel extension approval dance, no sleep-wake zombie mounts, no sshfs-mac homebrew cask maintenance.
- **`READDIRPLUS` is the killer feature for our workload.** One RTT returns 32+ entries *with* attributes. Our 28× latency cliff flattens out dramatically — editor tree-walks become one or two RPCs instead of thousands.
- **Close-to-open consistency is the right model for "Mac edits, ms02 consumes"**: when you `:wq` in the editor, ms02 sees the new bytes on next `open()`. That's exactly what Tilt's file watcher does.
- **Single TCP port 2049.** Unlike NFSv3 (needs rpcbind 111 + mountd random + lockd random), v4 has one port → one firewall rule.
- **idmap works** because both sides are `casibbald`. Set `Domain = microscaler.lan` in `/etc/idmapd.conf` on ms02 and the Mac infers. No UID collision hell.
- **Native NFS client is Kerberos-capable** (`sec=krb5p`) when we later need signed + encrypted NFS in a zero-trust LAN; drop-in upgrade from `sec=sys`.

### Known macOS NFS gotchas

- **`noresvport` then `resvport` (default in `just nfs-up`)** — The **Linux export** uses **`insecure`** so clients using **non-privileged** source ports (`noresvport`, macOS default) are accepted. **`just nfs-up`** tries **`noresvport`** first; if **`mount_nfs` fails**, it **retries with `resvport`** (privileged client port). That matches setups where **only** `resvport` succeeds (e.g. **Operation not permitted** on the first attempt on some macOS versions). For a **forced** resvport mount without the first attempt, use **`just nfs-up-resvport`**.
- **Legacy `resvport` wording** — Older Apple docs implied the client always used reserved ports; on current macOS, **`noresvport`** is common. The pairing that matters is **client options ↔ `exports`**: `insecure` on the server for `noresvport`; **`resvport`** does not require relaxing the port check the same way.
- **`nolocks`** — macOS's NFS lock manager negotiation with Linux `rpc.statd` is fragile; if the editor uses `flock()` it'll sometimes hang. `nolocks` (client-side only locks) is the pragmatic choice for a single-user dev mount. The **live** `just nfs-up` stack does **not** pass `nolocks`/`locallocks` (they conflict if both appear; NFSv4 locking is usually enough for this workflow).
- **`nfc` (NFD → NFC normalization)** — macOS filesystems store Unicode as NFD (decomposed); Linux ext4 stores bytes as-is. Without `nfc`, a file named `café.txt` created on Mac becomes unreadable from ms02 side. Always include this flag.
- **Spotlight** — **`mac_workstation`** disables indexing on **`~/Workspace/remote`** via **`mdutil`**. Repeat manually if you add paths. `/etc/fstab`-style option `nobrowse` also hides the mount from Finder sidebar if desired.
- **Mount stub** — **`~/Workspace/remote`** is a **`root:wheel`** directory created by **`mac_workstation`**; **`just nfs-up`** mounts ms02’s export there. Open **`~/Workspace`** in Cursor so `local/` + `remote/` stay side by side. See `just nfs-doctor`.
- **No README on the stub** — Do not keep **`README.md`** under **`~/Workspace/remote`**. Documentation lives in **`cylon-local-infra`** (`docs/`, this wiki, `justfile`). Ansible **does not** seed a stub README; it **deletes** a stray **`README.md` only while unmounted** (when mounted, that path is ms02). Point Cursor at **`local/`** for repo docs, not `remote/`.
- **EPERM / “Operation not permitted”** — Often **(1)** **`just nfs-up`** automatic **resvport** retry fixes it, **(2)** **Full Disk Access** for the app running the shell (Terminal, iTerm, **Cursor** — restart the app after toggling), **(3)** **`just nfs-troubleshoot`** probes **`/private/tmp`** with **noresvport** vs **resvport** vs **`mount -t nfs`**. **`just nfs-up`** runs **`xattr -cr`** on the stub before mount (some macOS builds are picky about **provenance** xattrs). On ms02: **`sudo exportfs -v`**, **`ss -lntp | grep 2049`**.
- **FSEvents** — NFS mounts don't fire Mac FSEvents for server-side changes. Same limitation as SSHFS. See "File watching" section below.

### Network prerequisite

NFS needs port **2049/tcp** from Mac → ms02 on the **LAN**. `inventory/host_vars/ms02.yml` allows **`2049`** (and the dev port range) from **`192.168.1.0/24`**. If a **router** still filters Wi-Fi↔LAN, fix the network path (wired to the same switch as ms02, or router rules) — **cylon-local-infra** no longer ships an SSH-tunneled NFS fallback (`ms02-dev-tunnel` retired).

## Option B: SMB3 (Samba)

**Linux side** — heavier config than NFS:

```conf
# /etc/samba/smb.conf
[workspace]
  path = /home/casibbald/Workspace
  valid users = casibbald
  read only = no
  create mask = 0644
  directory mask = 0755
  vfs objects = fruit streams_xattr
  fruit:metadata = stream
  fruit:resource = stream
  fruit:encoding = native
```

```bash
sudo smbpasswd -a casibbald   # separate password from the Linux account
sudo systemctl enable --now smbd
```

**Mac side**:

```bash
open "smb://casibbald@ms02/workspace"
# or mount_smbfs -o soft //casibbald@ms02/workspace ~/Workspace/remote
```

### Where SMB would beat NFS

- **Finder / macOS ecosystem integration**: Finder tags, coloured labels, `com.apple.FinderInfo`, etc. — the `fruit` VFS module in Samba exists specifically for this. Irrelevant for a terminal + editor dev workflow.
- **Multi-user share**: proper authentication per-user, ACL support. Not our use case.
- **Works on Windows clients** if we ever add a Windows dev Mac. Not on the roadmap.
- **SMB3 multi-channel**: Mac → ms02 over two NICs can parallelize. Not applicable — one NIC each side.

### Where SMB loses

- **Spotlight indexing is the default on SMB mounts and is a documented disaster.** Must `mdutil -X` *and* add `.metadata_never_index` *and* set `com.apple.NetworkBrowser.ShouldRandomizeMountPoint`. More moving parts.
- **Apple's SMB client has a rocky history post-Big Sur** — known perf regressions on Ventura/Sonoma for small-file operations, stuck oplock releases causing mount hangs on sleep/wake. NFS has been boring and stable on Mac for 15 years; SMB has been actively broken in multiple recent macOS minor versions.
- **Case-sensitivity mismatch** — HFS+/APFS default is case-insensitive; ext4 is case-sensitive. `Makefile` vs `makefile` drift is a real hazard; SMB papers over it on the Mac side in a way that creates silent conflicts (two files that look the same in Finder, are different on ms02).
- **Samba user management** is a separate password store from the Linux account — another credential to rotate, decrypt, and remember.
- **Encoding quirks**: filenames with `:` (common in log files) get mangled by SMB's Windows compatibility layer unless `fruit:encoding = native` is set (shown above).
- **No `READDIRPLUS` equivalent as clean as NFSv4's** — SMB2+ has `FIND_ID_BOTH_DIRECTORY_INFO` which does bundle attrs with dirents, so the metadata story is comparable to NFSv4 in theory. In practice the macOS SMB client doesn't always exploit it well.

### Same network prerequisite

SMB needs **445/tcp**. Starlink blocks. Same tunnel-over-SSH escape, same marginal-win caveat as NFS.

## Option C: SSHFS (staying put, but tuned)

If the wired adapter is weeks out, tune what we have:

```diff
 sshfs "$remote" "$local_mount" \
     -o reconnect \
     -o ServerAliveInterval=15 \
     -o ServerAliveCountMax=3 \
     -o auto_cache \
+    -o kernel_cache \
+    -o cache=yes \
+    -o entry_timeout=60 \
+    -o attr_timeout=60 \
+    -o cache_timeout=60 \
+    -o Ciphers=aes128-gcm@openssh.com \
+    -o Compression=no \
     -o defer_permissions \
     -o noappledouble \
     -o noapplexattr \
     -o volname=ms02-workspace
```

Expected improvement:
- Second-visit directory walks: **5 ms/entry → cached (sub-ms)** — the 14.5 s walk drops to <1 s on warm cache.
- First-visit (cold cache): unchanged. The underlying SFTP-no-READDIRPLUS problem isn't solvable with options.
- Bulk throughput: modest gain from `aes128-gcm` (AES-NI on M-series) vs the default `chacha20-poly1305`.
- Stale-cache risk: the 60 s TTL means if Tilt on ms02 rewrites a config file, Mac-side editor won't see it for up to a minute. Hit `:e` / `:reload` to force.

This is the **right intermediate step** — zero new infrastructure, recovers most of the pain without waiting for hardware.

## File watching (orthogonal to all three protocols)

None of SSHFS, NFS, or SMB bridge inotify (Linux) ↔ FSEvents (macOS) in either direction. This is a fundamental protocol limitation, not a config issue:

- **Mac editor watches a file on the mount, ms02 rewrites it**: the editor will not wake up. It either polls (slow, CPU-hungry) or stays stale.
- **Tilt on ms02 watches the same tree, Mac editor rewrites a file**: Tilt's inotify on ms02 *does* fire (the write lands as a local ext4 event from nfsd/smbd's perspective) — so builds rebuild correctly. This is the direction that matters for our workflow.

Practical consequence: use the mount for **Mac → ms02 writes** (editor saves) and **Mac reads of ms02 state** (browse logs, grep source). For **Mac-side live reactions to ms02-initiated changes** (e.g. Tilt touches a file and we want Cursor to reload it), the answer is to keep that flow inside Cursor Remote-SSH — which uses SSH to run the editor server on ms02 and so sees ms02's inotify natively.

This is already the repo's documented posture: `docs/remote-dev.md` says the mount is "SECONDARY… for Finder/grep/tree convenience", with Remote-SSH as the primary. The protocol choice doesn't change that posture, only how fast the secondary path is.

## Recommendation

**Status as of 2026-04-20: Phase 2 is live; Phase 1 superseded without ever landing.**

1. **Phase 1 (originally proposed)** — add caching flags to SSHFS. **Superseded.** The NFSv4 cutover happened first, so the SSHFS tuning was never committed. If you're ever forced back onto SSHFS (e.g. temporary loss of NFS on a road trip machine), the flags `-o kernel_cache,entry_timeout=60,attr_timeout=60` are still the right starting point — they're the 10–30× warm-cache win, with a 60 s stale-read TTL as the tradeoff.

2. **Phase 2 — NFSv4 at `~/Workspace/remote` (live 2026-04-20, supersedes SSHFS):**
   - `roles/nfs_server/` — ms02-side: `nfs-kernel-server` + idmapd + templated `/etc/exports` (handler-driven `exportfs -ra`; no service restart on export changes — preserves live mounts).
   - `roles/mac_workstation/` — Mac-side, local-connection: ensures `~/Workspace/remote` exists, excludes from Spotlight idempotently via `mdutil -s` probe, writes `/etc/nfs.conf` with the NFSv4 idmap domain matching ms02.
   - `inventory/host_vars/ms02.yml` — export to **`192.168.1.0/24`** with **`insecure`** (macOS `noresvport` clients) and **`all_squash,anonuid=1000,anongid=1000`** — single-operator dev mount; Mac UID / `staff` vs ms02 UID 1000 is handled server-side. Adds **`2049/tcp`** to **`firewall_trusted_lan_tcp_ports`**. (Historical **`127.0.0.1/32`** SSH-tunnel export **removed** — LAN + firewall only.)
   - `inventory/hosts.yml` — `macs` group, `picolino` as a `local`-connection host.
   - `playbooks/nfs_server.yml` + `playbooks/mac_workstation.yml` — dedicated playbooks; both check-mode safe.
   - `mac_workstation` — ensures **`Include ~/.ssh/config.d/*`** and **removes** retired **`~/.ssh/config.d/ms02-dev-tunnel`**; no managed port-forwards.
   - `justfile` — `nfs-up` (noresvport→resvport retry) / `nfs-down` / `nfs-status` / `nfs-bench` / `nfs-reconnect` / `ms02-lan-check`; `mac-provision` / `nfs-server-provision` with `-check` variants. SSHFS + **`dev-tunnel-*`** recipes removed (git history preserves them).

   **Working macOS mount options:** `vers=4,rsize=1048576,wsize=1048576,hard,noresvport,nfc` (first try), then the same with **`resvport`** instead of **`noresvport`** if the first `mount_nfs` fails — see **`just nfs-up`**. Do NOT add `nolocks`, `locallocks`, `rdirplus`, `intr`, or `bg` — they're either Linux-only, mutually contradictory, or cause zombie retry daemons to stack mounts on EINVAL. The `justfile` `nfs-up` recipe documents the per-flag rationale.

   **Activation order** (historical — 2026-04-20 cutover): bootstrap ms02 NFS server by direct SSH → fix exports with `all_squash,anonuid=1000` → LAN `mount_nfs` at `~/Workspace/remote` → reconcile via `just nfs-server-provision` → `mac-provision`. (Early iterations used an SSH tunnel for NFS; that path is **retired**.)

**Do NOT pick SMB.** On a pure development workflow where all participants are Unix, every advantage SMB offers (Finder tag interop, per-user ACLs, Windows clients) is irrelevant, and the two places it loses (Mac SMB client stability, Samba config surface) cost more than they save.

## Benchmarks to run post-cutover

Save these alongside the cutover run log so future protocol changes have an apples-to-apples number:

```bash
# Metadata (dir walk): the number that actually matters for editors
time find ~/Workspace/remote -maxdepth 3 -type d | wc -l

# Metadata (stat storm): rust-analyzer / git status approximation
time (cd ~/Workspace/remote/microscaler/hauliage && \
      find . -type f -name '*.rs' | head -500 | xargs stat -f "%N" >/dev/null)

# Sequential read (bandwidth ceiling)
time dd if=~/Workspace/remote/<some-large-file> of=/dev/null bs=1m count=500

# Single-file small write (editor save)
time (echo "ping" > ~/Workspace/remote/tmp/ping.txt)

# Tree enumeration (Cursor workspace index)
time (find ~/Workspace/remote/microscaler -type f | wc -l)
```

Target numbers (on 2.5GbE wired with NFSv4):
- Dir walk: <500 ms for 2,832 dirs (30× improvement over current 14.5 s).
- Stat storm 500 files: <2 s (vs ~2.5 s current — not a big improvement, but this is latency-floor territory).
- Sequential read: sustained 200+ MB/s (vs ~80 MB/s SSHFS ceiling).
- Editor save: <50 ms (vs ~100 ms current — also latency-floor).

## See also

- [workspace-sync](./workspace-sync.md) — the *additive* Mac → ms02 rsync that pre-dates any mount; still the canonical way to seed the tree.
- [starlink-wifi-lan-port-filter](./starlink-wifi-lan-port-filter.md) — the network-layer reason NFS/SMB are blocked today.
- [starlink-router](../entities/starlink-router.md) — retires when the USB 2.5GbE adapter arrives.
- `docs/remote-dev.md` — the dual-track "Mac editor, ms02 compute" pattern; all three protocols are the *secondary* path.
- `justfile` → `nfs-up` (noresvport then resvport retry) / `nfs-up-resvport` / `nfs-down` / `nfs-status` / `nfs-bench` / `nfs-reconnect` / `nfs-doctor` / `nfs-troubleshoot` / `ms02-lan-check` — live recipes (SSHFS + SSH tunnel recipes removed; use LAN URLs — see `docs/dev_hosts.md`).
