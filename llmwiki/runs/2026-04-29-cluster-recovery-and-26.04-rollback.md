---
title: 2026-04-29 — Cluster recovery chain (nvidia2 abrupt power-off, OTel SIGSEGV, HF retries, 26.04-py3 autoupgrade promotion failure, rollback, apt upgrade); role hardening
kind: run
status: shipped
date: 2026-04-29
hosts: [nvidia1, nvidia2]
tags: [postmortem, abrupt-power-off, autoupgrade, ngc-vllm-image, otel, hf-hub-offline, apt, role-tags, justfile, recovery]
related:
  - ../runs/2026-04-27-ray-head-exited-postmortem.md
  - ../runs/2026-04-19-fp8-stack-cutover.md
  - ../runs/2026-04-19-vllm-stack-autoupgrade-service.md
  - ../runs/2026-04-19-26.03-py3-upgrade.md
  - ../concepts/restart-unless-stopped-after-manual-stop.md
  - ../concepts/ngc-image-transformers-lag.md
  - ../concepts/ngc-stacked-container-stack.md
  - ../entities/nvidia2.md
  - ../entities/vllm-stack-autoupgrade-service.md
  - ../entities/ngc-vllm-image.md
---

# Cluster recovery chain — 2026-04-29

A cascade of five distinct failure modes hit the cluster within ~3 hours of an
ASUS Ascent firmware update reboot. Each was diagnosed and recovered in turn;
the underlying abrupt-power-off issue (postmortemed for nvidia1 on 2026-04-27)
is now confirmed on nvidia2 too.

## Timeline (EEST, all on 2026-04-29)

