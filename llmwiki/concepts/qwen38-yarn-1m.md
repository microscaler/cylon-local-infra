---
title: Qwen3.8-27B YaRN 1M context
kind: concept
status: active
tags: [qwen, vllm, yarn, context]
updated: 2026-08-19
sources:
  - ../sources/qwen38-27b.md
  - https://huggingface.co/Qwen/Qwen3.8-27B
  - https://forums.developer.nvidia.com/t/qwen3-8-27b-nvfp4-on-a-single-dgx-spark-up-to-1m-context-vllm-mtp-measurements/380244
related:
  - ../entities/model-qwen3_8-27b.md
  - ../../inventory/group_vars/sparks.yml
---

# Qwen3.8-27B YaRN → 1M tokens

**Native window is 262,144.** 1M is **not** in the checkpoint. It is a launch-time
YaRN RoPE scale (`factor: 4.0` → `262144 × 4 = 1_048_576`).

Official vLLM line (same for BF16 and official FP8):

```bash
VLLM_ALLOW_LONG_MAX_MODEL_LEN=1 vllm serve Qwen/Qwen3.8-27B-FP8 \
  --max-model-len 1000000 \
  --hf-overrides '{"text_config":{"rope_parameters":{"mrope_interleaved":true,"mrope_section":[11,11,10],"rope_type":"yarn","rope_theta":10000000,"partial_rotary_factor":0.25,"factor":4.0,"original_max_position_embeddings":262144}}}'
```

We already set `VLLM_ALLOW_LONG_MAX_MODEL_LEN=1` in `sparks.yml` (needed when
`--max-model-len` meets or exceeds native `max_position_embeddings`).

## Traps (from Qwen card + GB10 NVFP4 write-up, 2026-08-15)

1. Override **must** nest under `text_config`. Top-level `rope_parameters` is
   silently ignored; vLLM stays at 262k and rejects a larger `--max-model-len`.
2. `--hf-overrides` **replaces** the dict, does not merge. Omit
   `mrope_section` / `mrope_interleaved` / `partial_rotary_factor` / `rope_theta`
   and multimodal RoPE breaks.
3. YaRN in vLLM/SGLang is **static** — every request, including short ones,
   uses the scaled RoPE. Qwen: enable only when you actually need >262k.
   For a typical 512k workload use `factor: 2.0`, not 4.0.
4. Official `Qwen/Qwen3.8-27B-FP8` should have `tokenizer.json` `truncation: null`.
   Unsloth NVFP4 packs have shipped `truncation.max_length: 2048` (silent
   prompt cut). Check after download; do not copy Unsloth tokenizer wholesale.

## Memory on this cluster (2× GB10, FP8 KV, hybrid attn)

Only **16/64** layers are full attention (4 KV heads × dim 256). Linear-attn
state is ~constant per sequence. Spark-arena measurement (NVFP4, TP=1):
**~37 KiB/token** KV → **~36 GiB for 1M**. Our TP=2 + `gpu_memory_utilization
0.80` historically provisioned **70+ GiB** KV on the MoE stack — 1M should
**fit**. Fleet is YaRN **factor 2.0 / `--max-model-len` 524288** as of
2026-08-19 evening (was native 262144). Live 26.03 serve: GPU KV
**1,111,712** tokens, **8.32×** concurrency at 524288. Factor 4.0 / 1M
stays opt-in.

## Fleet (2026-08-19)

`--max-model-len 524288` + `--hf-overrides "$(cat /vllm-patches/qwen38-yarn.json)"`
with `factor: 2.0` (official 512 Ki step). JSON is staged by
`roles/vllm_stacked_container` — do **not** inline the object in
`vllm_api_server_extra_args` (serve starts inside `bash -lc`; raw JSON
brace-expands on `mrope_section` commas).

1M remains factor 4.0 + `--max-model-len 1000000` in that same file.
