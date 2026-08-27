---
title: Qwen/Qwen3.8-27B (and official FP8)
kind: entity
status: active
tags: [qwen, model, vision, fp8]
updated: 2026-08-19
sources:
  - ../sources/qwen38-27b.md
  - ../../inventory/group_vars/sparks.yml
related:
  - ../entities/ngc-vllm-image.md
  - ../concepts/ngc-image-transformers-lag.md
  - ../concepts/qwen38-yarn-1m.md
---

# Qwen3.8-27B

Production default as of 2026-08-17: **`Qwen/Qwen3.8-27B-FP8`**. Do **not**
serve BF16 `Qwen/Qwen3.8-27B`. Dense VL hybrid (`Qwen3_5ForConditionalGeneration`),
not MoE. Successor to `Qwen/Qwen3.6-35B-A3B-FP8`.

| Field | Value |
|---|---|
| HF (BF16 — do not serve) | `Qwen/Qwen3.8-27B` |
| HF (production) | `Qwen/Qwen3.8-27B-FP8` |
| Arch | `Qwen3_5ForConditionalGeneration` (dense VL, hybrid attn) |
| vs previous | 27B **dense** vs 35B **MoE / 3B active** FP8 |
| Fleet `--max-model-len` | **524288** YaRN factor 2.0 (2026-08-19). 1M is factor 4.0 opt-in — [qwen38-yarn-1m](../concepts/qwen38-yarn-1m.md) |

## Fit on this cluster (2× GB10, NGC 26.03-py3)

Probed live on `vllm-ngc-ray-head` 2026-08-17; still serving on 26.03 after the
2026-08-19 YaRN 512 Ki raise.

| Check | Result |
|---|---|
| Image | `nvcr.io/nvidia/vllm:26.03-py3` |
| vLLM | `0.17.1+a03ca76a.nv26.03` — registry **has** `Qwen3_5ForConditionalGeneration` |
| transformers | **`4.57.5`** — recipe wants **`>=5.8.0`** (text serve still boots) |
| Memory | FP8 ~38 GiB weights; TP=2 + fp8 KV at 524288 = **1,111,712** tokens (8.32× full-window concurrency) |

NGC **26.07** has transformers **5.6.1** (still &lt; 5.8) plus missing `ray` —
stay on 26.03 for the Ray stack
([ngc-image-transformers-lag](../concepts/ngc-image-transformers-lag.md)).

## Serve flags that differ from the 3.6 MoE stack

1. No `--chat-template /vllm-patches/unsloth.jinja`.
2. Keep `--tool-call-parser qwen3_coder` + `--reasoning-parser qwen3`.
3. `--mm-encoder-tp-mode data`.
4. Restart API only: `just spark-vllm-api-restart` (or tagged
   `vllm_ngc_stack,spark_assert`). Avoid full `--recreate` unless needed —
   `spark_nfs_client` chown of `/mnt/ms02-hf-archive` fails under root_squash.
