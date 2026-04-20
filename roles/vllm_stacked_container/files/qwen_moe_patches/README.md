# NGC vLLM image bolt-ons for GB10 (Spark stacked-TP=2)

Files in this directory are staged onto each Spark at provision time and then
applied inside the NGC Ray containers by the "Apply NGC 26.03 image bolt-ons"
task in `roles/vllm_stacked_container/tasks/main.yml`.

## Current contents

| File | What it does | Why it's still here |
|------|--------------|---------------------|
| `_triton_alloc_setup.py` + `.pth` | Replaces Triton's `NullAllocator` with `torch.cuda.caching_allocator_alloc` on interpreter startup (via site.py `.pth` machinery). | Without this, Triton fused-MoE kernels periodically stall on GB10's unified-memory allocator. `cp -f` is idempotent. Remove when Triton's default allocator works on GB10 (likely coincident with a CUDA/driver bump). |
| `unsloth.jinja` | Chat template for Qwen3 tool-calling (Unsloth's fixed-up version). | Referenced by `--chat-template /vllm-patches/unsloth.jinja` in `sparks.yml`. |

## Historical: two diffs that USED to live here

Both were vendored from `spark-vllm-docker/mods/fix-qwen3-coder-next/`. Both
are now obsolete; both were removed 2026-04-19 after upstream resolution was
verified against NGC 26.03's bundled vLLM (`0.17.1+a03ca76a.nv26.3`).

### `fix_crash.diff` — REMOVED

Targeted `vllm/v1/core/single_type_kv_cache_manager.py::cache_blocks()`.
Original symptom: `assert block.block_hash is not None` fired on Qwen3.5-MoE
during mixed prefill/decode CUDA-graph capture when the block was a "null
block" sentinel.

**Upstream resolution**: NGC 26.03 now ships the is_null guard in place,
ahead of the assert:

```python
for block in self.req_to_blocks[request.request_id][
    num_cached_blocks_before:num_cached_blocks_after
]:
    if block.is_null:
        continue
    assert block.block_hash is not None
    self.cached_blocks_this_step.add(block.block_hash)
```

This is cleaner than our patch (which dropped the assert entirely) because
the invariant check is preserved for all non-null blocks.

### `fix_slowness.diff` — REMOVED

Targeted `vllm/model_executor/layers/fused_moe/fused_moe.py`. Applied with
`patch -R` to revert vLLM [PR #34279](https://github.com/vllm-project/vllm/pull/34279).

Original symptom: PR #34279 (merged 2026-02-11) annotated every stride
parameter in the two Triton fused-MoE kernels as `tl.int64` to fix an
int32-overflow crash on very large tensors (m ≥ 100k). This caused ~60× decode
regression on DGX Spark (GB10) due to register pressure —
[`@eugr` confirmed Qwen3-Coder-Next-FP8 dropped from 43 t/s → 2 t/s](https://github.com/vllm-project/vllm/pull/34279#issuecomment-...).

**Upstream resolution**: the `tl.int64` annotations were reverted
([PR #34530](https://github.com/vllm-project/vllm/pull/34530), merged
2026-02-13), and a narrower fix that casts only `offs_token` to int64 at the
use site landed as [PR #34507](https://github.com/vllm-project/vllm/pull/34507)
on 2026-02-17. `@eugr` confirmed on that PR: *"this fixes the performance
regression on Spark"*.

NGC 26.03 carries both:
- No `tl.int64` annotations on stride params (post-#34530 state).
- `offs_token = offs_token.to(tl.int64)` cast at line 432 of the current
  `fused_moe_kernel` body (from #34507).

So int32 overflow is handled AND the GB10 perf regression is absent, without
any patch on our side. Confirmed live in NGC 26.03 on nvidia1 (2026-04-19).

## When to drop what's left

`_triton_alloc_setup*`: when Triton's default allocator works on GB10 without
stalls. Verify by dropping the shim temporarily and running a sustained
fused-MoE decode workload (Qwen3-coder-30B, TP=2, batch=16, ≥5 min). If no
stalls, remove both files and the cp step.

Revisit on every NGC image bump (`vllm_stacked_container_image` change) —
also re-check the "Historical" section above in case any upstream fix gets
reverted.
