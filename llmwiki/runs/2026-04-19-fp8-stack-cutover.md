---
title: 2026-04-19 — Qwen3.5-35B-A3B-FP8 + FlashInfer + dual-HCA cutover
kind: run
date: 2026-04-19
status: shipped
tags: [vllm, fp8, flashinfer, fastsafetensors, dual-hca, cutover, gb10, spark, ngc-26.03]
related:
  - runs/2026-04-19-roce-cutover.md
  - runs/2026-04-19-qwen3-throughput-and-256k.md
  - concepts/nccl-on-spark.md
---

# Qwen3.5-35B-A3B-FP8 + FlashInfer + dual-HCA cutover

## TL;DR

Landed the full vLLM optimisation stack on the stacked-Sparks cluster in one
`cutover_roce.yml` run: FP8 weights (Qwen3.5-35B-A3B-FP8) + FP8 KV cache +
FlashInfer attention + prefix caching + fastsafetensors loader + dual-HCA
RoCE. Aggregate KV cache is now **146.74 GiB** (73.23 + 73.51) across the
two GB10 nodes at `gpu_memory_utilization=0.80`. `/v1/chat/completions` is
serving on `nvidia1:8000` with the `qwen3` reasoning parser and `qwen3_coder`
tool-call parser both engaged. Weight load time 21 s (7/7 shards via
fastsafetensors); 54 CUDA graphs captured (35 piecewise + 19 full).

## Config that landed

`inventory/group_vars/sparks.yml`:

- `vllm_default_model: "Qwen/Qwen3.5-35B-A3B-FP8"` (37.5 GiB on disk per node,
  synced by `hf-prefetch` daemon on nvidia1 → nvidia2 over cylon-fabric)
- `vllm_tensor_parallel_size: 2`
- `vllm_load_format: "fastsafetensors"` (pip-installed by the qwen-patch shim —
  see "NGC 26.03 has no fastsafetensors extra" below)
- `gpu_memory_utilization: 0.80` (was 0.90 — see "Why 0.80 and not 0.90")
- `max_model_len: 262144`, `max_num_batched_tokens: 16384`, `max_num_seqs: 128`
- `kv_cache_dtype: fp8`
- `attention_backend: flashinfer`
- `enable_prefix_caching: true`
- `reasoning_parser: qwen3`, `tool_call_parser: qwen3_coder`
- `spark_dual_hca_enabled: true` (both ConnectX-7 cages active, RoCE v2)

## Why 0.80 and not 0.90

GB10 reports 119.61 GiB to CUDA on the UMA (LPDDR5X shared CPU + GPU pool).
vLLM's `request_memory` probe runs VERY early in startup — right after Ray
worker CUDA context init, before torch/allocator steady state. Measurements:

| Scenario                                           | Free at probe | 0.90 needs | Result |
|----------------------------------------------------|:-------------:|:----------:|:------:|
| Fresh reboot, single-HCA                           |   ~111.5 GiB  | 107.65 GiB | OK     |
| Fresh reboot, dual-HCA                             |   ~109.0 GiB  | 107.65 GiB | OOM    |
| After a few cutover cycles (dual-HCA)              |   ~109.5 GiB  | 107.65 GiB | OOM    |
| NGC 26.03 + FP8 + dual-HCA + cache drop (2026-04-19)|  **103.4 GiB** | 107.65 GiB | **OOM** |

NGC 26.03 carries ~6 GiB more baseline than 26.01 did (more shared libs, newer
torch/CUDA). Combined with the ~2.5 GiB dual-HCA MR/QP pin and not-fully-
released ibverbs state from prior containers, the probe-time free ceiling
dropped to ~103 GiB. `0.80 × 119.61 = 95.69 GiB` leaves ~7.7 GiB margin even
at that worst case — headroom for future regressions without churning this
config. With FP8 weights (~35 GiB back vs BF16) the KV cache at 0.80 is still
~12 GiB larger than the reference recipe's 0.70 baseline.

`drop_caches` was run on both Sparks before each cutover attempt — recovers
~4–5 GiB of buff/cache that vLLM's `free` probe (not `available`) wouldn't
otherwise see.

## NGC 26.03 has no fastsafetensors extra

First run with `--load-format fastsafetensors` died with:

```
ImportError: Please install vllm[fastsafetensors] for fastsafetensors support
```

