---
title: google/gemma-4-31B-it (blocked on NGC image)
kind: entity
status: blocked
tags: [model, gemma, instruct, tp2, blocked]
updated: 2026-04-18
related:
  - entities/hf-cache-sparks.md
  - concepts/stacked-vs-single-node.md
  - concepts/ngc-image-transformers-lag.md
---

> **Blocked 2026-04-18.** The `transformers` shipped in `nvcr.io/nvidia/vllm:26.01-py3`
> does not yet know the `gemma4` model type (pydantic ValidationError on ModelConfig).
> Target swapped to `google/gemma-3-27b-it` for now — see
> [concepts/ngc-image-transformers-lag.md](../concepts/ngc-image-transformers-lag.md).
> Re-visit when a newer NGC tag lands.


# google/gemma-4-31B-it

Target stacked-TP=2 model for the Spark pair. Hugging Face responds 200 to
`/api/models/google/gemma-4-31B-it` so the repo exists.

## Sizing / why TP=2

- 31B parameters; bf16/fp16 weights alone are ~62 GB — exceeds a single GB10's
  unified memory comfortably, hence TP=2 across the pair.
- Current serve args: `--max-model-len 32768`, `--load-format fastsafetensors`.
- `--enforce-eager` left off; turn on (`vllm_enforce_eager: true`) only if kernel
  compilation fails on the current GPU stack.

## Known gotchas

- **Transformers compatibility**: Gemma 4 requires a `transformers` build with
  `gemma4` in `CONFIG_MAPPING`. PyPI wheels have lagged; `sparks.yml` sets
  `vllm_transformers_from_git: true`. Current failure mode
  ([transformers-huggingface-hub-mismatch](../concepts/transformers-huggingface-hub-mismatch.md))
  is caused by this knob — pinning to a specific tag is the fix for the bare-metal path,
  but the NGC image ships its own matched transformers build.
- **Weight download is ~62 GB** — prefetch via `spark_provision_hf: true` (already
  cached on `nvidia1`, see [entities/hf-cache-sparks.md](./hf-cache-sparks.md)).
- First engine init is slow (systemd launcher sleeps 600s to let Ray settle; vLLM's
  engine-ready timeout is raised to 7200s).

## Smoke test (once API is up)

```
curl -s http://nvidia1:8000/v1/models | jq
curl -s http://nvidia1:8000/v1/chat/completions -H 'Content-Type: application/json' \
  -d '{"model":"google/gemma-4-31B-it","messages":[{"role":"user","content":"ping"}]}' | jq
```
