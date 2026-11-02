# Progress

## Session 2026-11-02 — fleet and GitOps commits pushed

`cylon-local-infra` `8f0ca3c` is on `origin/main`. Shared-gitops `main` is
`f40647f`: Flux status UI `140b2a5`, Zot two-tag retention plus
`docker2s2` `bc346d6`, and the August memory-bank notes `f40647f`.
Both working trees are clean. A later fetch showed each `main` equal to
`origin/main` (`e507b39`, `f40647f`). Nothing to rebase. The pushes were
fast-forwards.

## Session 2026-09-01 — finish rerp Failed HelmReleases

**Missed:** `rerp/general-ledger` + `invoice` had suspend cleared; Ready=RollbackFailed. Re-suspended then **deleted** (KS `rerp-accounting-services` already suspended so they will not recreate). Deploys left at 0/0. Manifests in rerp repo already `suspend: true` (still unpushed).

## Session 2026-09-01 — finish hauliage ImageRepository suspend

**Problem:** Live `spec.suspend: true` was cleared by GitOpsSet (template had no suspend); Failed Ready condition stuck.

**Fix:** Suspended `cluster-control` + `platform-gitopssets`; set GitOpsSet ImageRepository/Policy/IUA template `suspend: true`; deleted + recreated all 24 hauliage ImageRepos so they are born suspended (no Failed condition). Re-suspended hauliage/rerp KS.

**Pending push:** inventory + `${{ .Repeat.suspend }}` template; then unsuspend control KS. Until then sesame/pw image automation also suspended.

## Session 2026-09-01 — suspend hauliage ImageRepositories

**Done (live):** All 24 hauliage-labeled ImageRepository + matching ImagePolicy + `hauliage-dev-images` IUA set `spec.suspend: true` (also searates-terminal-mock). Failures were `NAME_UNKNOWN` (images not in registry while haulage paused). Also suspended rerp ImageRepos/IUA for consistency.

**Git (unpushed):** `product-components.yaml` image/automation `suspend` flags; GitOpsSet template emits `suspend` on ImageRepository/ImagePolicy/ImageUpdateAutomation.

## Session 2026-09-01 — platform-gitopssets HealthCheckFailed

**Root cause:** `wait: true` on `platform-gitopssets` blocked on GitOpsSet `product-components` InProgress/False. GitOpsSet error since ~2026-08-14: PriceWhisperer resources labeled for the set but missing from inventory → create failed with `already exists` (product-pricewhisperer, pricewhisperer-core, website/trader/platform Image* + IUA).

**Fix (live):** delete orphans; force-remove `pricewhisperer-core` Flux finalizer (stuck terminating); reconcile GitOpsSet → `128 resources created` Ready; `platform-gitopssets` Ready (`Applied revision: main@sha1:db814eb…`).

**Follow-ups:** PW `ib-gateway-paper` still unhealthy; shared-gitops/rerp suspend commits still unpushed.

## Session 2026-09-01 — suspend failed RERP HelmReleases

**Done (cluster):**
- `kubectl`/`MCP`: `rerp/general-ledger` and `rerp/invoice` HelmRelease `spec.suspend: true`
- Manifests edited in rerp:
  - `deployment-configuration/profiles/dev/rerp/accounting/services/general-ledger.yaml`
  - `deployment-configuration/profiles/dev/rerp/accounting/services/invoice.yaml`

**Not done (git):** agent Shell broken (`spawn EBADF`). Run on ms02:

```bash
ssh -o ControlMaster=no ms02 'cd ~/Workspace/microscaler/rerp && git add deployment-configuration/profiles/dev/rerp/accounting/services/general-ledger.yaml deployment-configuration/profiles/dev/rerp/accounting/services/invoice.yaml && git commit -m "Suspend failing general-ledger and invoice HelmReleases." && git push -u origin HEAD && git rev-parse HEAD'

ssh -o ControlMaster=no ms02 'cd ~/Workspace/microscaler/shared-gitops-k8s-cluster && git status -sb && git add -A && git commit -m "Suspend hauliage/rerp Flux sync and ship Flux Status Web UI." && git push -u origin HEAD && git rev-parse HEAD'
```

## Done (interim snapshot)

- **Parity doc** `docs/spark-parity-pre-stack.md` — referenced as gate before stacking; updated for **`provision_sparks.yml`**.
- **Canonical Spark playbook:** `playbooks/provision_sparks.yml` + role **`spark_provision`** (sudoers, apt, docker, firewall, cuda, vllm, optional stack, HF, optional docker vLLM, diagnostics).

- **2026-08-27 (Spark DeepSeek + HF prune):** Added first-party Ansible — role
  `hf_cache_prune` / `playbooks/prune_hf_cache.yml`; role `vllm_dspark_stacked`
  (Anemll image, mp TP=2, own `dspark-serve.sh.j2`, vendored hotfixes); inventory
  `spark_llm_profile` + keep list; mutual teardown + `spark_assert` profile gates;
  just recipes `spark-hf-prune*`, `spark-profile-qwen|deepseek`; docs
  `spark_llm_profiles.md`. Qwen inventory preserved for switch-back.
