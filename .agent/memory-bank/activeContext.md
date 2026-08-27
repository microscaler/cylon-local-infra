# Active Context

**Last updated:** 2026-07-27 — Kimi K3 archive watcher deployed.

## Current focus

Preserve the official Moonshot Kimi K3 FP8 weights first, then FP16/BF16.
`hf-archive-downloader` polls Hugging Face every 15 minutes, pins a verified
`moonshotai` repository to its immutable SHA, downloads to local ext4, uploads
serially to GCS, checksum-verifies the bucket, cleans that local snapshot, and
only then proceeds to the second precision. Do not stage the release on ms02
or either Spark root filesystem.

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

Kimi K3 preservation infrastructure deployed on project `microscaler`.
`gs://microscaler-huggingface` is an EU multi-region bucket with default
storage class `ARCHIVE`, uniform bucket access, and public access prevention.
The Cloud Run/FUSE experiment was removed after its managed FUSE mount fell
back to local staging under concurrent HF writes. The replacement
`hf-archive-downloader` is an `e2-standard-4` Ubuntu 24.04 VM in
`europe-west1-b`, reachable only through IAP SSH. It has a 30 GB boot disk and
an auto-deleted 2 TiB `pd-standard` ext4 data disk mounted at
`/srv/hf-archive`.

The pinned HF CLI is installed in `/opt/hf-archive-venv` at version 1.24.0.
The `Qwen/Qwen3.6-35B-A3B` dry run reported 40 files / 71.9 GB. Download to
local ext4 completed successfully in 21 minutes with eight workers, producing
67 GiB. Upload to
`gs://microscaler-huggingface/models/Qwen/Qwen3.6-35B-A3B` is running as
`hf-qwen36-upload-serial.service` with GCS process/thread concurrency fixed at
1/1. Transfer duration is secondary to preserving the verified local snapshot.
Once complete, run a checksum-only dry rsync before deleting the local test
snapshot. The active VM identity is
`hf-archive-vm-runner@microscaler.iam.gserviceaccount.com` with
`roles/storage.objectUser` only on the archive bucket. Wait for verified
Moonshot repositories and immutable commit SHAs before starting Kimi.

`hf-archive-watcher.timer` is enabled on the VM and polls every 15 minutes.
It accepts only verified `moonshotai` Kimi K3 safetensor repositories, detects
FP8 and FP16/BF16 primarily from tensor metadata, and rejects GGUF, MLX, ONNX,
AWQ, GPTQ, EXL2, INT4/INT8, FP4, MXFP4, and NVFP4. FP8 must archive,
checksum-verify, and be removed locally before FP16/BF16 starts. The first live
poll completed successfully with no Kimi K3 FP8 candidate yet. Source:
`scripts/hf_archive_watcher.py`; tests: 18 passing at 87% coverage; operations:
`just hf-kimi-watcher-status`, `just hf-kimi-watcher-logs`, and
`just hf-kimi-watcher-poll`.

On 2026-07-28, `RedHatAI/Kimi-K3-FP8-BLOCK` was the first available Kimi K3
FP8 candidate. Hugging Face metadata confirms Kimi K3 architecture, upstream
lineage `moonshotai/Kimi-K3`, block-wise compressed-tensors FP8, 96 safetensor
shards, predominantly `F8_E4M3` parameters, and immutable SHA
`a0dd16556a81f79acee4e576e88aae2e356472f1`. The exact dry run is 118 files /
2.8 TB, which does not fit the current 2 TiB disk. The existing watcher also
rejects it because its publisher allowlist is currently `moonshotai` only.
Before starting, choose whether to trust this exact RedHatAI repository and
resize the transient disk to 4 TiB, or continue waiting for Moonshot.

Decision: ignore the 2.8 TB RedHatAI FP8 build for now because it is too large
for the target hardware/use case. No disk resize, allowlist change, or download
was performed for that repository.

Current GCE sizing confirmed 2026-07-28: `hf-archive-downloader` was reduced to
standard-provisioned `e2-medium` (2 visible vCPU, 4 GiB RAM), with its existing
30 GB balanced boot disk and 2,048 GB standard persistent data disk unchanged.
GCE maps `e2-custom-1-4096` to this shape; the operator accepted it.

