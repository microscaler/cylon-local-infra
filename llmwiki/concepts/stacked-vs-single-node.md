---
title: Stacked TP=2 vs single-node TP=1
kind: concept
status: active
tags: [vllm, tp, sizing, decision]
updated: 2026-04-18
related: [entities/model-gemma-4-31b-it.md, entities/model-tinyllama-1_1b.md, concepts/ngc-stacked-container-stack.md]
---

# Stacked TP=2 vs single-node TP=1

## Rule of thumb

| Model weights (bf16) | Recommended TP | Why |
|---|---|---|
| ≤ 20 GB | TP=1 on one Spark | fits comfortably in unified memory; Ray overhead not worth it. |
| 20–45 GB | TP=1 with aggressive KV-cache tuning OR TP=2 | depends on `--max-model-len`. |
| 45 GB+ | TP=2 across both Sparks | weights alone exceed one GB10; TP=2 is mandatory. |
| MoE / quantized large | case-by-case | AWQ/FP8/NVFP4 can fit big models into TP=1 — see `dgx-spark-playbooks/nvidia/nvfp4-quantization`. |

Gemma-4 31B at bf16 = ~62 GB → TP=2 mandatory. TinyLlama 1.1B at bf16 = ~2.2 GB →
TP=1 trivially fits; we keep it for fast smoke tests of the stacked path too.

## Configuration

- `vllm_tensor_parallel_size: 2` for the stacked path (default in `sparks.yml`).
- For TP=1 on the leader only: set `vllm_sparks_deploy_single_node_service: true` and
  `spark_provision_vllm_serve: true`. This runs a one-node `vllm.service` on the
  leader and **does not** require Ray. Useful when debugging engine / API issues in
  isolation.

## Don't

- Don't set `vllm_sparks_deploy_single_node_service: true` when running a model that
  needs TP=2 — the service will OOM on first forward pass and systemd-restart forever.
