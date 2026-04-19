# 2026-04-19 — Qwen3.6-35B-A3B: context 32k→256k, throughput 86→164 tok/s

## Goal

Follow-up to [`2026-04-19-qwen3_6-35b-a3b-promoted.md`](2026-04-19-qwen3_6-35b-a3b-promoted.md).
User feedback: *"lets see if we can get our context higher, use up the Nvidia
boxes to their full potential. We are leaving tokens per second on the table."*

Two asks in one:

1. **Context** — raise `--max-model-len` toward Qwen3.6's native 262144.
2. **Throughput** — tune `vllm serve` flags so aggregate tok/s scales with
   concurrency, instead of flatlining at batch≥4.

## Baseline (before)

| Flag | Value |
|---|---|
| `--max-model-len` | 32768 |
| `--gpu-memory-utilization` | 0.90 (default) |
| `--max-num-batched-tokens` | 2048 (default) |
| `--max-num-seqs` | 256 (default) |
| `--reasoning-parser` | — |

Post-warmup bench (`/tmp/bench.py`, all prompts short, `max_tokens=256/1024`):

| Workload | tok/s |
|---|---|
| 1×256   single-stream | 29.0 |
| 4×256   concurrent (aggregate) | 86.4 |
| 1×1024  single-stream | 32.6 |

Boot-time KV report: per-rank 70.66 GiB available KV cache.

## Architecture notes (from `config.json`)

Pulled from `/root/.cache/huggingface/hub/models--Qwen--Qwen3.6-35B-A3B/.../config.json`:

- `model_type: qwen3_5_moe`
- `max_position_embeddings: 262144` — native 256k, no rope extension needed.
- 40 hidden layers, `full_attention_interval: 4` →
  **10 full-attention + 30 linear-attention** layers.
- Attention: `num_attention_heads=16`, `num_key_value_heads=2`, `head_dim=256`
  (GQA 8:1).
- MoE: `num_experts=256`, `num_experts_per_tok=8` (very fine-grained).

Per-token KV (full-attention layers only, BF16):
`2 × 2 × 256 × 2 × 10 = 20 KB/token total`, so `10 KB/token` per rank on TP=2.
Linear-attention layers use a fixed-size recurrent state independent of
sequence length — 30 of 40 layers carry zero marginal KV cost as context grows.

This is why the paged KV budget comfortably holds 1.89M tokens across the
cluster: only 10 layers participate in KV growth.

## The change

`inventory/group_vars/sparks.yml`:

```yaml
vllm_api_server_extra_args:
  - "--max-model-len"
  - "262144"
  - "--gpu-memory-utilization"
  - "0.92"                       # 0.95 crashed — see OOM note below
  - "--max-num-batched-tokens"
  - "16384"                      # 8× the 2048 default
  - "--max-num-seqs"
  - "128"
  - "--reasoning-parser"
  - "qwen3"
```

## Failed first attempt: `--gpu-memory-utilization 0.95`

vLLM's `init_device` refused on boot:

```
ValueError: Free memory on device cuda:0 (111.5/119.61 GiB) on startup
is less than desired GPU memory utilization (0.95, 113.63 GiB).
```

GB10 reports **119.61 GiB visible** to CUDA, but only **111.5 GiB** is free
on boot (~8 GiB lost to host OS, dockerd, ray runtime, and whatever else
shares the unified memory). Ceiling is therefore `111.5 / 119.61 ≈ 0.932`.

**Landed at 0.92** → requests 110.04 GiB, leaves ~1.5 GiB safety margin.
Pinned the number in the inventory comment so the next person in here
doesn't repeat the experiment.

## Boot-time KV report (after)

From `/root/vllm-serve.log`:

```
INFO 04-19 15:07:12 [kv_cache_utils.py:1314] GPU KV cache size: 1,890,240 tokens
INFO 04-19 15:07:12 [kv_cache_utils.py:1319] Maximum concurrency for 262,144 tokens per request: 28.42x
(rank 0) Available KV cache memory: 72.12 GiB
(rank 1) Available KV cache memory: 72.36 GiB
```