`unsloth/Kimi-K3` is now the explicit temporary backstop. Metadata identifies
Kimi K3 with `moonshotai/Kimi-K3` lineage, compressed-tensors packed MXFP4,
96 safetensor shards, and SHA
`6095bb6b51abc90e7e8f8804ad4f65be4a441262`. The pinned dry run is 121 files /
1.6 TB and fits the 2 TiB disk. The watcher archives this exact exception
without marking FP8 complete, then continues polling the Moonshot-only FP8 and
FP16/BF16 lanes. Download is active and resumable with two workers; it reached
28 GB locally during verification and uses about 2.4 GiB RAM. Twenty tests pass
at 87% coverage.

A delayed IAP SSH command reported exit 255 after timing out, but the remote
restart had already succeeded. Follow-up verification found the pinned Unsloth
download healthy with `--max-workers 2`, 40 GB stored, and the systemd service
still active.

On 2026-07-29 the Unsloth backstop was found stalled at 931 GiB. The 15-minute
timer was retrying correctly, but the free-space guard rejected the remaining
1.0 TB plus its 20 GiB reserve because the completed local Qwen test still used
67 GiB. Local and GCS Qwen trees matched exactly at 71,926,880,713 bytes, so
the local Qwen copy was removed. Available space increased to 998 GiB and the
pinned Unsloth download resumed with two workers; service state is active and
the partial tree is advancing again. No Kimi snapshot is complete in GCS yet.

Disk accounting clarified: the GCE data disk is 2,048 GiB (2 TiB), not 3 TB.
The block device is 2,199,023,255,552 bytes and ext4 exposes
2,163,368,968,192 usable bytes. After Qwen cleanup, 1,063,571,881,984 bytes are
available. The prior guard failure occurred because the rounded HF dry run
reported 1,000,000,000,000 bytes remaining and the configured 20 GiB reserve
required about 1.021 TB, while only 1.002 TB was then free.

Sunshine local-LAN access enabled 2026-08-01. Root cause of Moonlight failing
at home was the intentional WireGuard-only UFW policy. The same Sunshine TCP
and UDP ports are now allowed from trusted `192.168.1.0/24`, while public
access remains blocked. Sunshine CSRF origins include both
`https://192.168.1.189:47990` and `https://10.77.0.2:47990`. All three services
are active; a Mac-originated HTTPS probe to the LAN address returns the expected
authenticated HTTP 401. At home, add/use host `192.168.1.189` in Moonlight;
away, use `10.77.0.2`. Nine contract tests and Ansible syntax pass.

Kimi archive status checked 2026-08-01: GCS contains no Kimi snapshot and the
watcher state is empty. The watcher does not target
`RedHatAI/Kimi-K3-NVFP4`; NVFP4 was excluded by the original precision policy.
It remains stuck retrying the older `unsloth/Kimi-K3` MXFP4 backstop. That
partial tree uses about 1.1 TiB, with 916.8 GB still required; the 20 GiB
reserve rejects the run because only 923.8 GB is free. The RedHatAI NVFP4
repository is a valid Kimi K3 compressed-tensors NVFP4 build with
`moonshotai/Kimi-K3` lineage, 83 safetensor shards, immutable SHA
`71eed69448bc5ee7f83c4c08a61d6598a25b6ae4`, and an exact dry-run size of
100 files / 1.6 TB. A decision is required before deleting the 1.1 TiB Unsloth
partial or resizing storage.

Decision applied: stop the VM, expand `hf-archive-data` from 2 TiB to 3 TiB,
grow ext4, delete the incomplete Unsloth partial, and switch the exact archive
exception to `RedHatAI/Kimi-K3-NVFP4`. The filesystem now reports 3.0 TiB with
2.9 TiB free after cleanup. The watcher and timer are enabled again, and the
pinned download at SHA `71eed69448bc5ee7f83c4c08a61d6598a25b6ae4`
is actively running with two workers. The resulting bucket target will be
`gs://microscaler-huggingface/models/RedHatAI/Kimi-K3-NVFP4/<sha>`.
Twenty watcher tests pass; the previously measured coverage remains 87%.

