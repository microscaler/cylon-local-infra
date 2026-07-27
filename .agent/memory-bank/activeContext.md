# Active Context

**Last updated:** 2026-07-27 — Kimi K3 archive-path assessment.

## Current focus

Prepare a storage-only ingestion path for the announced Kimi K3 open weights.
Do not stage the release on ms02 or either Spark root filesystem: ms02 has
654 GiB free and the Sparks have 454/429 GiB free, versus an unverified
third-party estimate of roughly 594 GB for K3. Prefer a transient GCE downloader
in the bucket's region, writing through Cloud Storage FUSE v3 streaming writes,
so the payload never traverses the home WAN or consumes equivalent local disk.
The fallback is the same streaming mount on ms02.

### Done 2026-07-25

- Added role **`wireguard`**, playbook `playbooks/wireguard_mesh.yml`, inventory
  group `wireguard_linux_nodes`, CLI recipes, tests, and
  `docs/wireguard-mesh.md`.
- Deployed Hostinger `wg0` **10.77.0.1/24** and ms02 `wg0`
  **10.77.0.2/32** over `76.13.1.95:51820/udp`.
- Host-local private keys remain mode `0600` under `/etc/wireguard`; only
  public keys are exchanged by Ansible.
- `wg-quick@wg0` and `wireguard-healthcheck-wg0.timer` are enabled and active
  on both hosts. The timer repairs missing/stale interfaces.
- Verified bidirectional ping, current handshakes, host boot enablement,
  recovery after deliberately stopping each interface, and an idempotent
  second Ansible apply (`changed=0`).
- Reverse SSH remains active and verified through `ssh ms02-away`.
- Tests: 10 passing; healthcheck coverage 98%; Ansible syntax and check mode
  pass.

### Current operational commands

```bash
cd ~/Workspace/local/cylon-local-infra
just wireguard-linux-check
just wireguard-linux-up
just wireguard-linux-status
```

### Previous LAN edge state

- `metallb_lan_route` and `dev_dns` are applied on ms02.
- Mac LAN resolver and static MetalLB route tasks are ready.
- `microscaler-lan-proxy.service` remains managed from shared-k8s.

### Next (plan)

Add the on-demand Mac WireGuard peer/toggle, including deterministic
coexistence with the existing LAN static route. Then validate remote NFS and
Kubernetes access through ms02.

Readiness rechecked at 2026-07-25 22:32 EEST: both Linux interfaces and health
timers are active, the handshake is current, and ms02 → VPS ping is healthy.
Keep the Mac on the current LAN until its peer and toggle have been deployed.

Mac deployment attempt at 22:44 EEST stopped before any WireGuard Mac changes:
Ansible's become password was rejected while updating the existing narrow
sudoers drop-in. Validate/cache sudo with `sudo -v`, then rerun the playbook
without `--ask-become-pass`.

The cached sudo ticket was not inherited by Ansible's non-TTY local connection.
Deployment remains unchanged. Rerun with `--ask-become-pass` and use the same
password just validated successfully by `sudo -v`.

Mac peer deployed at 22:50 EEST and successfully reached the VPS. LAN testing
found two integration gaps: Docker's DROP policy on the VPS blocked wg0 peer
forwarding, and this Mac did not yet have the LAN MetalLB LaunchDaemon installed.
Both are fixed in source and covered by 25 passing tests (94% total coverage).
The live VPS forwarding rule is applied and Mac→ms02, NFS, DNS, Kubernetes API,
and MetalLB HTTPS probes pass. One final full playbook apply is required to
install the updated root-owned Mac toggle/LaunchDaemon and reconcile the VPS
configuration from its template before away-network testing.

Final reconciliation and lifecycle test completed at 23:11 EEST. Verified:
WireGuard down restores `10.177.76.0/24 via 192.168.1.189`; WireGuard up moves
that route to `utun6`; Mac reaches ms02 at `10.77.0.2`; Kubernetes API
`10.177.76.137:6443`, ingress `10.177.76.234:443`, and tunneled NFS all work.
The tunnel is left active, NFS is deliberately unmounted for the Wi-Fi switch,
and the independent `ms02-away` reverse-SSH fallback is active. Ready for
away-network testing; remount NFS only after the new network is stable.

Away-network validation completed at 23:14 EEST on A1 Wi-Fi. The existing
`utun6` tunnel re-handshook without toggling. Split routes remained on utun6;
VPS, ms02, the ms02 LAN /32, Kubernetes API, MetalLB ingress, and dev DNS all
passed. Kubernetes `/version` returned the expected unauthenticated 401,
`hauliage.dev.microscaler.local` returned HTTP 200, NFS mounted and a file was
read through it, and `ms02-away` remained active (one stale SSH mux socket
caused a harmless reconnect delay). The remote-access goal is complete.