NGC 26.03-py3 ships vllm without the `fastsafetensors` extra (contradicting
the optimistic comment from the pre-cutover config). Fix: bolted
`pip install --no-cache-dir fastsafetensors` into
`roles/vllm_stacked_container/tasks/main.yml` alongside the existing
qwen-patch shim. Pure-Python wheel (0.2.2), deps already satisfied, runs in
<10 s per container. Cold-load for 7-shard FP8 weights: **21 s** across both
ranks (was going to be much slower on the default safetensors loader). The
config stays aspirational; the image-baseline fix lives with the other image
bolt-ons.

## The two Qwen patches that no longer apply

Both patches bolted in from spark-vllm-docker (`fix_crash.diff` and
`fix_slowness.diff`) report "already applied or not applicable" on a fresh
NGC 26.03 container:

```
1 out of 1 hunk FAILED -- saving rejects to file vllm/v1/core/single_type_kv_cache_manager.py.rej
[qwen-patch] fix_crash: already applied or not applicable
Unreversed patch detected!  Skipping patch.
2 out of 2 hunks ignored -- saving rejects to file vllm/model_executor/layers/fused_moe/fused_moe.py.rej
[qwen-patch] fix_slowness: already reverted or not applicable
```

On a first-ever-run container this means the hunk targets don't exist in NGC
26.03's vLLM tree — the patches were written against a different snapshot
(probably spark-vllm-docker's nightly). Action: observe for runtime
symptoms. If decode stalls or crashes on Qwen3 MoE re-surface, rewrite the
patches against the current NGC snapshot. The triton-allocator shim DOES
install fine (copies two files, no patching).

## Weight load + graph capture timings

- Weight load (fastsafetensors, TP=2, 7 shards per rank): **21 s**
- `compile range (1, 16384)` inductor compile: **22 s**
- CUDA graphs captured: **35 piecewise + 19 full = 54 total**
- Total cold start to port 8000 LISTEN: **~4 minutes** (includes Ray cluster
  bring-up + engine init + weights + graphs)

## Verification

```bash
ssh casibbald@nvidia1 'curl -sS http://127.0.0.1:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d "{\"model\":\"Qwen/Qwen3.5-35B-A3B-FP8\",\"messages\":[
        {\"role\":\"user\",\"content\":\"Reply with exactly the word PONG and nothing else.\"}],
       \"max_tokens\":16,\"temperature\":0}"'
```

Returns:

```json
{"id":"chatcmpl-969dd767aa1cae9a",
 "model":"Qwen/Qwen3.5-35B-A3B-FP8",
 "choices":[{"message":{
   "role":"assistant",
   "content":null,
   "reasoning":"Thinking Process:\n\n1.  **Analyze the Request:**\n    *",
   "tool_calls":[]
 },"finish_reason":"length"}],
 "usage":{"prompt_tokens":21,"completion_tokens":16,"total_tokens":37}}
```

- `reasoning` field populated → `reasoning_parser=qwen3` working
- `tool_calls: []` array present → `tool_call_parser=qwen3_coder` loaded
- `finish_reason: length` is expected — `max_tokens=16` is too small to escape
  the thinking phase. Real prompts with `max_tokens>=200` will surface the
  visible answer after `reasoning` completes.

## Files touched

- `inventory/group_vars/sparks.yml` — `gpu_memory_utilization: 0.90 → 0.80`;
  updated comment table; corrected the "NGC 26.03 has fastsafetensors" claim.
- `roles/vllm_stacked_container/tasks/main.yml` — added
  `pip install fastsafetensors` shim after the triton allocator step; updated
  `changed_when` to register the install as a change.

## Open items

- Probe-time free memory is still ~103 GiB after a fresh cutover — compare
  against a post-reboot run to see whether ibverbs state actually accumulates
  over cutover cycles or if that's a constant baseline we should just accept.
- `_triton_alloc_setup.{py,pth}` — retained as the only image bolt-on besides
  the fastsafetensors install. Verify obsolescence on the next NGC bump by
  dropping the shim temporarily and running sustained fused-MoE decode
  (Qwen3-coder-30B, TP=2, batch=16, ≥5 min). If no stalls, remove both files
  and the `cp` step in `roles/vllm_stacked_container/tasks/main.yml`.

## Upstream patch disposition (resolved)

Tracked-down `fix_crash.diff` and `fix_slowness.diff` against the live NGC
26.03 source tree (`vllm==0.17.1+a03ca76a.nv26.3`). Both are obsolete and
were deleted from `roles/vllm_stacked_container/files/qwen_moe_patches/`.

### `fix_crash.diff` — dropped

The patch replaced an `assert block.block_hash is not None` in
`vllm/v1/core/single_type_kv_cache_manager.py::cache_blocks()` with
`if block.is_null: continue`. NGC 26.03 already carries the `is_null` guard
upstream, and — importantly — preserves the assert for the non-null path:

```python
for block in self.req_to_blocks[request.request_id][
    num_cached_blocks_before:num_cached_blocks_after
]:
    if block.is_null:
        continue
    assert block.block_hash is not None
    self.cached_blocks_this_step.add(block.block_hash)
```

Upstream's version is strictly better than ours (we dropped the assert;
they kept it for all non-sentinel blocks).

