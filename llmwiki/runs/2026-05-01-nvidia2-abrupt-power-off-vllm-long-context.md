---
title: 2026-05-01 — nvidia2 abrupt power-off (twice in 9 min); confirmed match for NVIDIA forum thread #359785
kind: run
status: shipped
date: 2026-05-01
hosts: [nvidia2, nvidia1]
tags: [postmortem, abrupt-power-off, gx10, gb10, vllm, long-context, firmware, pcie, forum-confirmed, hardware]
related:
  - runs/2026-04-27-ray-head-exited-postmortem.md
  - runs/2026-04-29-cluster-recovery-and-26.04-rollback.md
  - concepts/restart-unless-stopped-after-manual-stop.md
  - concepts/ngc-stacked-container-stack.md
  - entities/nvidia2.md
sources:
  - https://forums.developer.nvidia.com/t/title-asus-ascent-gx10-gb10-hard-power-off-unclean-reboot-under-vllm-gpt-oss-120b-long-context/359785
---

# nvidia2 abrupt power-off ×2 — confirmed match for NVIDIA forum thread #359785

A second nvidia2 abrupt power-off in 4 days, this one a **double-crash**:
the host crashed once at the end of a 34 h uptime, auto-recovered, then
crashed again 3 minutes into the next boot. Cross-referenced against
[NVIDIA Developer Forum thread #359785](https://forums.developer.nvidia.com/t/title-asus-ascent-gx10-gb10-hard-power-off-unclean-reboot-under-vllm-gpt-oss-120b-long-context/359785)
("ASUS Ascent GX10 (GB10) hard power-off / unclean reboot under vLLM
(gpt-oss-120b, long context)") — symptom is **a known platform issue**
affecting many GX10 owners across multiple firmware revisions, not unique
to our cluster.

## Timeline (EEST, all on 2026-05-01)

| Time | Event |
|---|---|
| `2026-04-29 13:29` | Last clean boot of nvidia2 (planned reboot via `just spark-reboot`, post-firmware-update + post-apt-upgrade per [2026-04-29 postmortem](./2026-04-29-cluster-recovery-and-26.04-rollback.md)). |
| `2026-04-29 → 2026-05-01 00:13:45` | nvidia2 ran 34 h 44 m, serving `Qwen3.6-35B-A3B-FP8` at TP=2 with `--max-model-len 262144` and the autoupgrade daemon pinned to `26.03-py3` (per the 2026-04-29 fix). |
| **`2026-05-01 00:13:45`** | **Abrupt power-off #1.** Journal file moved to `.journal~` recovery state (truncated; classic hardware-reset signature, not panic). |
| `00:19:21` | nvidia2 self-recovers (auto-power-on, presumably via BMC/firmware policy). |
| **`00:22:03`** | **Abrupt power-off #2 — only 3 minutes into the new boot.** No journal file at all (crashed before journald flushed). |
| `~00:34` | nvidia2 self-recovers again. Currently up for ~11 min at probe time. |
| `~00:36-00:37` | nvidia1 head container restarts via `--restart unless-stopped` (downstream cascade — Ray worker on nvidia2 disappeared, engine core got `ActorDiedError`, head container's `ray start --block` exited). vllm serve is gone (it was a `docker exec -d` payload). |

## Hypotheses tested

| Hypothesis | Source | Probe | Result |
|---|---|---|---|
| **PCIe link downgrade to 2.5 GT/s** | Forum post #5 (`tech201`, 2026-02-09) | `lspci -vv -s <gpu>` and `<each Mellanox NIC>` for `LnkSta:` | **RULED OUT.** Both Sparks show `Speed 32GT/s, Width x4` on GPU + all 4 ConnectX-7 NICs (full spec). |
| **Recent firmware update introduced the crash** | Operator's instinct (the user) | Compare crash dates across firmware revisions | **RULED OUT for THIS crash.** We crashed on FW `GX10DGX.0100.2025.0916.1213` (Apr 27, Apr 28, Apr 29 morning) AND on FW `GX10DGX.0103.2026.0129.1152` (Apr 29 afternoon, May 1 today). Pattern is **unchanged across firmware revisions**. So the FW update is **not the cause** — but it's also **not the fix**. |
| **Long context triggers it** | Forum post #2 (`notmy.reward438`): *"saw significant performance degrading after 30k tokens"* | Cross-check our `--max-model-len` vs forum's 30k threshold | **STRONGLY CORRELATED.** We run `--max-model-len 262144` (262k!) — well past the forum's instability threshold. The 34 h pre-crash boot was at this config. **Only operationally-testable lever**. |
| **Standing UMA pressure makes platform fragile** | Our [2026-04-29 cluster-recovery postmortem](./2026-04-29-cluster-recovery-and-26.04-rollback.md) (gpu_memory_utilization=0.80 leaves only ~5 GiB host headroom, may compound with long-context allocations) | Verify by lowering KV cache or memory util | Untested today; both 0.80 (current) and a hypothetical lower value haven't been A/B'd in the field. Worth considering jointly with max-model-len reduction. |

## Cross-refs to the NVIDIA forum thread

[Thread #359785](https://forums.developer.nvidia.com/t/title-asus-ascent-gx10-gb10-hard-power-off-unclean-reboot-under-vllm-gpt-oss-120b-long-context/359785) — opened **2026-02-05**, **31 posts** as of today (we read first 5 via Discourse JSON API):

- **#1 (siertum, 2026-02-05)**: same exact symptom on ASUS GX10 GB10 with vLLM. Quote: *"abrupt power cut / firmware reset (unclean shutdown)... not thermal — ~85-90 W, ~60-70 °C right before disconnect"*. Their FW: `GX10DGX.0102.2025.1111.1531` (one minor revision behind ours).
- **#2 (notmy.reward438, 2026-02-05)**: workaround = lower max context tokens, observed performance degradation past 30k.
- **#3 (tech201, 2026-02-09)**: fresh box, ran fine, **then updated system → started crashing**. Same FW `.0102` as siertum.
- **#5 (tech201, 2026-02-09)**: *"updated to latest firmwares yesterday without success... PCIe issue (speed being downgraded to 2.5GT/s instead of 32GT/s)"*. **PCIe theory ruled out for our hosts** by direct probe (see hypothesis table).

## What we did not do (deliberately)

- **Did NOT lower `--max-model-len`** despite the forum's #2 workaround being the only testable lever. Reason: it's a lossy workaround (real context loss for hard-reasoning workloads), and we want to land it as a deliberate decision in a maintenance window — not as a panic-edit at 00:45 EEST during recovery. Captured as an open follow-up below.
- **Did NOT change firmware** — both Sparks are on `GX10DGX.0103.2026.0129.1152` (latest published as of 2026-04-29). No newer revision available.
- **Did NOT touch inventory** — preserves the `--max-model-len 262144` + `gpu_memory_utilization 0.80` + `26.03-py3` pin invariants. Today's stable config remains the production reference.

## Recovery sequence

The recovery that **worked** (~01:00 EEST):

```bash
# After the abrupt-power-off + auto-recovery, BOTH SPARKS need a CLEAN REBOOT
# before recreate. Skipping this step caused a synchronised double-crash —
# see "Failed first attempt" below.
ansible sparks -b -m shell -a 'reboot'    # both hosts; SSH will drop briefly
# wait ~2 min for both to come back online
ansible sparks -m wait_for_connection -a 'delay=20 timeout=300'
just spark-vllm-provision-recreate    # full recreate on fresh kernel state
# wait ~3.5 min for cold-start (Application startup complete in /root/vllm-serve.log)
just spark-vllm-status                # /v1/models JSON when API binds
```

Cold-start to `:8000 LISTEN`: **3 min 29 s** on warm caches (`00:54:36`
boot → `00:58:05` `Application startup complete`).

### Failed first attempt (~00:51 EEST) — synchronised double-crash

Operator's first instinct was to skip the host reboot and go straight to
`spark-vllm-provision-recreate` after the abrupt-power-off recovery. Result:

| Time | Event |
|---|---|
| ~00:50 | `just spark-vllm-provision-recreate` starts. Containers freshly created on both hosts at this point. |
| ~00:51:34 | Head container reaches `Up 13s`, Ray cluster forms (2 ALIVE nodes), engine init begins (fastsafetensors weight load: ~30 GiB per rank simultaneously across both Sparks). |
| **`00:54:13`** | **nvidia1 abrupt power-off** (had been up 1 d 11 h with no prior issue). |
| **`00:54:24`** | **nvidia2 abrupt power-off** — **11 seconds after nvidia1**. Synchronised. |
| `00:54:36` / `00:54:47` | Both hosts auto-recover (BMC). |

**Hypothesis** (consistent with the [2026-04-29 cluster-recovery
postmortem](./2026-04-29-cluster-recovery-and-26.04-rollback.md) "standing
UMA pressure" section): the recreate-on-already-degraded-host triggers
simultaneous ~30 GiB UMA allocations on both ranks during weight load.
That allocation event, combined with whatever degraded state the abrupt-
power-off left in the NVIDIA driver / firmware on nvidia2, is enough to
push *both* hosts over the edge near-simultaneously.

**The lesson is operational, not architectural**: after an abrupt-power-
off, the correct recovery is **clean reboot of both hosts first, then
recreate**, not recreate-on-top-of-the-already-flaky-host. The clean
reboot resets the NVIDIA driver state, IB verbs MR/QP pinning, and any
other accumulated kernel state from the unclean shutdown. Recreate then
runs cleanly on top of a fresh-boot baseline. **Update `just spark-reboot`
recipe usage docs** to call this out explicitly — see open follow-up.

Cluster state at probe time:

- Ray cluster: 2 ALIVE nodes (auto-restarted via `--restart unless-stopped`)
- `vllm serve` PID: empty (the `docker exec -d` payload died with the head container restart, same as 2026-04-29 11:17)
- `:8000` listener: empty
- `rasdaemon --summary`: zero memory / PCIe AER / MCE errors
- BIOS on both: `GX10DGX.0103.2026.0129.1152` (unchanged since 2026-04-29 firmware update)

## Open follow-ups

- [ ] **Document the "clean-reboot before recreate" recovery rule** in
  `roles/vllm_stacked_container/README.md` and the `just spark-reboot`
  recipe comment. Specifically: after an abrupt-power-off recovery,
  do NOT run `spark-vllm-provision-recreate` directly on the
  auto-recovered cluster — it triggers a synchronised double-crash
  (observed 2026-05-01 ~00:54). Always: `just spark-reboot` (graceful
  reboot of both hosts) → wait for them to settle → THEN
  `just spark-vllm-provision-recreate`. The clean reboot resets NVIDIA
  driver / IB verbs / firmware state from the unclean shutdown.
- [ ] **Decide whether to lower `--max-model-len`** from `262144` → `65536` or `32768`. This is the forum thread's only operationally-testable workaround. **Cost**: real context loss for hard-reasoning sessions. **Benefit**: possibly fewer abrupt power-offs (unproven; correlation only). Land as a deliberate config change in a maintenance window, with before/after metrics on uptime, not as a panic edit. Could pair with reducing `gpu_memory_utilization` from `0.80` → `0.75` (per the 2026-04-29 postmortem's "standing UMA pressure" concern).
- [ ] **Track NVIDIA forum thread #359785** for new posts past #5 (31 total when we visited; we only read the first 5). Anything from NVIDIA staff acknowledging the issue, naming a fix, or proposing a more targeted workaround supersedes our local lever.
- [ ] **NVIDIA Developer Forum post**: consider posting our own observations (crash signature, ruled-out PCIe, two-crash sequence, FW-version invariance) to the thread. Useful both for our visibility and for the operator community. Worth pairing with the [2026-04-27 postmortem](./2026-04-27-ray-head-exited-postmortem.md)'s evidence (rasdaemon clean, journal in `.journal~` recovery state, kdump unarmed by default).
- [ ] **The `nvidia-spark-limits` reboot from 2026-04-29's apt upgrade** — has applied; `/var/run/reboot-required` is clear post the planned reboot. Not implicated in today's crash.
- [ ] **Same actions remain queued** from prior postmortems: arm kdump on both Sparks (so the next panic — if any — leaves a real dump), run a stress-while-monitoring-power test, file the open question with NVIDIA support.
- [ ] **Apply the Hermes-side config changes** in the table below (single-user-side edit, no cluster impact, reversible). Run for 1-2 weeks to observe whether crash MTBF improves.
- [ ] **Stand up the auxiliary-isolation small model** per [`concepts/auxiliary-model-isolation.md`](../concepts/auxiliary-model-isolation.md) — pick deployment pattern (today: ms02-CPU vs Pi-cluster-Ollama; future: dedicated MS-A2 once it arrives) and re-point `auxiliary.*` providers to the new endpoint. This is the biggest single Hermes-side lever to remove concurrent-call pressure on the main model.
- [x] **Kernel step-back scaffolding** — added `roles/spark_kernel/` (idempotent, reversible, 4 inventory flags). Pins GRUB default kernel via deterministic ID construction (`gnulinux-<KVER>-advanced-<root-fs-UUID>`), holds the kernel + HWE meta packages so apt won't bump them, and masks `nvidia-spark-run-apt-upgrade-once.service` (oneshot gated by `/var/lib/nvidia-spark-run-apt-upgrade-once/done`) so DGX OS dpkg triggers can't re-arm an apt-upgrade behind the pin. Wired into `roles/spark_provision` as new `kernel` phase (toggle: `spark_provision_kernel`). New justfile recipes: `spark-kernel-status`, `spark-kernel-apply`, `spark-kernel-check`, `spark-kernel-pin`, `spark-kernel-show-menu`. **Inventory chosen: cluster-wide step-back** — both Sparks pinned to `6.17.0-1008-nvidia` at the group level (`inventory/group_vars/sparks.yml`), no per-host override. Rationale: gives a clean before/after MTBF comparison against today's crash history (which was on `6.17.0-1014-nvidia`). If MTBF improves materially, the recent kernel bump is implicated; if not, kernel is ruled out and we move to load-axis bisection (`--max-model-len`, `--max-num-seqs`, Hermes concurrent-aux). Operator runs `just spark-kernel-apply` then `just spark-reboot` to materialise.
- [x] **PIN LOCKED 2026-05-02 09:39 EEST** — after **22h continuous uptime, both Sparks, zero abrupt power-offs** (vs. multiple crashes/day on `6.17.0-1014`), operator declared the bisection signal strong enough to commit. Ran `just spark-kernel-apply` for real; GRUB_DEFAULT now `gnulinux-advanced-<UUID>>gnulinux-6.17.0-1008-nvidia-advanced-<UUID>`, 9 packages held cluster-wide (kernel + HWE meta + nvidia-580-open + nvidia-fs modules), `nvidia-spark-run-apt-upgrade-once.service` masked. Pin is durable across routine reboots, unattended apt upgrades, and NVIDIA package triggers.
- [x] **HYPOTHESIS FALSIFIED 2026-05-02 09:51 EEST** — `nvidia1` abrupt power-off **on the pinned `6.17.0-1008-nvidia` kernel**, ~12 minutes after the pin was locked, ~22h after boot. Forensic data captured by the [GX10 hunt dashboard](http://192.168.1.189:3000/d/gx10-power-off-hunt) shows the host crashed **at idle**: GPU 67 W (vs. ~240 W TGP), 78°C, vLLM `running=1, waiting=0`, KV cache 0.87%, ALL rasdaemon counters zero, no kernel signal in journald (no mlx5/MCE/panic/aer/oom/xid). Last journal entry mid-line; no shutdown sequence — classic GX10 platform abrupt-power-off pattern matching forum #359785. **Kernel as sole cause is ruled out.** Pin retained as a likely-marginal MTBF improvement (22× over pre-pin baseline, but small sample size). The platform-level hardware bug is now the leading hypothesis; primary remediation path is vendor-side, not in our stack. Full forensic record + revised next-steps: [2026-05-02 nvidia1-abrupt-power-off-on-pinned-1008-kernel](./2026-05-02-nvidia1-abrupt-power-off-on-pinned-1008-kernel.md). The earlier [2026-05-02 bisection-evidence run page](./2026-05-02-kernel-pin-locked-bisection-evidence.md) is now superseded.

## Hermes config amplifies the precursors (added 01:05 EEST)

After the main recovery, operator pasted their `.hermes/config.yaml`
asking whether Hermes itself could be contributing. **Hermes is not the
root cause** (forum thread #359785 documents the same crash on users
who don't run Hermes), **but the default config materially amplifies
the two crash precursors** — long-context routine usage AND concurrent
auxiliary vLLM calls.

### What Hermes does to the cluster (with current config)

```yaml
providers:
  qwen-spark:
    base_url: http://192.168.1.104:8000/v1
    models:
      - id: Qwen/Qwen3.6-35B-A3B-FP8
        context_window: 262144     # uses FULL 256k window — past the forum's 30k stability threshold
        max_tokens: 16384          # 16k per response (long thinking traces)
agent:
  max_turns: 250                   # cumulative session length
  reasoning_effort: High           # forces deep <think> traces
auxiliary:
  vision:           {provider: auto, ...}    # ALL nine auxiliary tasks
  web_extract:      {provider: auto, ...}    # default to provider: auto
  compression:      {provider: auto, ...}    # which routes to the SAME main-model
  session_search:   {provider: auto, max_concurrency: 3}   # endpoint at :8000
  skills_hub:       {provider: auto, ...}
  approval:         {provider: auto, ...}
  mcp:              {provider: auto, ...}
  flush_memories:   {provider: auto, ...}
  title_generation: {provider: auto, ...}
compression:
  threshold: 0.5                   # compress when context hits 50% (=131k tokens) — runs OFTEN
memory:
  flush_min_turns: 6               # flush every 6 turns → another concurrent call
```

At any given moment a single Hermes session can have multiple concurrent
requests landing on the main 35B-FP8 model:

- main agent generation (16k max_tokens, deep reasoning traces)
- compression (kicks in when context hits 131k)
- memory flush (every 6 turns)
- session_search (up to 3 concurrent)
- title_generation, MCP, skills_hub, approval, etc.

vLLM is configured with `--max-num-seqs 128`, so it accepts all of them
— but each concurrent sequence claims KV-cache slots + activations. The
cumulative UMA pressure spike during periods of concurrent activity is
exactly the load step the [2026-04-29 cluster-recovery
postmortem](./2026-04-29-cluster-recovery-and-26.04-rollback.md)
hypothesised as the SoC-instability trigger.

### Recommended Hermes-side config changes (no cluster impact)

In rough order of impact, smallest effort first:

| Change | Today | Suggested | Why |
|---|---|---|---|
| `context_window` | `262144` | **`65536`** | Caps the per-request context Hermes will send. Brings inference under the forum's "stable below 30k" threshold for small queries with headroom for normal sessions. vLLM `--max-model-len` stays 262144; this is just Hermes's per-request budget. |
| `max_tokens` | `16384` | **`8192`** (or `4096` for non-reasoning tasks) | Caps response length. Doesn't kill thinking — most reasoning fits in 4-8k tokens per the [2026-04-19 thinking-validation note](./2026-04-19-qwen3-thinking-validation.md). |
| `compression.threshold` | `0.5` | **`0.7`** | Compression itself is a vLLM call. Higher threshold = compression runs less often. |
| `session_search.max_concurrency` | `3` | **`1`** | Direct concurrency cap on session search. |
| `auxiliary.*.provider: auto` | all auto | **point at a separate small model endpoint** | Biggest fix — see [auxiliary-model-isolation concept](../concepts/auxiliary-model-isolation.md). |
| `max_turns` | `250` | **`100`** (or `50`) | Caps session length. |
| `reasoning_effort` | `High` | **`Medium`** for default | Lower default = shorter thinking traces = less GPU time. |

These are all single-user-side edits — no vLLM bounce, no cluster
impact, no inventory change. Empirically, run with these settings for
1-2 weeks and see if crash MTBF improves.

### The architectural fix (separate concept page)

The biggest single lever — routing auxiliary tasks to a separate small
model — is bigger than this postmortem and lives on as
[`concepts/auxiliary-model-isolation.md`](../concepts/auxiliary-model-isolation.md).
It articulates the principle, deployment patterns (today vs post-MS-A2),
the specific Hermes config mappings, and how it fits into Phase 6 of
the [8-spark fabric plan](../concepts/8-spark-fabric-and-orchestrator.md).

## What this means for the 2026-04-29 8-spark plan

[concepts/8-spark-fabric-and-orchestrator.md](../concepts/8-spark-fabric-and-orchestrator.md) is still pinned waiting on hardware. Today's incident **doesn't change that decision** — it confirms the platform fragility that motivates several of its design choices:

- **TP=8 single replica with no DP** does not fix this — the abrupt power-off is per-host, and TP=8 still means 1-host-down = whole-cluster-down. The 8-spark plan's framing of replica DP as a future option (currently N/A) stays correct.
- **The Pi-cluster-as-orchestrator-host architecture** doesn't fix the crash either — it's NVIDIA hardware on the Spark side, not anything we put on top.
- **`vllm-stack-autoupgrade` pinned to `26.03-py3`** stays the right call — newer images don't fix the crash and risk introducing regressions.
- **`--max-model-len 262144` is the lever to revisit** — when we do migrate to TP=8 + Qwen3-VL-235B-A22B-FP8, picking a smaller initial context (e.g. 65536) and raising only when we have stable-uptime evidence is the right discipline.