| Time | Event |
|---|---|
| **~07:55** | Operator runs ASUS Ascent firmware update on both Sparks. BIOS bumped from `GX10DGX.0100.2025.0916.1213` (2025-09-16) to **`GX10DGX.0103.2026.0129.1152`** (2026-01-29) on both. Hosts reboot. `--restart unless-stopped` brings head + worker containers back, but `vllm serve` (a `docker exec -d` payload from earlier) does not. |
| **~07:55–10:00** | Operator restores `vllm serve` via `just spark-vllm-provision`. First attempt surfaces an OTel `getenv()` SIGSEGV inside the worker `RayWorkerWrapper` actor (pid 505) on nvidia2 — `OtlpGrpcMetricExporter::Export()` → `grpc_core::GetEnv` → libc `getenv` race. Worker actor dies, engine core sees `ActorDiedError`, API never binds. Fix: `OTEL_SDK_DISABLED=true` + `OTEL_{METRICS,TRACES,LOGS}_EXPORTER=none` added to `vllm_distributed_extra_env` in `inventory/group_vars/sparks.yml`. Recreate via `just spark-vllm-provision-recreate`. |
| **~10:15** | Second attempt surfaces a transient HF Hub `504` on `HEAD https://huggingface.co/Qwen/Qwen3.6-35B-A3B-FP8/resolve/main/preprocessor_config.json` (the file does not exist on Hub for this model — `Qwen3_5MoeForConditionalGeneration` is text-only but `transformers` probes for it because the arch class name suggests multimodal). vLLM retry-stalls startup; this is the same path that hung 2026-04-27 post-engine-init. Fix: `HF_HUB_OFFLINE=1` re-added to `vllm_distributed_extra_env` (we have `hf-prefetch.service` to guarantee local cache completeness). Recreate again. API binds and serves real workloads (`/v1/chat/completions 200 OK` from `192.168.1.189` for ~30 min, prompt + decode throughput nominal). |
| **`10:49:40`** | **All four mlx5 NICs on nvidia1 go LINK DOWN simultaneously** (`enp1s0f0np0`, `enP2p1s0f0np0`, `rocep1s0f0`, `roceP2p1s0f0`). Same kernel-timestamp on every interface = the **other end of the cable disappeared**, not a local issue. **nvidia2 has powered off abruptly** — same signature as the nvidia1 events postmortemed on 2026-04-27 (no journal entries from nvidia2 past this point, no shutdown record, ARP entry on nvidia1 transitions to `FAILED`). |
| **~10:50** | Ray on nvidia1 detects the dead worker → engine core `ActorDiedError` → ray-head process exits → `--restart unless-stopped` relaunches the head container running just `ray start --block --head`. `vllm serve` is gone (it was a `docker exec -d` payload). |
| **`11:12`** | Operator power-cycles nvidia2; host comes back online. |
| **`11:15:44`** | `vllm-stack-autoupgrade.service` (`enabled: true` since 2026-04-19) detects an idle/quiet cluster and decides to promote the candidate tag `nvcr.io/nvidia/vllm:26.04-py3` (synced earlier today by `ngc-image-sync.service`). It stops the worker (was already stopped due to nvidia2 abrupt power-off recovery), stops the leader, and `docker run`s the new head on **`26.04-py3`** with the **captured argv from the prior 26.03 head**. |
| **`11:17:47`** | Promotion fails. New head container exits with `127`. `docker logs` shows `/bin/bash: line 1: ray: command not found` — repeated for every `--restart unless-stopped` retry. `ngc-image-sync.service` shipped 26.04 with `ray` in a path **not on the default `bash -c` PATH**; the role's `--entrypoint /bin/bash -c 'ray start ...'` worked on 26.03 but not 26.04. Daemon writes `status: "error", reason: "promotion failed — operator triage required"` to `/var/lib/vllm-stack-autoupgrade/state.json` and stops attempting. |
| **~11:20** | Operator finds cluster down. Triage: stop the autoupgrade daemon, `docker rm -f` the failed 26.04 head, `just spark-vllm-provision-recreate` to rebuild on inventory's `vllm_stacked_container_image: "nvcr.io/nvidia/vllm:26.03-py3"`. |
| **~11:25** | Recreate completes. 2 ALIVE Ray nodes. `vllm serve` cold-starts on warm caches; `/v1/models` returns `gpt-4o-mini` JSON within ~3 min. |
| **~11:30** | Operator runs `apt upgrade`. `ansible-playbook --tags apt` reports `changed=0` — the `spark_apt` role's tasks didn't fire. Diagnosis: `roles/spark_provision/tasks/main.yml` includes the role with `tags: [spark, apt]` on the include task itself, but those tags do **not** propagate to the role's inner tasks without `apply: { tags: [...] }`. Same wart filed as a follow-up in [runs/2026-04-19-qwen3_6-35b-a3b-promoted.md](./2026-04-19-qwen3_6-35b-a3b-promoted.md). |
| **~11:32** | Operator works around with `ansible sparks -b -m apt -a 'update_cache=yes upgrade=safe force_apt_get=yes'`. 4 packages upgraded + 3 new `nvidia-spark-*` packages pulled in (see "What landed" below). Both hosts now flag `*** System restart required *** nvidia-spark-limits`. |
| **~11:33** | Role bug fixed: `apply: { tags: [spark, apt] }` added to the `Phase — APT upgrade` `include_role` so future `--tags apt` runs work via the playbook. |

## Five distinct issues, in dependency order

1. **OTel `getenv()` SIGSEGV race on aarch64 + glibc + gRPC** in `vllm/tracing/otel.py`'s periodic metrics exporter — kills the worker `RayWorkerWrapper` actor → engine core `ActorDiedError` → API never binds. Fixed by `OTEL_SDK_DISABLED=true` etc.
2. **HF Hub HEAD storm** on every cold start — the `transformers` library probes optional config files even when the model is fully cached; HF outages or slow responses block startup. Fixed by re-adding `HF_HUB_OFFLINE=1` (we have `hf-prefetch` doing the cache work).
3. **nvidia2 abrupt power-off** at 10:49:40 — same kernel-undetectable platform-level shutdown that's been hitting nvidia1. Confirmed both Sparks are affected; the firmware update did **not** fix it.
4. **Autoupgrade promotion failure to 26.04-py3** — role's `bash -c 'ray start ...'` doesn't find `ray` because 26.04 puts it in a location only on the **login shell** PATH. Fixed by `bash -c` → `bash -lc` in `roles/vllm_stacked_container/tasks/main.yml` (next recreate picks it up).
5. **`spark_apt` role tag-propagation bug** — `include_role` doesn't propagate outer tags to inner tasks. Fixed by `apply: { tags: [spark, apt] }`.

