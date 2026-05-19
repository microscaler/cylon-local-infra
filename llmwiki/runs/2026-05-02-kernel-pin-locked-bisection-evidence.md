---
title: Kernel pin locked — bisection evidence after 22h continuous uptime
kind: run
status: superseded
superseded_by: runs/2026-05-02-nvidia1-abrupt-power-off-on-pinned-1008-kernel.md
tags: [gx10, abrupt-power-off, kernel, bisection, spark_kernel]
date: 2026-05-02
related:
  - runs/2026-05-02-nvidia1-abrupt-power-off-on-pinned-1008-kernel.md
  - runs/2026-05-01-nvidia2-abrupt-power-off-vllm-long-context.md
  - runs/2026-04-29-cluster-recovery-and-26.04-rollback.md
  - concepts/sparks-observability-pipeline.md
  - roles/spark_kernel
---

> **⚠ Superseded the same day.** `nvidia1` had an abrupt power-off
> at 09:51:05 EEST (~30 minutes after the pin was locked, ~22h after
> boot) on the pinned `6.17.0-1008-nvidia` kernel, while idle. The
> "kernel jump is the cause" hypothesis filed below is **falsified**.
> The platform-level GX10 hardware bug (forum #359785) is now the
> leading hypothesis. The pin is retained as an MTBF-improvement
> workaround, not a fix. See
> [runs/2026-05-02-nvidia1-abrupt-power-off-on-pinned-1008-kernel.md](./2026-05-02-nvidia1-abrupt-power-off-on-pinned-1008-kernel.md)
> for the falsifying evidence and updated next-steps. **The
> "monitoring criteria" section below is cancelled.**

# Kernel pin locked — bisection evidence after 22h continuous uptime

## TL;DR

Both Sparks have been **stable for 22+ hours on `6.17.0-1008-nvidia`**
with **zero abrupt power-offs**. This is **dramatically better** than
the days running on `6.17.0-1014-nvidia`, where MTBF was measured in
minutes-to-hours. Strong signal that the recent HWE kernel jump
`1008 → 1014` is at least one factor in the GX10 abrupt-power-off
pattern. Operator decision: **lock the pin and continue gathering
evidence** under representative load.

## Timeline

EEST = `Europe/Athens` (UTC+3).

| Time | What |
|---|---|
| **2026-05-01 00:13 EEST** | nvidia2 abrupt power-off #1 (on 6.17.0-1014) |
| **2026-05-01 00:22 EEST** | nvidia2 abrupt power-off #2 (~3 min into next boot) |
| **2026-05-01 00:30-01:49 EEST** | recovery chain: 5 short boots on both Sparks (12-30 min uptimes), all on 1014 — see `journalctl --list-boots` |
| **2026-05-01 ~11:00 EEST** | operator manually `grub-reboot`s nvidia1 to `6.17.0-1008-nvidia`; kernel-bisection scaffolding (`roles/spark_kernel`) shipped |
| **2026-05-01 11:35 EEST** | clean reboot of both Sparks; both come up on **`6.17.0-1008-nvidia`** (later determined: both were grub-rebooted, not just nvidia1 — see [2026-05-01 postmortem](./2026-05-01-nvidia2-abrupt-power-off-vllm-long-context.md)) |
| **2026-05-01 11:35 EEST → 2026-05-02 09:39 EEST** | **22h 4min continuous uptime, both hosts, ZERO crashes** under operator's normal Hermes workload |
| **2026-05-02 09:39 EEST** | operator declares the bisection signal strong enough to lock the pin; ran `just spark-kernel-apply` |

## Pin lockdown applied 2026-05-02 09:39 EEST

**Before** (the 22h stability was coincidental — pin was inventory-only):

```
GRUB_DEFAULT=0
holds: (none)
nvidia-spark-run-apt-upgrade-once.service: enabled, inactive
```

**After** `just spark-kernel-apply`:

```
GRUB_DEFAULT="gnulinux-advanced-27173d25-12a6-445a-bdf1-41f8f9842b59>gnulinux-6.17.0-1008-nvidia-advanced-27173d25-12a6-445a-bdf1-41f8f9842b59"
holds:
  linux-headers-6.17.0-1008-nvidia
  linux-headers-nvidia-hwe-24.04
  linux-image-6.17.0-1008-nvidia
  linux-image-nvidia-hwe-24.04
  linux-modules-6.17.0-1008-nvidia
  linux-modules-nvidia-580-open-6.17.0-1008-nvidia
  linux-modules-nvidia-580-open-nvidia-hwe-24.04
  linux-modules-nvidia-fs-6.17.0-1008-nvidia
  linux-modules-nvidia-fs-nvidia-hwe-24.04
nvidia-spark-run-apt-upgrade-once.service: masked, inactive
```

So now:

- **Next reboot** → boots `6.17.0-1008-nvidia` (GRUB_DEFAULT pinned to the explicit `gnulinux-…-1008-nvidia-advanced-<UUID>` menuentry, not "the first/latest installed").
- **`apt upgrade`** (manual or via Ubuntu's `apt-daily-upgrade.timer` or via DGX OS package triggers) **cannot bump the kernel** — 9 packages held cluster-wide (the kernel + the HWE meta + the matching nvidia-580-open + nvidia-fs modules).
- **`nvidia-spark-run-apt-upgrade-once.service`** masked — even if a NVIDIA dpkg trigger removes its `done`-file, systemd refuses to start it.

The pin is therefore **durable across**: routine reboots, unattended apt
upgrades, NVIDIA package updates, and manual-but-uncareful `apt upgrade`
runs. Only an operator deliberately running
`apt-mark unhold linux-image-*` or editing `inventory/group_vars/sparks.yml`
+ `just spark-kernel-apply` can move off `6.17.0-1008-nvidia`.

## Hypothesis status

Per the [2026-05-01 postmortem](./2026-05-01-nvidia2-abrupt-power-off-vllm-long-context.md)
"hypotheses probed" framework:

| Hypothesis | Status as of 2026-05-02 09:39 EEST |
|---|---|
| PCIe downgrade to 2.5 GT/s (forum #5) | **RULED OUT** (`lspci -vv` showed `32GT/s × 4` on all NICs/GPU) |
| ASUS firmware update introduced the crash | **RULED OUT** (crashes happened on both `0100.*` and `0103.*`) |
| Long-context inference is the trigger (forum #2) | **STILL CORRELATED but no longer the prime suspect** — `--max-model-len 262144` unchanged across the 22h stable window |
| **HWE kernel jump `1008 → 1014` is a contributing factor** | **STRONGLY CORRELATED** — 22h+ stable on 1008 vs. multiple crashes/day on 1014, all other variables held constant |

The kernel hypothesis is now the leading candidate. **It is not yet
confirmed** — one sample of 22h is short, and the workload during the
22h window is what the operator happened to do, not a controlled test.

## What else could explain the 22h stability (alternative hypotheses)

Worth being honest about:

- **Workload happened to be lighter** during the 22h window than during
  the crash days. Mitigation: the [GX10 abrupt-power-off hunt
  dashboard](http://192.168.1.189:3000/d/gx10-power-off-hunt) records
  GPU power, KV cache occupancy, and `vllm:num_requests_running`
  continuously — a backwards look at the 22h timeseries would falsify
  this if peak load was demonstrably lower than during the crash days.
- **NVIDIA platform "warm-up"** or some hardware-side stabilisation that
  happens to coincide with a clean reboot. Unlikely (forum #359785
  reports the issue *after* clean reboots, not before), but not zero.
- **Apt upgrades / system housekeeping** that ran after the
  `spark-vllm-provision-recreate` and quietly fixed something. Would
  show in `apt history.log` — not yet checked, but easy to verify.

These are why we're declaring "**locked + monitoring**", not "**fixed**".

## Monitoring criteria — declaring kernel-fix-confirmed

| Window | Confidence | Action |
|---|---|---|
| **3 days** (~72h) continuous uptime, both hosts, normal Hermes workload | Moderate. ~3× the longest pre-pin uptime. | Continue monitoring; no action. |
| **7 days** continuous uptime, both hosts | Strong. | Update [2026-05-01 postmortem](./2026-05-01-nvidia2-abrupt-power-off-vllm-long-context.md) "hypotheses" table to reflect "kernel jump LIKELY contributing factor". |
| **14 days** continuous uptime, both hosts | High. | Promote the kernel pin from "diagnostic" to "production stable baseline" in the [8-spark fabric concept page](../concepts/8-spark-fabric-and-orchestrator.md). Consider posting our bisection evidence to NVIDIA forum #359785. |
| **Crash returns on 1008** | Falsified — kernel was not the only cause. | Move to load-axis bisection: lower `--max-model-len` 262144 → 65536, lower `vllm_gpu_memory_utilization` 0.80 → 0.75; or apply the [Hermes config tightening](./2026-05-01-nvidia2-abrupt-power-off-vllm-long-context.md#recommended-hermes-side-config-changes-no-cluster-impact); or stand up the [auxiliary-model-isolation pattern](../concepts/auxiliary-model-isolation.md). |

## What "test further" looks like

The operator wants to "test further with the current kernel version".
Concrete things to do:

1. **Run real Hermes workload normally** — the 22h was light-to-moderate
   load. A heavy multi-Hermes-session day under `--max-model-len 262144`
   on 6.17.0-1008 is the most informative test.
2. **Open the [GX10 abrupt-power-off hunt
   dashboard](http://192.168.1.189:3000/d/gx10-power-off-hunt)** and
   periodically eyeball the GPU power band, RAS counters, and `vllm:
   num_requests_running` during heavy use. Anything above the
   thresholds (200 W sustained / any RAS event / waiting > 8) is worth
   noting in this run page.
3. **Don't change anything else** for 1-2 weeks. Variables held
   constant: kernel, `--max-model-len`, `gpu_memory_utilization`,
   firmware, ASUS BIOS, NCCL/RoCE config, Hermes settings. The only
   thing that should vary day-to-day is workload intensity.
4. **If a crash does happen**, the dashboard will have captured the
   30 minutes preceding it across GPU power/temp, EDAC/PCIe AER/MCE,
   vLLM concurrent in-flight, and the kernel/dockerd journal — exactly
   the forensic record we never had before. Append findings to this
   run page; don't delete it.

## Open follow-ups

- [ ] **Day +3 check-in (2026-05-04 EEST)** — re-run
  `just spark-observability-probe` and `just spark-kernel-status`;
  capture uptime here.
- [ ] **Day +7 check-in (2026-05-08 EEST)** — same; if green, update
  the [2026-05-01 postmortem](./2026-05-01-nvidia2-abrupt-power-off-vllm-long-context.md)
  hypotheses table.
- [ ] **Day +14 check-in (2026-05-15 EEST)** — same; if green, promote
  pin to "production stable" in the
  [8-spark fabric plan](../concepts/8-spark-fabric-and-orchestrator.md)
  and consider posting bisection evidence to NVIDIA forum #359785.
- [ ] **kdump arming** still queued from prior postmortems — irrelevant
  if no crash happens, very relevant if one does. Worth doing while
  the cluster is calm.
- [ ] **Confirm dashboard captured the 22h window** — verify
  `up{cluster="cylon-sparks"}[24h]` is solid green in Prometheus over
  the bisection window. (The dashboard exists; haven't visually
  confirmed continuity.)
