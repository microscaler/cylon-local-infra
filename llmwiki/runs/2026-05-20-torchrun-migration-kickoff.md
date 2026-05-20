---
title: Torchrun migration kickoff — move away from Ray
kind: run
status: in-progress
tags: [torchrun, vllm, migration, 26.04, ray-deprecation]
updated: 2026-05-20
related:
  - concepts/ngc-stacked-container-stack.md
  - concepts/8-spark-fabric-and-orchestrator.md
  - runs/2026-04-29-cluster-recovery-and-26.04-rollback.md
  - roles/vllm_torchrun_stacked/README.md
---

# Torchrun migration kickoff — move away from Ray

**Baseline tagged:** `spark-ray-baseline-2026-05-20` (commit `223505e`) — last known-good
2-Spark Ray + `26.03-py3` stack before this migration.

## Why

NGC **`nvcr.io/nvidia/vllm:26.04-py3` ships without `ray`** in any executable path.
vLLM 0.19+ multi-node TP expects **`torchrun`** + `--distributed-executor-backend external_launcher`.
The repo already has `roles/vllm_torchrun_stacked/` behind `vllm_stack_kind: torchrun`; production
still defaults to Ray on `26.03-py3`.

## Goal

Validate torchrun on the **live 2-Spark pair**, promote to default stack kind, then adapt
observability/autoupgrade before scaling to 8 nodes.

## Phases

### Phase 0 — Pre-flight (2-Spark, still Ray)

- [x] Tag `spark-ray-baseline-2026-05-20`
- [ ] Confirm both Sparks on pinned kernel `6.17.0-1018-nvidia` + NVIDIA modules present
- [ ] `just spark-provision` passes `spark_assert` on Ray stack
- [ ] Capture perf baseline (tokens/s, latency) for A/B comparison

### Phase 1 — Torchrun A/B on 26.03 (optional smoke)

1. Snapshot inventory (`vllm_stack_kind: ray`, image `26.03-py3`)
2. Maintenance window: set `vllm_stack_kind: torchrun` (keep `26.03-py3` initially if image still has torchrun)
3. `just spark-provision-recreate`
4. Verify: `just spark-vllm-torchrun-ps`, `just spark-vllm-torchrun-status`, `just spark-vllm-lan-probe`
5. Roll back to Ray if blocked; restore from tag baseline

### Phase 2 — Image bump to 26.04-py3

1. Set `vllm_stacked_container_image: "nvcr.io/nvidia/vllm:26.04-py3"`
2. Lift or replace `vllm_autoupgrade_pinned_tag` — autoupgrade daemon is Ray-shaped today
3. `just spark-provision-recreate`
4. Re-run perf A/B vs Phase 0 baseline

### Phase 3 — Make torchrun production default

1. Set `vllm_stack_kind: torchrun` in `inventory/group_vars/sparks.yml`
2. Update docs — torchrun is canonical
3. Refactor Ray-only just recipes or mark deprecated
4. Adapt `vllm_stack_autoupgrade` for torchrun topology (or disable until rewritten)

### Phase 4 — 8-node readiness

Before TP=8 fleet cutover (see 8-spark hardware run):

- Confirm torchrun rendezvous on interconnect `:29500` scales to 8 ranks
- NCCL env + per-host `NCCL_IB_GID_INDEX` validated under `external_launcher`
- Observability: `:8000/metrics` on rank 0 (no Ray dashboard)

## Rollback

```bash
# vllm_stack_kind: ray
# vllm_stacked_container_image: nvcr.io/nvidia/vllm:26.03-py3
just spark-provision-recreate
```

## Next action (operator)

Start Phase 0 verification, then schedule Phase 1 maintenance window for torchrun A/B.
