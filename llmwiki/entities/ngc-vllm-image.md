---
title: nvcr.io/nvidia/vllm NGC image
kind: entity
status: active
tags: [ngc, docker, vllm, image]
updated: 2026-08-17
related: [concepts/ngc-stacked-container-stack.md, sources/nvidia-stacked-sparks.md]
---

# NGC vLLM container image

Official NVIDIA-built vLLM container for Blackwell sm_121, distributed via NGC
(`nvcr.io/nvidia/vllm:<tag>`). Carries a coherent `torch` + `flashinfer` +
`fastsafetensors` + `transformers` stack per tag, precompiled for aarch64 + CUDA.

## Tags we've used / observed

| Tag | Where | Notes |
|---|---|---|
| `25.11-py3` | `roles/vllm_stacked_container/defaults/main.yml` default | NVIDIA stacked-sparks doc example. |
| `26.01-py3` | pulled on both `nvidia1` and `nvidia2` (2026-04-18) | Older pin. |
| `26.03-py3` | **production** (`sparks.yml`) | Live 2026-08-17: vLLM **0.17.1**, transformers **4.57.5**. Has `Qwen3_5*` in vLLM registry. **Too old for Qwen3.8 VL processor (needs transformers ≥5.8.0).** |
| `26.04-py3` | tried 2026-04-29 | Dropped bundled `ray` on PATH — rolled back. |
| `26.07-py3` | **pulled** by ngc-image-sync; **not serving** | Live probe 2026-08-17: vLLM **0.24.0**, transformers **5.6.1**, `qwen3_5` in CONFIG_MAPPING, **`ray` not installed**. GB10: xgrammar tool-call 500s; Qwen3.6-FP8 DeepGEMM assert reported. |
| `26.06-py3` | pulled; not serving | vLLM **0.22.1**, transformers **5.6.0**, **`ray` not installed**. NGC catalog “latest” as of 2026-08-17. |

## Why we prefer this over the bare-metal venv

- No `pip` dependency resolution on the host → no
  [transformers-huggingface-hub-mismatch](../concepts/transformers-huggingface-hub-mismatch.md).
- Consistent across both Sparks by construction (same image hash).
- One knob to upgrade the whole stack: change the tag, re-pull, recreate containers.

## Invariants the role enforces

- `--network host` (Ray GCS uses fixed ports over the QSFP interconnect).
- `--gpus all` + `--shm-size 10.24g` (per NVIDIA doc).
- `-v {{ vllm_hf_home }}:/root/.cache/huggingface` — HF cache mounted read/write.
- `--env-file` carrying `VLLM_HOST_IP` + `MASTER_ADDR` + the
  `vllm_distributed_extra_env` block from `sparks.yml`.

## Pull + size

`docker images` on both Sparks reports **~14 GB** for the image content.
