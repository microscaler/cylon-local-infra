---
title: TinyLlama/TinyLlama-1.1B-Chat-v1.0
kind: entity
status: active
tags: [model, tinyllama, smoketest]
updated: 2026-04-18
related: [entities/hf-cache-sparks.md, concepts/stacked-vs-single-node.md]
---

# TinyLlama-1.1B-Chat-v1.0

Small chat model kept in the HF cache on `nvidia1` for fast sanity checks of the
serving stack (Ray + vLLM + NCCL / sockets / HF cache mount) **without** waiting on a
62 GB Gemma load.

## Why useful

- Full load in seconds.
- Validates that the container can talk to HF cache, allocate GPU, bind the OpenAI API
  port, answer `/v1/models`, and complete `/v1/chat/completions` — end-to-end — before
  we commit to a long Gemma warm-up.
- Because it's 1.1B params, **use it with `--tensor-parallel-size 1`** on the leader
  only (no Ray needed in the minimal smoke mode) OR with TP=2 to exercise Ray+NCCL with
  a trivial weight transfer.

## Smoke launch inside the NGC head container

```
docker exec -d vllm-ngc-ray-head bash -lc \
 'vllm serve TinyLlama/TinyLlama-1.1B-Chat-v1.0 \
  --tensor-parallel-size 2 --distributed-executor-backend ray \
  --host 0.0.0.0 --port 8000'
```

## Risk

Sometimes TinyLlama smokes green but the Gemma load fails — the model-specific issues
(tokenizer, config, fastsafetensors) don't surface until the large model. Always
re-verify with the real target.