The NVFP4 archive completed and GCS verification reports 1,557,434,547,173
bytes at the pinned bucket prefix. The watcher timer was disabled, the VM was
stopped, and the detached 3 TiB `hf-archive-data` disk was deleted to eliminate
its ongoing storage cost. `hf-archive-downloader` remains terminated with only
its 30 GiB boot disk and configuration. A replacement data disk must be
created, attached as `archive-data`, formatted, and mounted before the VM is
started again.

On 2026-08-25, diagnosed recent Mac-to-ms02 NFS slowness as a Wi-Fi latency
problem rather than NFS, ZFS, or ms02 Ethernet. The Mac (`192.168.1.130`) showed
100-packet RTTs of 2.3/29.9/221 ms to ms02 and 2.4/28.2/216 ms to the router,
with periodic 100–220 ms stalls approximately every 500 ms. In contrast, wired
ms02-to-router RTT was 0.72/0.88/1.68 ms. The Mac has a strong 5 GHz 802.11ac
link (-44 dBm, 866 Mbps), so likely causes are Wi-Fi airtime scheduling,
roaming/scanning, AWDL/Continuity coexistence, or AP behavior rather than weak
signal. Bulk SSH throughput was about 214 Mbps. ms02's NFS service is active,
port 2049 is listening, NFS has zero bad RPC calls, eno4 is 1 Gbps full duplex
with zero errors/drops, both ZFS pools are healthy, and observed ZFS latency was
sub-millisecond. The rpool is 88% allocated/58% fragmented, but it was not
latency-bound during the sample. The Mac had no active NFS mount and no NFS TCP
session; `~/Workspace/remote` was only the local root-owned mount stub. Next
isolate with wired Ethernet or temporarily disable AWDL/Continuity, then
reconnect the NFS mount after the network latency is stable.

Ethernet follow-up on 2026-08-25 confirmed the prior diagnosis: the Mac now
routes to ms02 over `en20` and RTT is stable at 0.82/1.05/1.59 ms. A read-only
ZFS space audit found `rpool` healthy but 87% allocated, with 174 GiB available
and no snapshots. The largest safe/regenerable opportunity is approximately
772 GB of Rust `target` directories across hauliage, sesame-idam-broken-index,
cylon, BRRTRouter, fleetingdns, lifeguard, rerp, PriceWhisperer, and
sesame-idam; no cargo/rustc/sccache processes were active at audit time. Docker
has 705 volumes, only 8 active, and reports 288.9 GB logical reclaimable
(the entire compressed Docker ZFS dataset is 208 GiB physical); the three
largest unused anonymous volumes contain 37 GiB, 75 GiB, and 65 GiB. Two ZFS
iSCSI datasets have no Kubernetes PV and no open target sessions but still
have targetcli objects: `pvc-d0f1...` uses 59.3 GiB and `pvc-7ab0...` uses
37.6 GiB. Old rotated syslogs use about 22 GiB. Multipass uses 75 GiB for ten
running VMs plus one stopped base and should not be treated as cache. Other
operator-review candidates are the 60.7 GiB Hugging Face cache, 47.5 GiB
`backup_mac`, and user documents/backups. No deletion was performed.

Zot follow-up on 2026-08-25 found that the registry is not part of ms02's
208 GiB Docker dataset. Zot v2.1.17 runs in Kubernetes and stores data on
`pvc-b901...`, which uses 34.2 GiB at the ZVOL level and about 21 GiB in its
mounted filesystem. Zot's GC is healthy (2,528 successful runs; 3,937 blobs
and 1,593 manifests deleted). The retention failure is configuration drift:
the live ConfigMap keeps five `dev-\d+` tags and then has a catch-all `.*`
keep rule with no limit, which retains every other tag. This is reflected in
36 tags for `cylon-daemon`, seven each for two Sesame repositories, and six
each for three PriceWhisperer repositories. The local GitOps file already
contains the intended single `mostRecentlyPushedCount: 2` policy, but that
change is uncommitted. The GitOps checkout is one unrelated commit ahead of
origin plus the uncommitted Zot edit. Flux's `stack-cluster` Kustomization is
also blocked: its current source revision fails dry-run because the recreated
bound `zot-data` PVC has an immutable `spec.volumeName` that Flux attempts to
remove. Last applied revision is `b957d102...`; attempted source revision is
`50ade5e6...`. No registry or Docker data was deleted.
