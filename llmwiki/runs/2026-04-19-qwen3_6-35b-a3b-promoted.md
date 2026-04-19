---
title: Qwen3.6-35B-A3B promoted as the default served model
kind: run
status: success
date: 2026-04-19
hosts: [nvidia1, nvidia2]
tags: [vllm, qwen, tp2, ngc, stacked]
related:
  - ../entities/ngc-vllm-image.md
  - ../entities/hf-prefetch-service.md
  - ../concepts/ngc-stacked-container-stack.md
  - ../concepts/stacked-vs-single-node.md
  - ../runs/2026-04-19-26.03-py3-upgrade.md
  - ../runs/2026-04-19-autoupgrade-armed-qwen-queued.md
---

# Qwen3.6-35B-A3B promoted as the default served model

## Context

The 26.03-py3 image cutover on 2026-04-19 unlocked
`Qwen3_5MoeForConditionalGeneration` in vLLM 0.17.1's ModelRegistry.
`hf-prefetch.service` had already landed the weights on both Sparks:

```
Qwen/Qwen3.6-35B-A3B         ready   71.93 GB   cached + synced
Qwen/Qwen3-Coder-30B-A3B…    ready   61.08 GB   cached + synced
TinyLlama/TinyLlama-1.1B…    ready    2.20 GB   cached + synced
```

Operator ask: take Qwen3.6 for a spin.

## Change

Inventory (`inventory/group_vars/sparks.yml`):

```diff
-vllm_default_model: "TinyLlama/TinyLlama-1.1B-Chat-v1.0"
+vllm_default_model: "Qwen/Qwen3.6-35B-A3B"
-vllm_api_server_extra_args: []
+vllm_api_server_extra_args:
+  - "--max-model-len"
+  - "32768"
```

Rationale for `--max-model-len 32768`:
- Qwen3.6 supports 262144 native / 1M extended natively, but KV cache on
  2× GB10 unified memory caps us far earlier.
- 32k is the proven-safe first-boot value; we observed **70.66 GiB KV
  cache available** after init, so we can safely raise later.

## Execution

Because the stack was already running with 26.03-py3 + Ray cluster up, no
container recreate was needed — only the `vllm serve` process inside the
head container had to be replaced:

1. SIGTERM the existing `vllm serve TinyLlama/…` process inside
   `vllm-ngc-ray-head`. Ray worker actors cleanly transitioned to `DEAD`
   (verified via `ray list actors`).
2. `docker exec -d vllm-ngc-ray-head bash -lc 'exec >
   /root/vllm-serve.log 2>&1; vllm serve Qwen/Qwen3.6-35B-A3B
   --tensor-parallel-size 2 --host 0.0.0.0 --port 8000
   --distributed-executor-backend ray --max-model-len 32768'`
3. Tail `/root/vllm-serve.log` for boot milestones.

Why not `ansible-playbook --tags vllm_ngc_stack`? The parent role is
pulled in via `include_role` in `roles/spark_provision/tasks/main.yml`,
and tags on an `include_role` task do **not** propagate to the role's
internal tasks without `apply: { tags: [...] }`. The playbook ran in 4 s
but only matched the outer `Phase — NGC stacked vLLM` include, not the
inner start-serve task. Filed as a follow-up (below) — direct `docker
exec` was a 30 s detour rather than a 10 min debug session.

## Boot timeline (from `/root/vllm-serve.log`)

| Event | Wall time | Δ from launch |
|---|---|---|
| `vllm serve` launched | 14:48:10 | +0 s |
| Resolved `Qwen3_5MoeForConditionalGeneration` | 14:48:16 | +6 s |
| Ray placement group created + TP=2 workers spawned | 14:48:32 | +22 s |
| Safetensors shard 1/26 loaded | 14:48:37 | +27 s |
| Safetensors shard 26/26 loaded | ~14:49:30 | ~80 s |
| flashinfer autotuner ends | 14:50:34 | +144 s |
| `Available KV cache memory: 70.66 GiB` | 14:50:28 | +138 s |
| `init engine (profile, create kv cache, warmup) took 58.66 seconds` | 14:50:51 | +161 s |
| `Application startup complete.` | 14:51:08 | **+178 s** |

Total cold boot: **~3 minutes**. For comparison, TinyLlama on this same
stack boots in ~35 s; the extra time is 26 safetensors shards × ~2 s each
+ flashinfer autotune.

