---
title: GLM-5.3-Flash on GB10
kind: concept
status: active
tags: [glm, vllm, gb10, nvfp4]
updated: 2026-08-29
sources:
  - ../sources/glm53-flash.md
related:
  - ../entities/model-glm53-flash.md
  - ../concepts/ngc-image-transformers-lag.md
---

# GLM-5.3-Flash on this cluster

## What we would need (2× nvidia1/nvidia2)

1. **Do not serve official FP8/BF16** on 2 Sparks. Prefetch
   `LibertAIDAI/GLM-5.3-Flash-NVFP4` (~181 GiB) via hf-prefetch; optional
   `incoai/GLM-5.3-Flash-DFlash2` (~2.2 GiB) for speculative decode.
2. **Leave NGC 26.03 / Ray Qwen up or swap** — cannot share one
   `vllm serve` image. Cutover means a second container image
   (`glm53-flash-arm64-cu130` + SM121 patches) and torchrun/mp TP=2, not
   today's Qwen argv.
3. GB10-mandatory flags from day-0 deploys:
   - `--moe-backend marlin` (weight-only NVFP4 otherwise silent zeros)
   - `--language-model-only` unless vision is in-scope (frontend OOM)
   - SM121 sparse-indexer bind-mount (decode crash past ~24k)
   - `--reasoning-parser deepseek_r1` (vLLM `glm45` drops the reply)
   - `--tool-call-parser glm47` (not `glm` / not `qwen3_coder`)
   - `--trust-remote-code`, `VLLM_ENGINE_READY_TIMEOUT_S=3600`
   - drop page cache on both hosts before boot (same UMA ritual)
4. First boot target: `--max-model-len 262144`, `--gpu-memory-utilization
   0.85`, let the profiler size KV (do not pin `--kv-cache-memory`).
   Measured TP=2 + fp8 KV + DFlash2: **581k** token pool, ~47 tok/s.

## Why more Sparks later

Operator has **two more GB10s ready**, blocked on extra external cooling
(not in `inventory/hosts.yml` yet). Those turn 2× → 4×.

- **2× (today, NVFP4):** enough to *run* it. Community-proven TP=2 @ 262k,
  ~47 tok/s with DFlash2. Official FP8 does **not** fit.
- **4× (after cooling):** unlocks official FP8 with KV headroom, native 1M
  KV on NVFP4, and ~68 tok/s (community TP=4). 3× is theoretically over
  FP8 weight size and operationally tight — wait for the fourth.
- BF16 is still an 8-Spark-class model on this UMA.

## Deploy (2026-08-29, aborted)

- Role `roles/vllm_glm53_stacked` landed (sm121-v8, marlin,
  language-model-only, 262k, SM121 indexer bind-mount).
- Weights reached `ready` (~182G) on both hosts. Cutover pulled the
  image, started worker+leader, then operator aborted the `/health` wait.
- Hub trees deleted the same evening to free disk for DeepSeek-V4-Flash.
  Image `ghcr.io/tonyd2wild/vllm-glm53-flash:sm121-v8` may still be local.
- Live serve: Qwen3.8 `:8000`. Next intended profile: `deepseek_v4_flash`.