- **vLLM single-node path:** `roles/vllm` (`serve.yml`, `vllm.service`, `run-vllm-api.sh`) — toggled via **`spark_provision_vllm_serve`** / **`vllm_sparks_deploy_single_node_service`**.
- **Two-node stack automation:** same playbook, **`spark_provision_vllm_stack`** (interconnect facts → stop single-node `vllm` on leader → Ray head → Ray worker → `vllm-stacked`).
- **Verify Ray:** `spark_provision_verify_ray` + **`provision_sparks.yml --tags verify`** (`ray status` from leader with `RAY_ADDRESS`).
- **Docs:** `README.md`, `docs/provision_sparks.md`, `docs/vllm-multi-node.md`, `docs/PRD-spark-stacking-nvidia2.md` — align with single entry playbook where updated.
- **Ansible UX:** `roles/vllm/tasks/stack_vllm.yml` — `systemd` `no_block: true` for `vllm-stacked` start.
- **Ray pin:** `inventory/group_vars/sparks.yml` — `vllm_ray_package` set (verify version matches cluster policy).

## Not done / verify on cluster

- **PRD M3:** End-to-end validation — OpenAI chat completion through leader with TP=2 on real hardware; document any model-specific limits (memory).
- **PRD top status:** Move to **Accepted** after M3 and any follow-up tickets from §5 backlog.
- Optional: assert two alive nodes in `ray status` output when **`spark_provision_verify_ray`** runs.

## Next actions when resuming

1. **PRD M3 (definition of done):** One successful **OpenAI chat completion** against the **leader** `vllm-stacked` API with **TP=2** (model sized for 2× device memory). Example: `curl` to `http://<leader>:8080/v1/chat/completions` with `vllm_default_model` from `sparks.yml`.
2. After **APT full-upgrade** (especially kernel/NVIDIA): **reboot** Sparks if required, then `ansible-playbook playbooks/provision_sparks.yml` (or `--tags vllm_stack` if only stack units need refresh) to bring **ray-head / ray-worker / vllm-stacked** back cleanly.
3. `ansible-playbook playbooks/provision_sparks.yml --tags verify` with **`spark_provision_verify_ray: true`** — confirm Ray cluster; if **stale nodes** appear in `ray status`, stop units and clear: `sudo systemctl stop vllm-stacked ray-head` (leader), `sudo systemctl stop ray-worker` (follower), then `sudo -u nvidia /opt/vllm/venv/bin/ray stop -f` on both, re-run stack phase.
4. If API missing: `journalctl` on leader/follower per `docs/vllm-multi-node.md`.

## Session notes

- **2026-09-15 (Massive raw mirror):** Python syncer + tests +
  `just massive-mirror-*`. Dest `gs://microscaler-massive/raw/`.
  Creds from GCP Secret Manager at runtime (no env file). Parquet later.
  VM install + first sync not done.