## What landed (apt upgrade)

| Package | Before | After | Notes |
|---|---|---|---|
| `dgx-dashboard` | 0.23.3 | **0.25.11** | DGX Spark dashboard (used for OTA flow). |
| `dgx-spark-ota-update-meta` | 26.03.1 | **26.04.1** | Pulls 3 new `nvidia-spark-*` packages as deps. |
| `nvidia-dgx-telemetry` | 4.11 | **5.22** | Upgrade pre-stops + post-starts the systemd unit. |
| `nvidia-spark-avahi-conf` | absent | **1.0-1** | mDNS configuration for Spark hostnames. |
| `nvidia-spark-limits` | absent | **1.0-1** | New `/etc/security/limits.d/*` rules — **wants reboot to take effect**. |
| `nvidia-spark-ota-check` | absent | **1.0.11-1** | Periodic OTA poll service. |
| `iproute2` | 6.1.0-1ubuntu6.2 | unchanged | Skipped by `safe` mode (likely needs a new dependency); will retry under `full` mode in the next maintenance window. |

`/etc/dgx-release` did NOT advance to a new `DGX_OTA_VERSION` line — the 26.04.1 metapackage is the *infrastructure* for the next OTA, but the OS-level OTA bump still has to be triggered (via DGX dashboard, `nvidia-spark-ota-check` daemon, or a reboot).

## Repo changes this run

- **`roles/vllm_stacked_container/tasks/main.yml`** — both `docker run` invocations (head and worker) changed from `-c 'ray start ...'` to `-lc 'ray start ...'`. Login shell sources `/etc/profile.d/*` so `ray` and friends are on PATH regardless of which NGC image variant we're on. Forward-compat with 26.04+ once the next recreate replays the new spec into the running containers (and thus into anything the autoupgrade daemon captures via `docker inspect`).
- **`inventory/group_vars/sparks.yml`** —
  - `OTEL_SDK_DISABLED: "true"`, `OTEL_METRICS_EXPORTER: "none"`, `OTEL_TRACES_EXPORTER: "none"`, `OTEL_LOGS_EXPORTER: "none"` added to `vllm_distributed_extra_env` (kills the periodic OtlpGrpcMetricExporter that triggers the `getenv()` SIGSEGV).
  - `HF_HUB_OFFLINE: "1"` re-added to `vllm_distributed_extra_env`. We have `hf-prefetch.service` to guarantee local completeness; `transformers` should never need to talk to HF Hub at vLLM startup.
- **`roles/spark_provision/tasks/main.yml`** — `Phase — APT upgrade` `include_role` now carries `apply: { tags: [spark, apt] }` so `--tags apt` actually runs the role's inner `apt update`/`upgrade`/`autoremove` tasks. Same fix should be considered for the other `include_role` lines in this file (vllm_ngc_stack already fixed in earlier runs).
- **`justfile`** — six new recipes:
  - `spark-reboot-required` — checks `/var/run/reboot-required` on both Sparks.
  - `spark-apt-upgrade` — wraps `--tags apt` (now actually works); fallback documented.
  - `spark-reboot` — graceful cluster-wide reboot (parallel; stops containers first; waits for hosts to come back; reminds operator to run `spark-vllm-api-restart` after).
  - `spark-autoupgrade-status` — daemon state + `state.json`.
  - `spark-autoupgrade-disable` / `spark-autoupgrade-enable` — operator hooks for the autoupgrade daemon. Comment in `enable` warns to only run AFTER a fresh recreate has rolled the `bash -lc` fix into the running container's argv.

