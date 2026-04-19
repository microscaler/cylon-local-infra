---
title: Hugging Face cache on Sparks
kind: entity
status: active
tags: [hf, cache, storage]
updated: 2026-04-18
related: [concepts/ngc-stacked-container-stack.md]
sources: [../../docs/hf-cache-sparks.md]
---

# HF cache on Sparks

The canonical path is **`/home/nvidia/.cache/huggingface`** owned by user `nvidia`.
The `vllm_hf_home` and `vllm_stacked_container_hf_home` variables both resolve to this
by default.

## Contents (as of 2026-04-18, nvidia1)

```
/home/nvidia/.cache/huggingface/hub/
  models--google--gemma-4-31B-it/
  models--TinyLlama--TinyLlama-1.1B-Chat-v1.0/
  CACHEDIR.TAG
```

Not yet audited on `nvidia2` (cache mirroring is manual — the `hf_prefetch_models`
list runs on every Spark, so a fresh prefetch will populate both).

## Mount into NGC container

The `vllm_stacked_container` role mounts this host path at `/root/.cache/huggingface`
inside the container:

```
-v /home/nvidia/.cache/huggingface:/root/.cache/huggingface
```

`HF_HOME` inside the container is the default `/root/.cache/huggingface` so no extra
env is needed.

## Permissions gotcha

Under default mode, only `nvidia` can `ls` this directory; `casibbald` needs `sudo`.
Don't rely on `casibbald` probes without `sudo -u nvidia` or `sudo ls`.

## See also

- [`docs/hf-cache-sparks.md`](../../docs/hf-cache-sparks.md) — cache layout + diagnostics.