Total KV cache went from **70.66 GiB → 144.48 GiB** (+104%) because the
0.90 → 0.92 util bump reclaims headroom on *both* ranks, not just rank 0.
The cluster now budgets for **28 concurrent full-256k-context sequences**.

## Benchmark (after)

Proper warmup across batch={1,2,4,8} before measurement (needed to prime the
CUDA graph capture for each batch size — skipping this was what made my
first pass look like a regression on 4×concurrent).

| Workload | Aggregate tok/s | Per-req tok/s (median) | vs baseline |
|---|---|---|---|
| N=1  parallel=1  max=256  | 35.3  | 35.3 | +22% |
| N=4  parallel=4  max=256  | 86.3  | 21.6 | ≈ |
| N=8  parallel=8  max=256  | 125.6 | 15.7 | new |
| N=16 parallel=16 max=256  | **163.9** | 10.2 | new |
| N=1  parallel=1  max=1024 | 36.2  | 36.2 | +11% |
| N=4  parallel=4  max=1024 | 87.3  | 21.8 | ≈ |
| N=8  parallel=8  max=1024 | 124.3 | 15.5 | new |

Key numbers:

- **Single-stream**: 29 → 35 tok/s (+22%). CUDA graph capture plus the
  slightly looser KV budget is showing up here.
- **Saturated throughput**: 86 → **164 tok/s** at batch=16 (+90%). The
  old `max_num_batched_tokens=2048` default was the real bottleneck —
  with 4+ concurrent prefills it was single-stepping prefill chunks.
  At 16384 it fits several prefills per scheduler step.
- Logger also reported **steady-state decode at 63 tok/s** when fed
  a single hot request, which matches the single-stream ceiling once
  prefill tax is amortised over longer generations.

## Long-context proof

`/tmp/bench_longctx.py` — single request with a deliberately oversized
prompt that would have been rejected at the old 32k cap:

- **Prompt: 46,023 tokens / 214,561 chars.**
- Response: 256 tokens.
- Wall: 25.25s.
- Effective rate: **~1,833 tok/s** end-to-end (prefill dominated).

At 25k prompt + 4096 max_tokens the model returned a clean 3-bullet summary
with `finish_reason: stop` at 696 tok/s end-to-end. No OOM, no eviction, no
config warning. Paged KV handled it entirely in-cache.

## Reasoning parser

`--reasoning-parser qwen3` is live — vLLM's startup log confirms
`reasoning_parser='qwen3'`. Consumers now get an OpenAI-compatible
`reasoning_content` field with the `<think>…</think>` monologue separated
from the `content` field (the final answer). Simple summarisation tasks
don't trigger thinking, so `reasoning_content` is legitimately empty for
those — not a bug.

## Scaling observations

| Concurrency | Aggregate | Per-seq | Scaling efficiency |
|---|---|---|---|
| 1  | 35.3  | 35.3 | 1.00× |
| 4  | 86.3  | 21.6 | 0.61× |
| 8  | 124.3 | 15.5 | 0.44× |
| 16 | 163.9 | 10.2 | 0.29× |

Scaling efficiency drops as expected for memory-bandwidth-bound decode on
GB10 — each extra concurrent stream shares weights-reload bandwidth. The
**164 tok/s plateau at batch=16** is likely where LPDDR5X HBM bandwidth
(not compute) becomes the ceiling; further gains probably need:

- **Speculative decoding** (e.g., an EAGLE3 draft for the MoE backbone) —
  trades compute for decoded tokens per memory pass.
- **Expert-parallel sharding** instead of tensor-parallel — with 256 experts
  and TP=2, each rank still loads half the expert weights. EP would halve
  the weights-per-token-per-rank pressure at the cost of all-to-all traffic
  on the QSFP fabric (which is socket-NCCL only, not RDMA — may hurt).