## Operator cheatsheet (pasted from the recovery)

If this happens again (or we follow the recommended cycle):

```
# 1. cluster crashed because nvidia[12] powered off
just spark-vllm-ps             # confirm containers state
ping <missing host LAN IP>     # confirm host is down
# physical power cycle the offline host

# 2. once host is back
just spark-vllm-ps             # head + worker should be Up via --restart unless-stopped
just spark-vllm-api-restart    # relaunch vllm serve in head (Ray cluster is fine)

# 3. if autoupgrade daemon is in error state
just spark-autoupgrade-status   # confirm
just spark-autoupgrade-disable  # while triaging
# fix the underlying issue (today: bash -c → bash -lc role fix)
just spark-vllm-provision-recreate   # rebuild containers with new spec
just spark-autoupgrade-enable        # re-arm

# 4. apt upgrade and reboot
just spark-apt-upgrade
just spark-reboot-required
# if reboot needed:
just spark-reboot               # parallel; ~3 min downtime
just spark-vllm-api-restart     # relaunch model
```

## Open follow-ups

- [ ] Operator-triggered cluster reboot for `nvidia-spark-limits` to take effect — `just spark-reboot` is ready, schedule for next maintenance window.
- [ ] `iproute2 6.1.0-1ubuntu6.2 → 6.1.0-1ubuntu6.3` is still listed as upgradable. Try `spark_apt_upgrade_mode: full` once during a maintenance run, or install directly via `ansible sparks -b -m apt -a 'pkg=iproute2 state=latest'`.
- [ ] Re-arm `vllm-stack-autoupgrade.service` only **after** the next `spark-vllm-provision-recreate` has rolled the `bash -lc` fix into the running container's argv (so the daemon's next `docker inspect` captures the safer command). Without this step, the daemon will replay the broken `bash -c` spec on the next image bump and re-fail. Use `just spark-autoupgrade-enable` when ready.
- [ ] **Both hosts now confirmed-affected by abrupt power-off.** The 2026-04-27 nvidia1 postmortem's hardening recommendations (arm kdump on both, run a stress test under monitoring, compare BIOS versions, environmental check for shared mains/circuit) should now be applied to both. The ASUS Ascent firmware update from `0100.*` → `0103.*` did **not** fix the issue — both hosts have crashed since.
- [ ] Apply the same `apply: { tags: [...] }` fix to other `include_role` lines in `roles/spark_provision/tasks/main.yml` (defensive — at minimum check `firewall`, `cuda`, `docker`, `vllm` includes).
- [ ] When the next NGC `nvcr.io/nvidia/vllm:YY.MM-py3` tag lands, **manually** test `spark-vllm-provision-recreate -e vllm_stacked_container_image=nvcr.io/nvidia/vllm:<new>-py3` against the inventory before re-arming the autoupgrade daemon. Treat NGC image promotions as needing a manual A/B until we're more confident in PATH/entrypoint stability across tags.

## Update 2026-04-29 (afternoon) — 26.04-py3 deeper root cause + final pin

After the morning rollback + apt upgrade, the operator ran the recommended
sequence: `just spark-reboot` (applied `nvidia-spark-limits` ulimits),
`just spark-vllm-provision-recreate` (rolled the new `bash -lc` argv into the
running containers), `just spark-autoupgrade-enable` (re-armed the daemon).

The daemon sat in `waiting_quiet` for ~5 min, then attempted promotion to
`nvcr.io/nvidia/vllm:26.04-py3` **again**. It failed in **exactly the same way**
as the morning attempt: `/bin/bash: line 1: ray: command not found`, head
container `Restarting (127)`. The `bash -lc` change had **not** fixed it.

Direct probe of the 26.04 image showed why:

