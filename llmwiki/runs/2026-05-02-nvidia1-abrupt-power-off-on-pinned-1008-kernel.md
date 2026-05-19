---
title: nvidia1 abrupt power-off on the pinned 6.17.0-1008-nvidia kernel — kernel-as-cause hypothesis FALSIFIED
kind: run
status: shipped
tags: [gx10, abrupt-power-off, kernel, bisection, hypothesis-falsification]
date: 2026-05-02
related:
  - runs/2026-05-02-kernel-pin-locked-bisection-evidence.md
  - runs/2026-05-01-nvidia2-abrupt-power-off-vllm-long-context.md
  - concepts/sparks-observability-pipeline.md
---

# nvidia1 abrupt power-off on the pinned 6.17.0-1008-nvidia kernel

## TL;DR

`nvidia1` had an **abrupt power-off at 09:51:05 EEST on 2026-05-02**,
**22h 15min after boot, on the pinned `6.17.0-1008-nvidia` kernel**,
**while essentially idle** (1 vLLM request, GPU 67 W, 78°C, KV cache
0.87 %, all RAS counters zero, no kernel signal in journald). The
[kernel hypothesis](./2026-05-02-kernel-pin-locked-bisection-evidence.md#hypothesis-status)
filed earlier today is therefore **falsified** — at least as a
*sole* cause.

`nvidia2` did NOT crash; uptime 22h 31min and counting on the same
pinned kernel.

The signature exactly matches NVIDIA forum thread #359785: spontaneous
abrupt power-off on a GB10 SoC platform with no software-visible
precursor. **This is hardware/platform, not software.**

The forensic capability we built yesterday
([sparks-observability-pipeline](../concepts/sparks-observability-pipeline.md))
worked *exactly as designed* — it captured the 30 minutes preceding
the crash across GPU/SoC/RAS/vLLM/journald axes, and that record is
what makes this run page possible.

## Timeline

| Time (EEST) | What |
|---|---|
| **2026-05-01 11:35:47** | nvidia1 booted on `6.17.0-1008-nvidia` (manual `grub-reboot` from earlier in the recovery sequence) |
| **2026-05-02 09:39** | operator declared the bisection signal strong; ran `just spark-kernel-apply` to durably lock the pin |
| **2026-05-02 09:51:05** | nvidia1 abrupt power-off (22h 15min uptime). Last journal entry mid-line; no shutdown sequence. |
| **2026-05-02 09:58:04** | operator powered nvidia1 back on; **booted on `6.17.0-1008-nvidia`** (GRUB pin held) |
| **2026-05-02 10:06** | operator notices `dpkg -l ... \| grep '^ii'` doesn't show 1008, raises concern about pin loss |
| **2026-05-02 ~10:10** | confirmed: 1008 IS still installed (dpkg state `hi` = hold/installed; grep `^ii` hides held packages); pin durably in place; **22h ago started, did not prevent crash** |

**nvidia2 did not crash** — current uptime 22h 31min on the same
pinned kernel.

## Forensic data — the 30 minutes preceding the crash

Captured by the
[GX10 abrupt-power-off hunt dashboard](http://192.168.1.189:3000/d/gx10-power-off-hunt)
(Prometheus + Loki, both populated by the spark_observability
pipeline). All queries `@ 1777704665` (UTC `06:51:05` = EEST
`09:51:05`).

| Source | Metric | Value during 30-min window |
|---|---|---|
| node_exporter | `up{host="nvidia1"}` | **1** for all 125 samples (15 s × 31 m). Cluster healthy until the cut. |
| dcgm-exporter | `max_over_time(DCGM_FI_DEV_POWER_USAGE[35m])` | **66.95 W** (idle. ~28% of GB10's ~240 W TGP) |
| dcgm-exporter | `max_over_time(DCGM_FI_DEV_GPU_TEMP[35m])` | **78°C** (below 85°C concern threshold) |
| vLLM `/metrics` | `max_over_time(vllm_num_requests_running[35m])` | **1** |
| vLLM `/metrics` | `max_over_time(vllm_num_requests_waiting[35m])` | **0** |
| vLLM `/metrics` | `max_over_time(vllm_kv_cache_usage_perc[35m])` | **0.0087** (= 0.87 %) |
| rasdaemon (textfile) | `rasdaemon_events_total{*}` | **all zero** across all 8 categories: `memory_ce`, `memory_ue`, `pcie_aer_correctable`, `pcie_aer_uncorrectable`, `pcie_aer_fatal`, `mce_records`, `extlog_records`, `devlink_records` |
| Loki (journald) | grep `(?i)(mlx5\|MCE\|panic\|aer\|oom\|hardlockup\|softlockup\|nvidia.*xid\|vbios\|gpu fallen\|uncorrected\|reset)` over the 22h 15min boot | **zero matches**. (One `mlx5_core ... Link down` event at boot startup 2026-05-01 11:35:55 — expected during interface bring-up, not crash-related.) |
| journalctl boot -1 last entries | dcgm-exporter container restart at 09:51:05 (port 9400 already bound — small unit-script race, **not causal**); journal cuts mid-line on the next dockerd shim cleanup message. **No shutdown sequence.** |

**Conclusion**: The host crashed while doing essentially nothing,
with no software-visible precursor of any kind, on the older HWE
kernel that was supposed to be the safer one.

## Hypothesis status — significantly updated

| Hypothesis | Yesterday | Today |
|---|---|---|
| PCIe downgrade to 2.5 GT/s (forum #5) | RULED OUT | RULED OUT |
| ASUS firmware update introduced the crash | RULED OUT | RULED OUT |
| Long-context inference is the trigger (forum #2) | Still correlated, no longer prime suspect | **FURTHER WEAKENED** — crash at idle, KV cache <1% |
| Multi-Hermes session amplifier | Plausible | **WEAKENED** — `num_requests_running=1` at peak across the 31-min window |
| HWE kernel jump `1008 → 1014` is the cause | **STRONGLY CORRELATED, NOT YET CONFIRMED** | **FALSIFIED** as sole cause — crash on pinned `1008` |
| **GX10 platform-level hardware bug (forum #359785)** | Plausible | **STRENGTHENED — leading hypothesis.** Matches every signature: spontaneous, no kernel panic, all RAS clean, no shutdown sequence, reproduces across kernel revisions, reproduces across firmware revisions, occurs at idle. |

## Did the kernel pin help at all?

Open question. **Maybe yes, maybe no:**

- **Pre-pin baseline (running `1014`):** ~5 boots in 1h 50min during
  the 2026-05-01 ~00:00-01:50 EEST window (i.e. multiple crashes
  per hour), then ~10h between boots until the next crash chain.
- **Post-pin (running `1008`):** **22h 15min** continuous uptime on
  nvidia1 before this crash; nvidia2 still up 22h 31min and counting.

**MTBF appears to have improved roughly 22×**, but with N=2 cluster
and few data points, this is not statistically significant. The
improvement could be:

1. The kernel actually does help (real effect, just not a fix).
2. The pre-pin crashes coincided with a specific trigger (firmware
   update + recovery turbulence) that has now passed; a parallel
   universe with no kernel pin would have stabilised too.
3. Random — small sample, large variance.

We do not have enough data to distinguish (1) from (2). The honest
position is: **the pin doesn't hurt, may help marginally, and we
should keep it for operational simplicity** — but **it is not a
fix**.

## What to do next

### Immediate (this morning)

- [ ] **Bring nvidia1's vLLM API back online**:
  ```bash
  just spark-vllm-status            # confirm head + worker containers are Up
  just spark-vllm-api-restart       # docker exec -d the vllm serve payload
  ```
- [ ] **Verify the dashboard panels rendered the crash window**:
  open <http://192.168.1.189:3000/d/gx10-power-off-hunt>, set
  time range `2026-05-02 09:00:00` to `2026-05-02 10:30:00`. The
  GPU power / temp / vllm panels should be flat-low up to 09:51:05
  with a sharp gap (no data) until 09:58:04 when nvidia1 came back.
- [ ] **Snapshot the rendered dashboard for the NVIDIA forum reply**.

### Short term (this week)

- [ ] **Arm kdump on both Sparks**. Long overdue. Currently
  `crashkernel=1G-:0M` (zero memory reserved). Even if the next
  crash is hardware-side, having a kernel dump for the cases where
  it *isn't* gives us at least *some* signal. Operationally: this
  is a kernel command-line change → `update-grub` → reboot →
  verify `cat /proc/cmdline` shows `crashkernel=512M`.
- [ ] **File a NVIDIA Developer Forum reply on thread #359785** with
  this forensic record. Specifically:
  - **Crash signature**: spontaneous power-off, no shutdown
    sequence, no kernel panic, no MCE, no PCIe AER, no rasdaemon
    events.
  - **At-crash workload**: idle (`vllm_num_requests_running=1`,
    `kv_cache_usage_perc=0.87%`).
  - **GPU envelope at crash**: 67 W, 78°C — well within nominal.
  - **Reproduces across kernel revisions**: `6.17.0-1014`
    (multiple crashes/day on 2026-05-01) AND `6.17.0-1008`
    (this crash, 22h after boot).
  - **Reproduces across ASUS BIOS revisions**: `0103.2026.0129.1152`
    confirmed; reports also from `0100.*` per
    [2026-04-29 postmortem](./2026-04-29-cluster-recovery-and-26.04-rollback.md).
  - **DGX OS apt-pinned to known-stable; vllm-stack-autoupgrade
    masked**; nothing in our config has changed in the 22h
    preceding the crash.
  - Include link to a snapshot of the dashboard panels for context.

### Medium term

- [ ] **Don't unpin the kernel.** It probably helps marginally; it
  certainly doesn't hurt. The "kernel-fix-confirmed" milestone in
  [2026-05-02 bisection-evidence run page](./2026-05-02-kernel-pin-locked-bisection-evidence.md#monitoring-criteria-declaring-kernel-fix-confirmed)
  is **cancelled** — we won't get there. Replace with: "kernel pin
  retained as MTBF-improvement workaround; primary remediation is
  vendor-side."
- [ ] **Don't lower `--max-model-len` or `gpu_memory_utilization`
  yet.** This crash had nothing to do with workload. Keep these
  changes in reserve for *if* we see crashes that DO correlate with
  load (which we'd see in the GX10 hunt dashboard's "vLLM concurrent
  in-flight" panel preceding the crash).
- [ ] **Engage NVIDIA support / RMA process** if the ASUS Ascent
  GX10 hardware is under warranty or support contract — this is
  textbook hardware fault behaviour and they should be able to
  either swap the unit or escalate the firmware/silicon
  investigation. (Likely the operator will need to make this call;
  Ansible/code changes don't help here.)

### Cancelled

- ~~"3-day check-in: kernel-fix-confirmed at moderate confidence"~~
  — superseded by this run page.
- ~~"7-day / 14-day check-ins for kernel hypothesis"~~ — superseded.
  Replace with quarterly MTBF tracking once we know the actual
  steady-state behaviour.

## Recovery surprises (post-crash, both fixed)

While bringing nvidia1's services back into operation post-recovery,
two latent issues surfaced — neither is causally related to the
abrupt power-off, but both would have bitten any future Spark
recovery:

### 1. dockerd refused to start: `--live-restore daemon configuration is incompatible with swarm mode`

After nvidia1 powered back on, dcgm-exporter wouldn't start
("dependency failed"). Tracing the dependency chain found
**`docker.service` itself was failed** with:

```
failed to start cluster component: --live-restore daemon
configuration is incompatible with swarm mode
```

Root cause: Both Sparks had **stale Docker Swarm state from
2026-02-21** — `/var/lib/docker/swarm/` carried a swarm membership
(node_id `ud52cjv9wztc3ih0ojunhh0cp`, manager addr
`192.168.1.104:2377`). Neither the operator nor any of the roles in
this repo use Docker Swarm — kind on ms02 uses Kubernetes; the
Sparks use plain Docker (NGC vllm containers via
`roles/vllm_stacked_container`). The swarm state was leftover from
some earlier provisioning experiment in February and had been
quietly present since.

We **didn't notice it for 71 days** because dockerd was happily
running both as a swarm member AND with our containers — until
yesterday's [docker_daemon_config change](./2026-05-01-nvidia2-abrupt-power-off-vllm-long-context.md#open-follow-ups)
added `live-restore: true` (for the nvidia runtime + journald
log-driver work). On a running daemon, the SIGHUP picked up the
new options without conflict. **But the next clean dockerd start
with `live-restore: true` AND active swarm state hits this
incompatibility**. nvidia2's dockerd has been running for 22h+ since
the SIGHUP without a restart, so it didn't fail — but **next
reboot would have hit the same wall**.

**Fix applied 2026-05-02 ~10:20 EEST**:
- nvidia2 (dockerd up, containers running): `docker swarm leave --force`
  — Node left the swarm cleanly, no container disruption.
- nvidia1 (dockerd FAILED, can't run docker CLI): moved
  `/var/lib/docker/swarm` → `/var/lib/docker/swarm.preswarm-leave-2026-05-02`
  (preserves state for forensics, removes it from dockerd's path),
  then `systemctl reset-failed docker.service && systemctl start
  docker.service` — succeeded immediately. Containers (`vllm-ngc-ray-head`)
  came back up via `--restart unless-stopped`. dcgm-exporter
  restarted; HTTP 200 on `:9400`. `just spark-vllm-api-restart`
  brought `vllm serve` back inside the head container.

**Lesson**: when adding `live-restore: true` to dockerd config on a
host with a long history, check `/var/lib/docker/swarm/` first.
Worth adding a pre-flight check in `roles/docker` that fails-loud
if swarm state is present + `live-restore: true` is requested. (Open
follow-up.)

### 2. `roles/spark_kernel` discovery awk had the same `install ok installed` filter bug

Earlier today, the user's `dpkg -l 'linux-image-*' | grep '^ii'`
output appeared to show that `linux-image-6.17.0-1008-nvidia` had
been removed — actually, `apt-mark hold` had moved it to dpkg state
`hi` (hold/installed), and `^ii` excluded it. We
[fixed `just spark-kernel-status`](./2026-05-02-kernel-pin-locked-bisection-evidence.md)
to use `/ok installed/` instead of `/install ok installed/`.

**The Ansible role had the same bug**:

```yaml
# roles/spark_kernel/tasks/main.yml — old discovery
dpkg-query -W -f='${Status}\t${Package}\n' 'linux-image-*-nvidia' \
  | awk -F'\t' '$1=="install ok installed" && ...'
```

When `just spark-kernel-check` ran post-recovery to verify the
pin was still durable, the discovery returned `['6.11.0-1014-nvidia',
'6.17.0-1014-nvidia']` (excluding the held `6.17.0-1008-nvidia`),
which then **failed the assertion** `spark_kernel_pin in
spark_kernel_installed.stdout_lines` with:

```
spark_kernel_pin='6.17.0-1008-nvidia' is NOT an installed kernel
on this host. Installed: ['6.11.0-1014-nvidia', '6.17.0-1014-nvidia'].
```

**Fix applied 2026-05-02 ~10:18 EEST**:

```yaml
# roles/spark_kernel/tasks/main.yml — new discovery
dpkg-query -W -f='${Status}\t${Package}\n' 'linux-image-*-nvidia' \
  | awk -F'\t' '$1 ~ /ok installed$/ && ...'
```

Matches BOTH `install ok installed` (ii) AND `hold ok installed`
(hi). After this fix, `just spark-kernel-check` returns
`ok=18, changed=0` on both Sparks — confirming the pin is fully
idempotent and the role can no longer be tricked by its own
hold.

### Combined: the recovery sequence end-to-end

Post-power-on, the operator's recovery dance is now:

```bash
# (1) bring dockerd back if it failed (one-time after applying live-restore)
ansible nvidia1 -i inventory/hosts.yml -b -m shell -a 'mv /var/lib/docker/swarm /var/lib/docker/swarm.bak; systemctl reset-failed docker; systemctl start docker'
# (2) restart dcgm-exporter (was blocked on dockerd dependency)
ansible nvidia1 -i inventory/hosts.yml -b -m shell -a 'docker rm -f dcgm-exporter; systemctl restart dcgm-exporter'
# (3) bring vllm serve back inside the head container
just spark-vllm-api-restart
# (4) verify
just spark-vllm-status
just spark-observability-probe
just spark-kernel-status      # confirm pin still in place
```

Total: ~3 minutes from "host just came back" to "cluster fully
serving". Once both Sparks have left swarm, future recoveries skip
step (1).

## Did anything in the dashboard miss this?

No. The dashboard captured exactly what we built it for. The reason
"nothing showed" in the hunt panels (no GPU power spike, no temp
threshold crossing, no RAS counters incrementing) is itself the
critical signal: **the platform crashed without anything an OS or
exporter could have observed.**

This validates the dashboard design. It also validates the
"forensic capability" promise: had we been guessing post-incident,
we'd have wasted the next week chasing load / memory pressure.
Instead we have a clean rule-out in 30 minutes.

## Reasoning trace for the operator's `^ii` filter concern

Worth recording so this doesn't recur as a cognitive trap:

`apt-mark hold linux-image-6.17.0-1008-nvidia` does NOT change the
package's "installed" state — it changes its "desired" state from
`install` to `hold`. The dpkg state code is **two letters**:
`<desired><actual>`:

- `ii` = `install`/`installed` (normal)
- `hi` = `hold`/`installed` (held; **still installed**, just won't be
  auto-upgraded or auto-removed)
- `un` = `unknown`/`not-installed`
- `pn` = `purge`/`not-installed`
- ...

`grep '^ii'` filters to just `install`/`installed`, hiding `hi`
(held). To see all kernels regardless of hold status, use
`grep -E '^(ii|hi)'` or simply `dpkg -l 'linux-image-*' | grep
^.i` (any desired state, installed actual state).

Fixed in `just spark-kernel-status` earlier today (changed
awk filter from `/install ok installed/` → `/ok installed/` so held
packages stay visible). The operator's `^ii` was the same class of
issue applied manually.