- **2026-09-14 (Mac/Parallels WAN):** Recent ms02 UFW 3260 / Ansible
  persist did not steal Mac default route. USB `en20` (service #1)
  flapped 10baseT → inactive; that is the likely shared failure for
  Mac + Parallels Shared NAT.
- **2026-09-14 (what to persist):** iSCSI 3260 already lives in
  shared-gitops `multipass_bridge_firewall` (`just cluster-edge-apply`).
  cylon-local now also has `firewall_trusted_bridge_*` (3260 only).
  Other live-not-in-cylon-local: Tilt Anywhere UFW forwards; LIO
  `target.service` failed on boot (rtslib restore succeeded). Do not
  duplicate the full CSI set here.
- **2026-09-14 (what to persist):** iSCSI 3260 already lives in
  shared-gitops `multipass_bridge_firewall` (`just cluster-edge-apply`).
  cylon-local now also has `firewall_trusted_bridge_*` (3260 only).
  Other live-not-in-cylon-local: Tilt Anywhere UFW forwards; LIO
  `target.service` failed on boot (rtslib restore succeeded).
- **2026-09-07 (UMA applied):** Tagged recover + Qwen Ray recreate.
  swappiness 0, swap 0B both hosts, assert gate on. Warm decode 13.1
  tok/s (was 4.7). Playbook recap nvidia1 55/11 nvidia2 43/10, failed=0.
- **2026-08-30 (back to Qwen3.8):** DeepSeek/GLM parked as too new. Profile
  `qwen38`. Hub trees deleted (156G DeepSeek both hosts); GLM sm121 image
  removed. Prefetch is TinyLlama + Qwen3.8. Live `:8000` smoke `QWEN38_OK`.
- **2026-08-29 (GLM ditched, DeepSeek queued):** GLM `/health` wait killed;
  `vllm-glm53-*` removed; 182G GLM hub deleted (nvidia2 only had 168G free).
  DeepSeek was not on disk. Prefetch of `DeepSeek-V4-Flash-0731` started
  (~13 MB/s). Qwen3.8 Ray restored on `:8000` (assert + smoke pass). Inventory
  intent is `deepseek_v4_flash`. Anemll image pull in flight.

## Session notes (older)

- **2026-08-25 (Zot retention drift diagnosed):** Confirmed Zot is separate
  from the 208 GiB host Docker dataset: it uses Kubernetes ZVOL `pvc-b901...`
  (34.2 GiB ZFS allocation, ~21 GiB mounted files). Zot v2.1.17 GC is working
  (2,528 successful runs, 3,937 blobs and 1,593 manifests removed). The live
  ConfigMap is wrong: retain five `dev-\d+` tags, then retain all other tags via
  an unlimited `.*` rule. Actual excess includes 36 `cylon-daemon` tags, two
  Sesame repos with seven, and three PriceWhisperer repos with six. The local
  GitOps manifest has the intended single hard-cap rule
  `mostRecentlyPushedCount: 2`, but it is uncommitted. Flux cannot advance
  `stack-cluster` because reconciliation fails on immutable `zot-data`
  `spec.volumeName` drift after the PVC was recreated. No cleanup performed.
- **2026-08-25 (ZFS reclaim audit):** Ethernet restored stable Mac-to-ms02 RTT
  (0.82/1.05/1.59 ms over `en20`). Audited the healthy but 87%-allocated rpool
  without deleting data. Found roughly 772 GB of regenerable Rust target
  directories, with no active cargo/rustc/sccache processes. Docker reports
  288.9 GB logical reclaimable from 697 unused volumes; its compressed ZFS
  dataset occupies 208 GiB. Two orphaned ZFS iSCSI datasets have no PV or open
  session but retain targetcli entries and consume 96.9 GiB total. Rotated
  syslogs consume about 22 GiB. Multipass's 75 GiB belongs to active VMs and is
  not a default cleanup candidate. Additional user-review candidates include a
  60.7 GiB Hugging Face cache and 47.5 GiB `backup_mac`. No cleanup performed.
- **2026-08-25 (NFS slowdown isolated to Mac Wi-Fi):** The Mac currently has no
  active NFS mount or TCP/2049 session; `~/Workspace/remote` is the unmounted
  root-owned stub. Repeated tests showed the same periodic stalls from the Mac
  to both ms02 (2.3/29.9/221 ms) and the router (2.4/28.2/216 ms), while wired
  ms02-to-router latency was stable at 0.72/0.88/1.68 ms. The Mac's 5 GHz link
  has strong signal (-44 dBm) and an 866 Mbps PHY rate, but likely suffers
  radio/AP airtime, roaming/scanning, or AWDL/Continuity stalls. Bulk SSH
  throughput remained about 214 Mbps, explaining why large transfers work
  better than RTT-heavy NFS metadata. Server checks were healthy: NFS active,
  TCP/2049 listening, zero bad RPC calls, 1 Gbps full-duplex eno4 with zero
  errors/drops, healthy ZFS pools, and sub-millisecond storage latency. rpool is
  88% allocated and 58% fragmented but did not show an active I/O bottleneck.
- **2026-08-02 (NVFP4 archive storage retired):** Verified
  `gs://microscaler-huggingface/models/RedHatAI/Kimi-K3-NVFP4/71eed69448bc5ee7f83c4c08a61d6598a25b6ae4`
  contains 1,557,434,547,173 bytes. Disabled the watcher timer, stopped
  `hf-archive-downloader`, detached `hf-archive-data`, and deleted the 3 TiB
  disk. The terminated VM and its 30 GiB boot disk remain. A future run must
  create and attach a replacement disk as `archive-data` before starting the
  VM.
- **2026-08-01 (RedHatAI Kimi K3 NVFP4 archive started):** Per operator
  direction, disabled the watcher, stopped `hf-archive-downloader`, resized
  `hf-archive-data` from 2 TiB to 3 TiB, restarted the VM, and grew ext4 online.
  Removed the incomplete 1.1 TiB Unsloth MXFP4 partial. Replaced the exact
  backstop policy with `RedHatAI/Kimi-K3-NVFP4`, requiring RedHatAI publisher,
  Kimi K3 metadata, `moonshotai/Kimi-K3` lineage, compressed-tensors
  `nvfp4-pack-quantized`, safetensors, and a valid immutable SHA. Deployed the
  watcher, re-enabled its timer, and started the pinned
  `71eed69448bc5ee7f83c4c08a61d6598a25b6ae4` download with two workers. The
  3 TiB filesystem has approximately 2.9 TiB free and the service is active.
  Twenty tests pass; prior branch-aware coverage remains 87%.
- **2026-08-01 (Kimi NVFP4 archive diagnosis):** Confirmed no Kimi objects are
  in GCS and watcher state is empty. The current implementation explicitly
  rejects NVFP4 and therefore never selected `RedHatAI/Kimi-K3-NVFP4`.
  Instead, the old `unsloth/Kimi-K3` MXFP4 backstop is stalled at about
  1.1 TiB: 916.8 GB remains, but only 923.8 GB is free and the 20 GiB reserve
  blocks each timer retry. Inspected the requested RedHatAI model: Kimi K3,
  `moonshotai/Kimi-K3` lineage, compressed-tensors NVFP4, 83 safetensor shards,
  SHA `71eed69448bc5ee7f83c4c08a61d6598a25b6ae4`, and 100 files / 1.6 TB by
  pinned dry run. Awaiting an explicit choice to delete the incomplete Unsloth
  snapshot and switch, resize storage and preserve both, or leave it unchanged.
- **2026-08-01 (Sunshine enabled on trusted home LAN):** Diagnosed local
  Moonlight failure while all Sunshine services were healthy. UFW exposed the
  streaming ports only to picolino's WireGuard `10.77.0.3/32`, so a Mac using
  its `192.168.1.x` home address was correctly blocked. Added the Sunshine TCP
  and UDP rules to trusted `192.168.1.0/24`, retained the existing WireGuard
  rules, and kept public access closed. Added both LAN and VPN Web UI origins
  to `csrf_allowed_origins`, changed `just sunshine-web` to the LAN URL, added
  `just sunshine-web-vpn`, and updated documentation. Deployed through Ansible;
  all three services are active, UFW shows all seven LAN rules, and a direct
  Mac HTTPS probe returns HTTP 401 as expected. Nine tests and syntax check
  pass. Use Moonlight host `192.168.1.189` at home and `10.77.0.2` away.
- **2026-07-29 (backstop download resumed):** Diagnosed why no Kimi archive had
  completed. `unsloth/Kimi-K3` reached 931 GiB, then every timer retry failed
  the free-space guard: 1.0 TB remained to download, but the 20 GiB reserve
  could not fit while the completed Qwen test still occupied 67 GiB locally.
  Confirmed the local and GCS Qwen snapshots matched exactly at
  71,926,880,713 bytes, removed only the local Qwen copy, and recovered enough
  space. Reset the failed unit and resumed the pinned Unsloth download with two
  workers. The service is active and the partial tree is advancing; no Kimi
  snapshot is complete in GCS yet.
- **2026-07-28 (Unsloth Kimi K3 backstop started):** Downsized
  `hf-archive-downloader` from `e2-standard-4` to the accepted `e2-medium`
  shape (2 visible vCPU, 4 GiB RAM); GCE normalized the requested
  `e2-custom-1-4096` to this machine type. Disks and watcher state were
  preserved. Added an exact, metadata-constrained exception for
  `unsloth/Kimi-K3`: Kimi K3 architecture, `moonshotai/Kimi-K3` lineage,
  compressed-tensors MXFP4, safetensors, and immutable SHA pinning. Its dry run
  is 121 files / 1.6 TB at SHA
  `6095bb6b51abc90e7e8f8804ad4f65be4a441262`. It is a temporary backstop and
  does not satisfy the FP8 goal. Download is active and resumable; worker
  concurrency was reduced from four (3.0 GiB peak RAM) to two (2.4 GiB observed)
  for safe headroom on the 4 GiB VM. Verification observed 28 GB downloaded.
  Twenty tests pass at 87% coverage.
- **2026-07-28 (first Kimi K3 FP8 candidate assessed):**
  `RedHatAI/Kimi-K3-FP8-BLOCK` is a credible NVIDIA FP8 candidate but is not
  accepted by the current Moonshot-only publisher policy. Metadata confirms
  Kimi K3 architecture, `moonshotai/Kimi-K3` lineage, compressed-tensors
  block FP8, predominantly F8_E4M3 tensors, 96 safetensor shards, and SHA
  `a0dd16556a81f79acee4e576e88aae2e356472f1`. An exact pinned dry run reports
  118 files / 2.8 TB, exceeding the VM's 2 TiB disk. Recommended next action:
  explicitly trust this repository, resize the transient disk to 4 TiB, and
  archive the pinned snapshot; otherwise continue waiting for Moonshot. The
  operator chose to ignore this build for now because it is too large for the
  target hardware. No infrastructure or watcher policy was changed.
- **2026-07-27 (Kimi K3 sequential archive watcher):** Resized
  `hf-archive-data` and its ext4 filesystem to 2 TiB. Added
  `scripts/hf_archive_watcher.py`, a CLI-driven systemd oneshot that polls the
  verified `moonshotai` organization every 15 minutes. It normalizes Kimi K3
  naming variants, detects FP8 and FP16/BF16 primarily from safetensor parameter
  metadata, rejects incompatible quantizations/formats, and pins every selected
  repository to an immutable SHA. It downloads FP8 first, serially checksum-
  syncs to GCS, verifies with a dry rsync, removes only the verified local
  snapshot, then permits FP16/BF16. `hf-archive-watcher.timer` is enabled on the
  VM; the first two live polls completed successfully and found no K3 FP8
  candidate. Added operator recipes and `docs/gcp-hf-gce-archive.md`.
  Eighteen tests pass with 87% coverage. The independent Qwen test upload
  remains active with GCS process/thread concurrency 1/1.
- **2026-07-27 (GCE Hugging Face preservation path):** Rejected the Cloud
  Run/FUSE prototype after real Qwen traffic showed managed GCS FUSE falling
  back to local staged writes. Removed its job, image repository, partial
  objects, and repository implementation. Enabled Compute Engine and deployed
  `hf-archive-downloader` (`e2-standard-4`, Ubuntu 24.04,
  `europe-west1-b`) with IAP-only SSH, a 30 GB boot disk, and an auto-deleted
  250 GB standard persistent ext4 disk at `/srv/hf-archive`. The runtime
  identity `hf-archive-vm-runner` has object-user access only to the
  EU multi-region Archive-class `microscaler-huggingface` bucket. Pinned
  `huggingface_hub` 1.24.0 in `/opt/hf-archive-venv`. Qwen
  `Qwen/Qwen3.6-35B-A3B` dry-run reported 40 files / 71.9 GB; local download
  completed successfully in 21 minutes (67 GiB on disk). Bucket rsync is
  running as `hf-qwen36-upload-serial.service` with one process and one thread;
  transfer time is not critical after local verification. Verify with a
  checksum-only dry rsync after completion. For Kimi, wait for verified
  Moonshot repo IDs and immutable
  SHAs, dry-run exact FP8 and FP16/BF16 sizes, grow the data disk, download to
  ext4, upload, verify, then destroy VM and disk.
- **2026-07-27 (Sunshine/Moonlight remote desktop):** Pivoted away from GNOME
  RDP and deployed `sunshine_remote_desktop` on ms02. It provides a dedicated
  2560x1440 Xorg/XFCE desktop without GDM autologin or a physical display,
  captures through X11, and hardware-encodes through Intel VA-API. The official
  `v2026.516.143833` Debian Trixie package is checksum-pinned; Ubuntu 24.04
  failed safely on missing ICU 74 and Ubuntu 26.04 requires glibc 2.43 / ICU
  78, while Trixie matches Ubuntu 25.10's glibc 2.42 / ICU 76. Three systemd
  services are enabled/active and restart on failure. UFW allows Sunshine only
  from picolino's WireGuard `10.77.0.3/32`; UPnP is disabled. Verified Web UI
  HTTP 307 over `10.77.0.2:47990`, connected DUMMY0 at 2560x1440/59.95 Hz, and
  H.264/HEVC/AV1 VA-API encoders. Seven tests, syntax, dry run, and idempotence
  (`changed=0`) pass. Pairing remains manual: install Moonlight, create Web UI
  credentials, add `10.77.0.2`, approve PIN, and launch Cursor.
- **2026-07-25 (on-demand Mac WireGuard peer):** Deployed picolino
  `10.77.0.3/32` through the Hostinger hub. Added split routes only for the
  mesh, ms02 `/32`, and MetalLB bridge; a root-owned toggle swaps the LAN
  LaunchDaemon route and WireGuard route safely. Added ms02 UFW, routed
  forwarding, and NFS `/32` policy. Docker's VPS FORWARD drop was fixed with a
  persistent `wg0→wg0` rule. Final down/up test passed: LAN route restoration,
  `10.77.0.2`, Kubernetes API `10.177.76.137:6443`, ingress
  `10.177.76.234:443`, DNS, and NFS. 25 tests pass at 94% total coverage.
  Tunnel left active; NFS unmounted before away-network switch; reverse SSH
  fallback active. **Away test on A1 Wi-Fi passed:** WireGuard re-handshook
  automatically, split routes stayed on utun6, K8s API returned 401 as
  expected without credentials, Hauliage ingress returned 200, DNS resolved
  through ms02, NFS mounted/read successfully, and reverse SSH remained usable.
- **2026-07-25 (always-on WireGuard Linux leg):** Added and deployed
  `playbooks/wireguard_mesh.yml` plus role `wireguard` for Hostinger
  (`10.77.0.1/24`, public UDP 51820) and ms02 (`10.77.0.2/32`, 25-second
  keepalive). Private keys are generated and retained on each host. Both
  `wg-quick@wg0` and `wireguard-healthcheck-wg0.timer` are boot-enabled; the
  two-minute healthcheck restarts a missing/stale interface. Verified current
  bidirectional handshakes/pings, recovery after stopping each interface,
  reverse-SSH fallback, check mode, 98% healthcheck coverage, and idempotence
  (`changed=0`). Mac peer is intentionally deferred as a separate toggle.
- **2026-07-16 (LAN edge → Ansible):** Audited ad-hoc ms02/Mac state (dnsmasq, `/etc/resolver`, UFW DNS/haproxy/k8s, MetalLB L3). Captured: roles `metallb_lan_route` + `dev_dns`, firewall LAN rules, Mac resolver/route tasks. Applied ms02 (`just metallb-lan-route-ms02`, `just dev-dns-ms02`). Mac still needs interactive `just mac-dev-dns` + `just metallb-lan-route-mac`. HAProxy `lan-proxy` remains shared-k8s. Prefer Ansible over `shared-k8s just dev-dns-*` (overwrite risk). Audit: `llmwiki/concepts/ms02-lan-edge-ansible.md`.
- **2026-06-14 (HF prefetch):** Paused Kimi (~554 GiB) and MiniMax (~796 GiB NFS) prefetches. Switched queue to **Qwen/Qwen3-Coder-30B-A3B-Instruct** (~57 GiB MoE). All three configured models **ready** on nvidia1+nvidia2. Partial Kimi cache removed. Inventory updated in `sparks.yml`; config pushed via `spark_hf_nfs.yml --tags hf_prefetch --skip-tags nfs_client`.
- **2026-05-20 (torchrun migration):** Ray API root cause = `pgrep -af 'vllm serve'` self-match in `vllm_stacked_container` (fixed `[v]llm serve`, commit `41bfabc`). Torchrun role hardened: leader-first, headless follower, master-addr, /v1/models wait, defensive docker rm + run_once pause (`156c30c`). Qwen patch no longer aborts provision on pip SIGKILL (`0c51b68`). **Cluster:** inventory `vllm_stack_kind: torchrun`, image `26.03-py3`; torchrun containers crash-loop (~2 min), `/v1/models` down. Parallel provision runs caused Ray↔torchrun flips and mid-playbook container deletion — operator must run one reconcile at a time. **Before 8-Spark add:** green `/v1/models` on 2-Spark torchrun (or validated Ray rollback), 26.04-py3 bump, spark_assert green, HF cache parity nvidia2.
- **2026-05-20:** **GPU clock cap 2000 MHz** — `roles/spark_gpu_clock` locks GB10 graphics clocks via `nvidia-smi -lgc 2000,2000` + systemd `cylon-gpu-clock-lock.service` on boot. Inventory: `spark_gpu_lock_clocks_enabled: true`, `spark_gpu_locked_graphics_clock_mhz: 2000`. `just spark-gpu-clock-apply` / `--tags spark_gpu_clock`. nvidia2 rebooted onto `6.17.0-1018-nvidia` without `linux-modules-nvidia-580-open-6.17.0-1018-nvidia` (stale 1008 modules blocked apt). Fixed on host; `spark_kernel` now detects missing module packages, purges stale nvidia module debs, and runs `nvidia-smi` when pin==running kernel. `spark_assert` uses `ray list nodes` (not `ray status` for IP grep); fixed NCCL container-name Jinja ternary; added `nvidia-smi` gate. in `inventory/group_vars/sparks.yml`. `roles/spark_kernel` now apt-installs the pinned image when missing (temp unhold + `allow_change_held_packages`). Apply: `just spark-kernel-apply` then `just spark-reboot`. `apt changelog linux-image-unsigned-6.17.0-1018-nvidia-64k` on gx10-47b5. **Running:** `6.17.0-1008-nvidia` (4K); **installed but not booted:** `1014`, `1018-nvidia`, `1018-nvidia-64k`. The **64k** image is a **64K page-size** build (`CONFIG_ARM64_64K_PAGES=y`), not interchangeable with fleet pin `1008` (4K). **1018-only fixes:** DGX Spark iGPU IOMMU quirk (LP #2150487), SMT asym-capacity scheduler (LP #2150671), Olympus perf PMU skip (LP #2149758), BMC IOMMU identity domain (LP #2150470), MPAM bandwidth hard-limit (LP #2150290), CXL/NPEM/fwctl (LP #2149918), CVE-2026-31431. **Cumulative since 1008 (high-signal):** RoCE stale GIDs on netdev events (1017, LP #2148311), r8169 vs r8127 Spark Ethernet contention (1017/1016, LP #2144345), ucsi/TBT5 power-plug boot hang (1016), NVFS SGL pool overrun (1016), CXL Type-2 + large CXL stack (1017). **1014** was AppArmor-only — no Spark platform fixes; aligns with prior crash bisection. **No explicit GX10 abrupt power-off fix** in 1008→1018 changelog. Hold pin at 1008 until symmetric A/B on `1018-nvidia` (4K, not 64k).
- **2026-05-20:** **Canonical provision:** `just spark-provision` = full `provision_sparks.yml` + `spark_assert` gate. Retired `cutover_roce.yml`, `refresh_hf_prefetch.yml`.
- **2026-04-11:** **nvidia1 Ray + vLLM** — **Stale GCS** fixed by reset. **Ansible `--tags vllm_stack`** skipped inner stack tasks until roles used **tagged blocks**. **`ufw`** only allowed **22/6379/8080** while Ray uses **many TCP ports** on the interconnect (`ss` showed raylets on e.g. **10002–10022**, **42xxx**) — GCS health checks to **nvidia2** failed → “marked as dead”. **Fix:** **`ufw allow from 169.254.0.0/16`** (link-local QSFP) + **`ray start`** **`--min-worker-port 10002`–`--max-worker-port 19999`** (avoids **10001** client_server and avoids huge range colliding with Ray’s other dynamic ports). Post-deploy **`ray status`:** **2** Active nodes, **`0.0/2.0` GPU**, no pending demands. **vLLM:** long **`sleep`** before API; **8080** after engine ready.
- **2026-04-09:** Playbook consolidation — single **`provision_sparks.yml`**; removed legacy `vllm_*_sparks.yml`, `apt_upgrade_sparks.yml`, `docker_hosts.yml`, probe playbooks; docs and memory bank updated.
- **APT:** `provision_sparks.yml` with `spark_apt_upgrade_mode=full` **completed** by operators — treat as parity maintenance; reboot if upgrades demanded it, then restack as above.

- **2026-05-20 (torchrun HF cache):** nvidia1 `models--Qwen--Qwen3.6-35B-A3B-FP8` was **root-owned** under `/home/nvidia/.cache/huggingface/hub` (broken offline load for non-root / container). **Fix:** `chown -R nvidia:nvidia` on that tree; **hf-prefetch** state `ready` (~37GB); snapshot `95a723…` has `config.json` + 42 FP8 shards on **nvidia1+nvidia2**. `get_model_path` + `snapshot_download` OK offline in torchrun env after fix. **Stack:** `just spark-provision --tags hf_prefetch,spark_assert` run; torchrun relaunch via `-e vllm_stack_kind=torchrun` + `vllm_torchrun_stack` (inventory on disk still `ray` until committed). **API:** `/v1/models` **not 200** yet — leader torchrun container up, log at pip/fastsafetensors + weight load. Use `just spark-vllm-logs-serve` after setting `vllm_stack_kind: torchrun`.
- **2026-09-09 (Zot/iSCSI recovery):** Zot was down after all Multipass
  workers rebooted at 02:37. The ms02 LIO target listened on 3260 but did not
  answer worker SYNs, and democratic-csi retained hung NodeStageVolume locks.
  Confirmed Zot's current 60 GiB PVC (`pvc-ca98...`) was not full: 9.2 GiB
  ext4 used (16%). Reloaded UFW, restarted `rtslib-fb-targetctl` with zero
  sessions, rolled the CSI node DaemonSet, and recreated Zot. Registry
  endpoints and Flux `stack-cluster` recovered. The checked-in policy was
  found to retain five dynamic builds, not two. Updated the local GitOps source
  (uncommitted) to keep the two newest `dev-*`/`tilt-*` tags plus stable aliases.
  A temporary count=2 startup GC pruned some excess tags, then Flux correctly
  reverted live state to committed count=5. Durable policy repair still needs
  an explicit commit/push. Remaining Airflow/Hauliage/RERP pull failures are
  missing tags on the current Zot PVC and require image rebuilds/pushes.
- **2026-09-14 (PostgreSQL replica reseed):** Both Bitnami PostgreSQL 15
  replicas were ~24 days behind and could not retrieve removed WAL segments
  (`1/A2...` and `1/A0...` versus primary `65/AA...`). Rebuilt them
  sequentially in place: scaled each Deployment to zero, used a one-shot
  BusyBox pod to remove only the PVC's `/data/data`, then scaled up and allowed
  `pg_basebackup` to clone from the primary. Preserved both existing 10 GiB
  ZFS/iSCSI PVCs. Final primary `pg_stat_replication`: two asynchronous
  `streaming` walreceivers, both flush/replay at `65/AF098568`, zero byte lag.
  Replica logs confirm completed base backups and clean WAL streaming. The
  primary Service remains hard-selected to `app=postgres-primary`; failover is
  manual despite restored standby copies.
- **2026-09-25 (NFS route blip):** `just nfs-up-resvport` reported no route
  to `192.168.1.189:2049`. Server exports, listeners, and the LAN UFW 2049
  rule were intact. After the Mac route returned on `en0`, the resvport mount
  and a remote directory read succeeded.
- **2026-09-26 (GitHub runner pool):** Uncordoned `k8s-runner-1` and
  `k8s-runner-2` (VMs were already running; nodes had been
  `SchedulingDisabled` since 2026-08-28). Runners 3–5 stay cordoned. ARC
  runner `microscaler-5t4qk-runner-4xp8g` is 2/2 on runner-1; listener and
  `dind-registry-proxy` are Running. iSCSI 3260 was dropped because live
  `ufw-user-input` was empty; `ufw reload` restored it and Zot remounted.
  DinD now uses `--insecure-registry` for `10.177.76.220:5000`, and Zot
  `http.compat` is `docker2s2` so `docker push` of Docker manifest v2 is
  accepted. Proved push and pull of a scratch probe image, then deleted the
  tag. All five runner nodes got the HTTP containerd mirror for that
  registry. Those Zot edits are on `shared-gitops` `main` as `bc346d6`.
- **2026-10-01 (Zot iSCSI mount lock):** Zot pod was Unknown with
  `operation locked` on `pvc-ca98cc93` (zfs-iscsi, not NFS). Live iptables
  was missing the configured TCP 3260 allow from `10.177.76.0/24`, so
  `iscsiadm` logins timed out. `ufw reload` restored the rule; force-deleted
  CSI node pods stuck on zombie `iscsiadm`. Zot `zot-54b4db8ddd-xjwzp` is
  Running on `k8s-cp-1` and `/v2/` returns 200.
- **2026-11-02 (vLLM did not return after ms02 power loss):** spark1, spark2, and spark4 are off the LAN. Flash-Next stayed Terminating because a graceful delete never finishes without the kubelet, and LeaderWorkerSet will not replace that ordinal. Pods were force-deleted and are Pending. `fabric-hosts` is on `shared-gitops` `main` (`2926698`) and Flux applied it. After the hosts returned, Flash-Next crash-looped on a RoCE GID mismatch (spark1 index 4, spark2 index 3). `7aa69ca` pins the leader to 4. A Hermes-style gateway completion then returned `pong`.
- **2026-10-31 (NCCL on nvidia3/nvidia4):** `playbooks/nccl_sparks.yml -l nvidia3,nvidia4` built NCCL `v2.28.9-1` and `all_gather_perf` passed (0 wrong, bus bandwidth 11.88 GB/s). Cable is Cage B. Link-local is `169.254.211.40` (nvidia3) and `169.254.196.121` (nvidia4) on `enp1s0f1np1`. Test HCA is `rocep1s0f1` only; `roceP2p1s0f1` has no IPv4. Intended serving is DeepSeek-V4.1-Flash-1017 on sparks 3–6, with 7–8 reserved for vector 1 training. nvidia5–8 are not inventoried yet.
- **2026-10-31 (fleet kernel + nvidia1/nvidia2 addresses):** All four Sparks
  run `7.0.0-1024-nvidia`. nvidia1 is `192.168.1.110` (`spark1`), nvidia2
  is `192.168.1.120` (`spark2`). Mac `/etc/hosts` is still the old pair
  until `playbooks/mac_workstation.yml` is applied. `roles/mac_sudoers`
  now grants the operator `NOPASSWD: ALL` so that apply does not keep
  asking for a password; the sudoers tag itself still needs the password
  once, because the drop-in is not on disk yet.
- **2026-10-31 (nvidia3/nvidia4 joined):** LAN is `192.168.1.130` (`spark3`,
  kernel `7.0.0-1024-nvidia`) and `192.168.1.140` (`spark4`, kernel
  `7.0.0-1019-nvidia`). Provision `failed=0`. Docker 29.6.2 client was
  installed but the daemon was dead (`buildkit: invalid database`, then
  start-limit). Cleared `/var/lib/docker/buildkit` and taught `roles/docker`
  to recover that before asserting the engine. Both nodes are Ready on
  k3s `v1.36.2+k3s1` with the GPU NoSchedule taint, slots 3 and 4, fabric
  `crs804`. `just cylon-node-up` still exits 2: the derived vLLM image is
  not in containerd, and the NGC base is not in Docker (only
  dcgm-exporter). `nvidia1`/`nvidia2` stay on `.104`/`.229`. QSFP host
  aliases stay off until those links have addresses.
- **2026-09-14 (OpenSearch TTL recovery):** OpenSearch reached flood stage at
  56/59 GiB (95%), applying read-only blocks and causing Data Prepper HTTP 429
  plus buffer exhaustion. The seven-day ISM policies existed but could not
  advance while `.opendistro-ism-config` was blocked. Deleted expired daily
  metrics/log indices from 2026-08-29 through 2026-09-04. Raw span retention
  was structurally ineffective because its delete transition followed a
  size-only 50 GiB rollover; manually rolled `otel-v1-apm-span` to `000021`
  and deleted expired non-write `000001`/`000020` (11.8 GiB). Cleared all
  flood blocks. Final disk use is about 1/59 GiB (3%); fresh metrics/logs are
  indexing and the DataPersistence query sees 90 recent PostgreSQL metrics.
  Added an uncommitted TDD fix in `shared-gitops-k8s-cluster` to roll spans
  daily or at 5 GiB; targeted test passes. Full test file has an unrelated
  existing heap expectation mismatch. The fix requires explicit commit/push
  before the ten-minute provisioner stops restoring the old policy.