```
$ docker run --rm --entrypoint /bin/bash nvcr.io/nvidia/vllm:26.04-py3 \
    -c "find / -maxdepth 6 -name ray -type f -executable 2>/dev/null"
(empty — no `ray` anywhere in the image)

$ docker run --rm --entrypoint /bin/bash nvcr.io/nvidia/vllm:26.04-py3 \
    -c "find / -maxdepth 6 -name vllm -type f -executable 2>/dev/null"
/usr/local/bin/vllm

$ docker run --rm --entrypoint /bin/bash nvcr.io/nvidia/vllm:26.04-py3 \
    -lc "echo \$PATH"
/usr/local/lib/python3.12/dist-packages/torch_tensorrt/bin:/usr/local/cuda/bin:
  /usr/local/nixlbench/bin:/usr/local/nixl/bin:/usr/local/nvidia/bin:
  /usr/local/cuda/bin:/usr/local/mpi/bin:/usr/local/sbin:/usr/local/bin:
  /usr/sbin:/usr/bin:/sbin:/bin:/usr/local/ucx/bin:/opt/amazon/efa/bin:
  /opt/tensorrt/bin:/opt/mellanox/doca/tools/
```

`ray` is **not in the image at all** — and `vllm` is still at the old path.
NVIDIA repackaged 26.04 to drop the bundled Ray console script (presumably
expecting a different multi-node mechanism, or to be installed at runtime).
The `bash -c` → `bash -lc` change *was* correctly captured by the autoupgrade
daemon's `docker container inspect` on the recreated container's argv (the
new failed head container's `Cmd: ['-lc', 'ray start ...']` confirms it),
so the role fix was structurally correct — but no shell flavor finds a
binary that doesn't exist.

### Pinned to 26.03-py3

`vllm_autoupgrade_pinned_tag` in `inventory/group_vars/sparks.yml` flipped
from empty to **`"26.03-py3"`** with a comment recording the root cause and
the conditions for lifting the pin (NVIDIA reinstates `ray`, OR our role
switches to a `python -m ray ...` invocation once we know where Python lives
in 26.04+). The autoupgrade daemon now reports
`candidate_tag = 26.03-py3 = current_image`, `status: ready`, `last_error:
null` — idle, no promotion will be attempted.

### Cleanup of the `include_role` tag-propagation wart

The same recurring bug that hid the apt upgrade this morning **also hid the
autoupgrade config re-render** when we tried to apply the pin via
`--tags vllm_autoupgrade`. Same symptom: `PLAY RECAP nvidia1 ok=2 changed=0`,
no role tasks ran. Fixed across the whole file:
`roles/spark_provision/tasks/main.yml` now adds `apply: { tags: [...] }` to
**every** `include_role` (sudoers, cuda apt-cleanup, spark_apt, docker,
firewall, spark_hosts, cuda toolkit, hf_prefetch_service, ngc_image_service,
vllm_stack_autoupgrade, spark_diagnostics — `vllm_stacked_container` already
had it from earlier). Header comment added so the pattern is visible to
future operators.

### Final cluster state (post-pin)

```
docker ps:    head + worker Up on 26.03-py3, both with /bin/bash -lc 'ray …'
ray status:   2 ALIVE nodes, 2.0/2.0 GPU reserved in placement groups
ss :8000:     LISTEN 0.0.0.0:8000 (vllm)
/v1/models:   gpt-4o-mini / Qwen/Qwen3.6-35B-A3B-FP8 JSON
autoupgrade:  active, status=ready, current==candidate==26.03-py3, no error
```

### Open follow-ups (updated)

- [ ] **26.04-py3 launch pattern (RESOLVED — root cause + plan)**:
  NVIDIA dropped Ray entirely in 26.04 and bumped vLLM `0.17.1 → 0.19.0`.
  The post-Ray multi-node story in vLLM 0.19 is the
  `--distributed-executor-backend external_launcher` (a.k.a.
  `ExecutorWithExternalLauncher` in `vllm/v1/executor/uniproc_executor.py`),
  driven by **`torchrun`** rather than `ray start`. Both Sparks run an
  identical container with one `torchrun --nnodes 2 --nproc-per-node 1
  --rdzv_backend c10d --rdzv_endpoint <node0>:29500 -m
  vllm.entrypoints.openai.api_server <model> --tensor-parallel-size 2
  --distributed-executor-backend external_launcher` invocation; both engines
  run in lock-step with deterministic scheduling, NCCL handles data plane,
  Gloo handles control plane, only rank 0 returns to the HTTP client. No
  Ray dashboard. Two important gotchas: (1) `VLLM_ENABLE_V1_MULTIPROCESSING=0`
  is required for deterministic scheduling under `external_launcher`;
  (2) `build.nvidia.com/spark/vllm/stacked-sparks` (NVIDIA's own published
  playbook) is **out of date** as of 2026-04-29 — still references
  `nvcr.io/nvidia/vllm:25.11-py3` and `run_cluster.sh` (Ray flow), no mention
  of `external_launcher`. Vendor docs haven't caught up to the 26.04 image,
  which is a strong signal **not to migrate yet**.

  **Migration sketch** (multi-day work, not a 5-min flip — keep the pin until
  we're ready):

  1. New role `roles/vllm_torchrun_stacked/` (sibling to
     `roles/vllm_stacked_container`); both Sparks run identical containers
     with the `torchrun … -m vllm.entrypoints.openai.api_server …` Cmd.
     `RANK` from `host_vars` (0=leader, 1=follower), `MASTER_ADDR` = leader's
     interconnect IP. Add `VLLM_ENABLE_V1_MULTIPROCESSING=0` to
     `vllm_distributed_extra_env`.
  2. New justfile recipes mirroring `spark-vllm-*`
     (`spark-vllm-torchrun-{provision,start,stop,…}`) so we can A/B against
     the Ray stack until we're confident. Keep both roles installable from
     the same inventory; flip via a `vllm_stack_kind: ray|torchrun` var.
  3. Retire or rewrite `vllm-stack-autoupgrade.service`'s `bounce()` —
     current code captures+replays a Ray-shaped container spec; torchrun
     topology is different (no head-vs-worker, single Cmd, no Ray GCS to
     wait for). Either teach the daemon both shapes or pin permanently and
     drive cutovers manually via a `playbooks/cutover_torchrun.yml` (same
     pattern as `cutover_roce.yml`).
  4. Drop Ray dashboard plumbing (`vllm_stacked_container_dashboard_*`,
     8265 in firewall) — torchrun doesn't have one. Compensate with the
     existing Prometheus `:8000/metrics` (already what
     `vllm-stack-autoupgrade` quiet-window gate reads) plus optional
     Grafana on ms02.
  5. Re-validate every wiki performance number under torchrun
     (FP8 cutover throughput, 256k context, RoCE+GDR rates) — vLLM 0.19's
     scheduler differences may move the numbers.
  6. **Then** lift `vllm_autoupgrade_pinned_tag` (or set it to the new
     known-good torchrun-compatible image), and switch the autoupgrade
     daemon back on if it's been adapted to the new shape.

- [ ] **`ngc-image-sync.service` is still pulling 26.04 onto both hosts** —
  it's harmless (just disk space; ~19 GB) but consider adding a similar
  pin-list to the image-sync daemon so we don't continue pulling tags we
  can't run. Defer until we have a confident 26.04 launch.

## Outcome

Cluster fully recovered: 2 ALIVE Ray nodes, vLLM serving Qwen3.6-35B-A3B-FP8
at `:8000`, `/v1/models` returns the expected JSON. apt upgrade landed on
both hosts with the `dgx-spark-ota-update-meta 26.04.1` infrastructure
metapackage in place. **Six** role + inventory fixes shipped (the morning
five plus the afternoon `vllm_autoupgrade_pinned_tag` change and the wider
`apply: { tags: [...] }` cleanup). Autoupgrade daemon armed and pinned to
26.03-py3. Documented operator surface (`spark-reboot*`, `spark-apt-upgrade`,
`spark-autoupgrade-*`) for the next recurrence.