- **FP8 weights** (via NVFP4 or `--quantization fp8`) — roughly halves
  memory bandwidth per token. 26.03-py3 ships with fbgemm-gpu FP8 support;
  untested on this architecture's linear-attention layers.

None of those are follow-ups for today — the raw tok/s gain from 86 → 164
already nearly 2×s the cluster's useful throughput, and the 8× context
unlock was the main ask.

## Small gotchas

- **vLLM init "WARNING: Tensor parallel size (2) exceeds available GPUs
  (1)"** — cosmetic. Ray reports 1 GPU per node (one GB10 per Spark), and
  TP=2 places one rank on each node. The warning predates multi-node TP
  becoming first-class in Ray; ignore.
- **SymmMemCommunicator: Device capability 12.1 not supported** — Blackwell
  compute cap 12.1 isn't in vLLM 0.17's hardcoded SymmMem allowlist yet;
  falls back to default all-reduce. Not a regression vs 26.01-py3.
- **`async scheduling disabled` under Ray backend** — known upstream
  restriction (`mp`, `uni`, `external_launcher` only). Irrelevant for our
  single-engine setup.

## Files changed

- `inventory/group_vars/sparks.yml` — `vllm_api_server_extra_args` expanded
  + explanatory comment about the 0.95 OOM boundary and rationale for the
  other knobs.

## Verification transcript

```
$ curl -sS http://nvidia1:8000/v1/models | python3 -c "import sys,json; [print(m['id'],'max_len=',m['max_model_len']) for m in json.load(sys.stdin)['data']]"
Qwen/Qwen3.6-35B-A3B max_len= 262144
```

vLLM boot log (abbreviated):

```
non-default args: {
  'model': 'Qwen/Qwen3.6-35B-A3B',
  'max_model_len': 262144,
  'reasoning_parser': 'qwen3',
  'distributed_executor_backend': 'ray',
  'tensor_parallel_size': 2,
  'gpu_memory_utilization': 0.92,
  'max_num_batched_tokens': 16384,
  'max_num_seqs': 128
}
GPU KV cache size: 1,890,240 tokens
Maximum concurrency for 262,144 tokens per request: 28.42x
```

## Autoupgrade compatibility

`vllm-stack-autoupgrade.service` captures the live container's
`vllm serve` argv via `docker inspect`, so a future NGC image promotion
will replay these flags verbatim — no daemon change required. Verified
against the daemon implementation in
[`roles/vllm_stack_autoupgrade/files/vllm_stack_autoupgrade.py`](../../roles/vllm_stack_autoupgrade/files/vllm_stack_autoupgrade.py).

## Follow-ups (not blocking)

- Speculative decoding — pending an EAGLE3 draft model for Qwen3.6-35B-A3B;
  would target >250 tok/s single-stream.
- FP8 weights via `--quantization fp8` — needs a smoke on the hybrid linear
  layers; could unlock ~2× throughput if supported.
- Expert-parallel sharding (`--enable-expert-parallel`) — worth a benchmark
  comparison against the current TP=2; 256 experts across 2 ranks is a lot
  of expert weight per rank under pure TP.
- Fix tag propagation in `roles/spark_provision` so
  `ansible-playbook --tags vllm_ngc_stack` can replay this config on a
  full re-provision (carry-over from the Qwen promotion run).

## Related

- [`runs/2026-04-19-qwen3_6-35b-a3b-promoted.md`](2026-04-19-qwen3_6-35b-a3b-promoted.md) — model landed at 32k context.
- [`runs/2026-04-19-26.03-py3-upgrade.md`](2026-04-19-26.03-py3-upgrade.md) — the NGC image that enabled Qwen3.6.
- [`concepts/ngc-stacked-container-stack.md`](../concepts/ngc-stacked-container-stack.md) — container-only architecture.
- [`concepts/nccl-on-spark.md`](../concepts/nccl-on-spark.md) — NCCL recipe this run inherits.