Notable log lines on the way through:

- `Resolved architecture: Qwen3_5MoeForConditionalGeneration` —
  confirmed in the pinned vLLM 0.17.1+a03ca76a.nv26.03.46967107 build.
- `Setting attention block size to 1056 tokens to ensure that attention
  page size is >= mamba page size.` — hybrid DeltaNet + attention MoE
  paging is alive.
- `Padding mamba page size by 0.76% to ensure that mamba page size and
  attention page size are exactly equal.` — expected with this arch.
- `Async scheduling will be disabled because it is not supported with
  the ``ray`` distributed executor backend` — expected with TP=2 stacked.
- `Not enough SMs to use max_autotune_gemm mode` — expected on GB10
  (60 SMs per chip, threshold is ~68).

## Verification

```
$ curl -sS http://localhost:8000/v1/models | jq '.data[] | {id, max_model_len}'
{
  "id": "Qwen/Qwen3.6-35B-A3B",
  "max_model_len": 32768
}

$ time curl -sS http://localhost:8000/v1/chat/completions \
    -H 'Content-Type: application/json' \
    -d '{"model":"Qwen/Qwen3.6-35B-A3B","max_tokens":200,"temperature":0.2,
         "messages":[{"role":"user","content":"Write a concise Python function
         quicksort(xs) that sorts a list of integers in place using Lomuto
         partition. Include a brief docstring."}]}'
…
usage: {"prompt_tokens":39,"total_tokens":239,"completion_tokens":200}

real    0m43.727s
```

So **~4.6 tok/s** at first-token-inclusive latency. Modest for a 35B MoE
(3B active) — explained partly by the first post-boot request paying the
CUDA graph / torch.compile cold-start tax (flashinfer autotuner had run
during init, but kernel specializations for the actual request shapes
are lazy).

Also notable: Qwen3.6 is a **reasoning model** by default. The 200-token
budget was almost entirely consumed by its `<think>` monologue before it
started emitting code. For pure coder workflows we'll want
`--reasoning-parser qwen3` on the next relaunch (expose `reasoning_content`
separately so apps can show / hide the chain-of-thought) or use the
`/chat/completions` `enable_thinking=false` hint in generation params.

## Safety rails still in force

- `vllm-stack-autoupgrade.service` is `enabled: true` and will bounce the
  stack onto any future NGC `YY.MM-py3` tag, but only after the tag has
  been `ready` on all nodes ≥ 1 h *and* the vLLM API has been quiescent
  for ≥ 5 min. On bounce it calls `docker inspect` + captures the live
  `vllm serve` argv — so it **will** replay
  `Qwen/Qwen3.6-35B-A3B --max-model-len 32768` with the new image, not
  fall back to TinyLlama or default args.
- To revert to TinyLlama for smoke work: edit
  `inventory/group_vars/sparks.yml` — two lines — and run the playbook
  (see follow-ups below).

## Follow-ups

- **Fix tag propagation in `roles/spark_provision`.** Change
  `include_role: name: vllm_stacked_container` to also carry
  `apply: { tags: [spark, vllm_ngc_stack, stack_ngc, vllm_container] }`
  (or switch to `import_role`) so
  `ansible-playbook … --tags vllm_ngc_stack` actually runs the role's
  inner tasks. Small, mechanical, unblocks operators running the role
  out-of-band.
- **Reasoning-parser flag.** Add `--reasoning-parser qwen3` to
  `vllm_api_server_extra_args` once we have a consumer that wants to
  hide `<think>` output. Until then, leave it off — the raw reasoning
  is useful during bring-up.
- **Context length.** 70.66 GiB KV cache available at max_model_len
  32768. Profile KV footprint per token and raise to 65536 or 131072
  when we have a workload that wants long context.
- **Throughput profiling.** Capture p50/p99 latency at steady-state
  (post-autotune, after a few warmup requests) before deciding whether
  torch.compile / CUDA graphs are worth enforcing or explicitly
  disabling.

## Outcome

Qwen3.6-35B-A3B serving TP=2 across nvidia1+nvidia2, reachable at
`http://nvidia1:8000/v1/*`, weight-wise identical to what the hf-prefetch
daemon synced. Inventory persisted the change for the next full
provision run.
