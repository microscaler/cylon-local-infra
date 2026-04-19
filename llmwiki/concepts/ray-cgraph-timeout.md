---
title: Ray compiled-DAG timeout (RayChannelTimeoutError)
kind: concept
status: active
tags: [ray, tp, failure-mode, tuning]
updated: 2026-04-18
related: [concepts/bare-metal-venv-stack.md, concepts/ngc-stacked-container-stack.md]
first_observed: 2026-04
---

# Ray compiled-DAG 300s read timeout kills TP workers

## Symptom

Long-running stacked TP=2 steps (large models, first-token prefill with long context)
produce:

```
RayChannelTimeoutError: ...
EngineDeadError: ...
```

The vLLM engine dies mid-step, systemd restarts the API, and the model starts reloading
from scratch. Pattern: steady state serves fine, but one heavy request takes the whole
engine down.

## Why

vLLM v1 uses Ray's **compiled DAG** for TP collectives. The compiled DAG has a per-read
timeout defaulting to `300 s`. On Spark + GB10 + stacked NCCL-over-sockets, some
steps legitimately take longer than 300 s (first prefill with `max-model-len=32768`,
or a step that coincides with page-cache thrash). When a single read times out, the
worker is declared dead.

## Fix

Raise the timeout via env. We set **900 s** as our default:

```yaml
# inventory/group_vars/sparks.yml
vllm_distributed_extra_env:
  RAY_CGRAPH_get_timeout: "900"
  ...
```

This env is passed into:
- The bare-metal `vllm-stacked.service` (Environment= and wrapper exports).
- The NGC container env-files (`head.env`, `worker-<host>.env`) via
  `vllm_distributed_extra_env`.

## Tuning

- Start at `900`; raise to `1800` only if the symptom recurs on first-token of
  pathological prompts.
- Do not set to `0` (disable) — then a hung step hangs forever and no supervisor
  notices.

## Cross-refs

- Current value in `sparks.yml` (diff vs committed): replaced `HF_HUB_OFFLINE` with
  `RAY_CGRAPH_get_timeout: "900"`.