Sunshine/Moonlight replaced the proposed GNOME RDP path on 2026-07-27. The
`sunshine_remote_desktop` role now runs an isolated Xorg/XFCE session on display
`:10` without console autologin or a physical dummy plug. The official
`v2026.516.143833` Debian Trixie package is pinned by SHA-256 because it matches
Ubuntu 25.10's glibc 2.42 / ICU 76; the Ubuntu 24.04 and 26.04 packages are
dependency-incompatible. The virtual display is 2560x1440 at 59.95 Hz.
Sunshine detects H.264, HEVC, and AV1 VA-API encoders through Intel
`/dev/dri/renderD128`. All three systemd services are enabled, active, and
idempotent (`changed=0`). The Web UI returns HTTP 307 at
`https://10.77.0.2:47990` over WireGuard.

Only picolino's WireGuard `/32` can reach Sunshine's TCP/UDP ports; UPnP is
disabled. Seven contract tests, syntax check, dry run, deployment, display
probe, encoder probe, Web UI probe, and idempotence pass. Next operator action:
install the official Moonlight Qt macOS Universal DMG from moonlight-stream.org
(the App Store build is iOS/iPadOS/tvOS only; Steam Link is incompatible),
create the initial Sunshine Web UI credentials, add host `10.77.0.2`, approve
Moonlight's PIN, and verify Cursor in the streamed desktop. Use
`just sunshine-web`, `just sunshine-status`, and
`docs/sunshine-remote-desktop.md`.

Moonlight troubleshooting at 2026-07-27 11:42 EEST found no UDP/tunnel fault:
WireGuard handshakes, routes, ping, VPS peer forwarding, UFW rules, and direct
UDP probes all pass. The packet capture showed bidirectional TCP 47989 but no
47984/stream-session traffic. Sunshine reports `PairStatus=0` and its state
contains credentials but no paired device. Moonlight also saved the discovered
LAN address `192.168.1.189`, not `10.77.0.2`. Remove that host, manually add
`10.77.0.2`, complete the displayed PIN in Sunshine's PIN page, then launch the
Desktop tile. Web UI credentials alone do not pair Moonlight.

First stream exposed frozen keyboard/mouse. Root cause was the headless Xorg
template setting `AutoAddDevices=false`, which prevented Sunshine's uinput
devices from joining the desktop despite correct `/dev/uinput` permissions.
The role now installs `xserver-xorg-input-libinput` and enables
`AutoAddDevices`. Eight contract tests pass; the fix is deployed. Xorg confirms
the Sunshine keyboard and absolute/relative mouse devices use libinput.

At 2026-07-27 11:50 EEST, reduced the Sunshine virtual mouse speed by half with
a persistent Xorg `InputClass` matching `Mouse passthrough` and libinput
`AccelSpeed=-0.5`. Nine contract tests pass; the configuration is deployed and
all Sunshine services remain active at 2560x1440. Reconnect Moonlight to test
the new speed because deployment restarts the virtual desktop.

Cursor state migrated Mac→ms02 at 2026-07-27 12:40 EEST. Existing remote state
is backed up at `~/.cursor-migration-backups/20260727-123132`. Copied 52 project
contexts (including a remapped current-workspace directory), 19 current-project
transcripts, plans, skills, agents, MCP config, 65,127 file-history entries,
workspace history, settings, keybindings, and snippets. Extracted and merged
only six portable history-index records from the Mac's pathological 33 GB
`state.vscdb`; did not copy the database, its 27 GB backup, caches, extensions,
or machine authentication. Rewrote Mac workspace/plan paths to ms02 paths and
adapted the Kubernetes MCP kubeconfig path. Conversation indexes are healthy:
248 composer headers and 451 searchable conversations. Removed plaintext
API/GitHub tokens from the remote settings; they remain in the Mac source
settings and should be rotated/moved to approved environment storage.

Kimi K3 storage assessment at 2026-07-27 14:50 EEST: the verified Moonshot AI
Hugging Face organization did not yet list a Kimi K3 repository, despite the
announced July 27 release. Record the official repository and immutable commit
only after it appears; do not ingest a third-party mirror. Existing
`/srv/hf-archive` on ms02 is an empty ZFS-backed NFS archive but shares the
654 GiB root pool. Spark roots have only 454 GiB and 429 GiB available.
Cloud Storage FUSE v3 supports direct streaming writes without local staging.
For `hf download`, set `HF_HUB_DISABLE_XET=1` because Xet reconstructs files
from concurrent byte ranges, while the GCS streaming path should remain
sequential. Run `hf download --dry-run` first, pin the commit, and use a
dedicated bucket prefix. The current ms02 gcloud account targets project
`cf-sam-pp-9e`, but its user token requires reauthentication, so bucket name,
region, and permissions remain unconfirmed. Pending operator choice: transient
GCE downloader (recommended) or ms02 GCS FUSE fallback.
