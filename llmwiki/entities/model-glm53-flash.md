---
title: zai-org/GLM-5.3-Flash
kind: entity
status: stale
tags: [glm, model, moe, vision, fp8]
updated: 2026-08-29
sources:
  - ../sources/glm53-flash.md
related:
  - ../concepts/glm53-flash-on-gb10.md
  - ../entities/model-qwen3_8-27b.md
  - ../entities/ngc-vllm-image.md
---

# GLM-5.3-Flash

**Parked 2026-08-29.** First boot on sm121-v8 was aborted mid-`/health`
(operator: too early / not stable). Hub tree deleted on both Sparks.
Profile role stays wired; do not prefetch or cut over unless asked.

Candidate successor-class model vs production
`Qwen/Qwen3.8-27B-FP8`. Do **not** point `vllm_default_model` at the official
BF16 or FP8 repos on the current 2× GB10 NGC 26.03 Ray stack.

| Field | Value |
|---|---|
| HF (default, native FP8) | `zai-org/GLM-5.3-Flash` (~306–331 GiB) |
| HF (BF16 — do not serve here) | `zai-org/GLM-5.3-Flash-BF16` (~640+ GiB) |
| HF (fits 2× GB10) | `LibertAIDAI/GLM-5.3-Flash-NVFP4` (~181 GiB) |
| Arch | `Glm5NextForConditionalGeneration` |
| Size | 320B total / **18B active** MoE |
| Context | native 1,048,576 |
| vs production Qwen3.8 | much larger MoE; hybrid KDA+MLA; always-on thinking |

## GB10 count

Each Spark exposes ~119.6 GiB to CUDA (UMA). Two units ≈ **239 GiB**.

| Checkpoint | Weights | Min GB10 | Practical |
|---|---|---|---|
| Official FP8 | ~306–331 GiB | **3** (no KV headroom) | **4** |
| Official BF16 | ~640–772 GiB | **6** | **8** (matches 8-spark plan class) |
| NVFP4 (community) | ~181 GiB | **2** (proven) | **2** at 262k; **4** for 1M + ~68 tok/s |

**1× GB10 cannot hold even NVFP4.** Official FP8 does not fit our current pair.

## Engine

NGC `26.03-py3` / vLLM 0.17.1 has **no** `glm5_next`. Need
`vllm/vllm-openai:glm53-flash-arm64-cu130` plus GB10 patches, or
`ghcr.io/tonyd2wild/vllm-glm53-flash:sm121-v11-dflash2`. That is a **second
stack**, not a flag swap on the Qwen Ray image.

Parsers (vLLM): `--tool-call-parser glm47`, `--reasoning-parser deepseek_r1`,
`--enable-auto-tool-choice`. SGLang uses `glm45` for reasoning — do not copy
that name onto vLLM.
