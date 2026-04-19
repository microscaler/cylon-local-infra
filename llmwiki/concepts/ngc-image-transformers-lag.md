---
title: NGC image's `transformers` lags upstream
kind: concept
status: active
tags: [ngc, transformers, vllm, failure-mode]
updated: 2026-04-18
first_observed: 2026-04-18
related:
  - entities/ngc-vllm-image.md
  - concepts/ngc-stacked-container-stack.md
  - runs/2026-04-18-rip-out-bare-metal.md
---

# NGC image `transformers` lags upstream

## Symptom

`vllm serve <some-new-model>` crashes immediately with a pydantic `ValidationError`
on `ModelConfig`:

```
Value error, The checkpoint you are trying to load has model type `gemma4`
but Transformers does not recognize this architecture.
```

Observed on `nvcr.io/nvidia/vllm:26.01-py3` with `google/gemma-4-31B-it`. The image's
bundled `transformers` package is pinned per-tag and doesn't chase `main`.

## Implication for this repo

The whole reason we went container-only was to avoid host-side pip resolution
([runs/2026-04-18-rip-out-bare-metal.md](../runs/2026-04-18-rip-out-bare-metal.md)).
That trade still holds. The cost is that **the NGC image tag is the version you get**
for `transformers` too — if upstream adds a new architecture after the image was
frozen, you cannot serve models using it until NVIDIA ships a new tag.

## Policy

1. **Prefer models the image already supports.** When picking a target, check that the
   model's `config.model_type` is in the image's `transformers.models.auto.CONFIG_MAPPING`:
   ```
   docker exec vllm-ngc-ray-head python3 -c \
     'from transformers.models.auto.configuration_auto import CONFIG_MAPPING; \
      print("gemma4" in CONFIG_MAPPING, "gemma3" in CONFIG_MAPPING, "llama" in CONFIG_MAPPING)'
   ```
2. **Track the image tag we use** in
   [entities/ngc-vllm-image.md](../entities/ngc-vllm-image.md) with a list of verified
   model families. Append to it whenever we validate a new one.
3. **If we must serve a new-architecture model**, the options — in order of
   preference — are:
   1. Wait for the next NGC image tag and bump `vllm_stacked_container_image`.
   2. Pin the image to a specific digest we validated, and file a concept page here.
   3. Last resort: build a sidecar image layered on NGC with `transformers` upgraded.
      **Do not** `pip install` inside the stock NGC container at runtime — that
      drift will re-create the bare-metal problem the container stack exists to
      avoid.

## What we did (2026-04-18)

Swapped `vllm_default_model: "google/gemma-4-31B-it"` → `"google/gemma-3-27b-it"`
(also TP=2 target, ~54 GB bf16, a family the image's transformers does recognise).
Updated `hf_prefetch_models` to match. See
[runs/2026-04-18-ngc-container-bringup.md](../runs/2026-04-18-ngc-container-bringup.md).

## Cross-refs

- [entities/model-gemma-4-31b-it.md](../entities/model-gemma-4-31b-it.md) — now
  marked as a future target pending NGC image update.