### `fix_slowness.diff` — dropped

The patch reverted vLLM PR #34279 (merged 2026-02-11), which added
`tl.int64` stride annotations to the fused-MoE Triton kernels to prevent an
int32-overflow IMA on very large tensors. Those annotations caused ~60×
decode regression on GB10 —
[`@eugr` on #34279](https://github.com/vllm-project/vllm/pull/34279):
*"Qwen3-Coder-Next-FP8 dropped from solid 43 t/s to 2 t/s. … Reverting this
PR restored the performance."*

The vLLM maintainers:

1. [#34530](https://github.com/vllm-project/vllm/pull/34530) (2026-02-13) —
   reverted #34279 wholesale.
2. [#34507](https://github.com/vllm-project/vllm/pull/34507) (2026-02-17) —
   narrow fix: cast only `offs_token` to `tl.int64` at the use site, leaving
   stride params in their native type. `@eugr` confirmed on the PR:
   *"this fixes the performance regression on Spark."*

NGC 26.03 carries both: stride params are plain (post-#34530 state), and the
live `fused_moe.py` has the `offs_token.to(tl.int64)` cast at line 432
(from #34507). Verified 2026-04-19 on nvidia1:

```
$ grep -n 'offs_token.*tl.int64\|def fused_moe_kernel' \
    /usr/local/lib/python3.12/dist-packages/vllm/model_executor/layers/fused_moe/fused_moe.py
177:    offs_token_id = pid_m * BLOCK_SIZE_M + tl.arange(0, BLOCK_SIZE_M).to(tl.int64)
179:    offs_token = tl.load(sorted_token_ids_ptr + offs_token_id).to(tl.int64)
315: def fused_moe_kernel(
432:    offs_token = offs_token.to(tl.int64)
```

### Ansible changes

- Deleted `roles/vllm_stacked_container/files/qwen_moe_patches/fix_crash.diff`
  and `fix_slowness.diff`.
- Removed both files from the `loop` in the "Stage Qwen3 MoE patches" task
  (now just the triton allocator shim + chat template).
- Renamed the apply task to "Apply NGC 26.03 image bolt-ons inside Ray
  containers (Triton shim + fastsafetensors)" and stripped the two `patch`
  invocations. `changed_when` simplified to track only the fastsafetensors
  install.
- Rewrote `roles/vllm_stacked_container/files/qwen_moe_patches/README.md` to
  record the upstream-resolution trail (PR #34279 → #34530 → #34507) and
  the live verification so the next NGC image bump has context.

### Post-cleanup verification (2026-04-19)

Re-ran `ansible-playbook playbooks/cutover_roce.yml` after `echo 3 >
/proc/sys/vm/drop_caches` on both Sparks. Clean recap, no `patch: **** failed`
noise, image bolt-on log is now:

```
[qwen-patch] installing triton allocator shim ...
[qwen-patch] installing fastsafetensors ...
[qwen-patch] fastsafetensors: installed
[qwen-patch] done
```

Smoke test:

```
POST /v1/chat/completions { model=Qwen/Qwen3.5-35B-A3B-FP8,
    content="What is 47 times 53? One sentence only.",
    max_tokens=120, temperature=0.1 }
→ reasoning:"Thinking Process:\n\n1. Analyze… 47×50=2350, 47×3=141, 2350+14…"
  usage: prompt=24 completion=120 total=144 | finish_reason=length
```

`qwen3` reasoning parser active, `fastsafetensors` loaded 7 shards,
`gpu_memory_utilization=0.80` passed the memory check, CUDA graph capture
healthy. Nothing regressed by dropping the two patches.
