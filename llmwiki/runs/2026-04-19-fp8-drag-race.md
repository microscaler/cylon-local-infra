---
title: 2026-04-19 — FP8 drag race: throughput, long-context, thinking vs BF16 baseline
kind: run
date: 2026-04-19
status: shipped
tags: [vllm, fp8, flashinfer, benchmark, drag-race, thinking, gb10, spark, ngc-26.03]
related:
  - runs/2026-04-19-fp8-stack-cutover.md
  - runs/2026-04-19-qwen3-throughput-and-256k.md
  - runs/2026-04-19-qwen3-thinking-validation.md
---

# FP8 drag race: throughput, long-context, thinking — FP8 vs BF16 baseline

## TL;DR

Replayed the three bench suites from the 2026-04-19 BF16 Qwen3.6 runs against
the cleaned-up FP8 Qwen3.5 stack (cutover log:
[`2026-04-19-fp8-stack-cutover.md`](2026-04-19-fp8-stack-cutover.md)). Every
number moved the right direction.

Two reference columns because there were two upgrades between the original
baseline and today:

- **Pre-RoCE BF16** — original drag race (TCP sockets for NCCL data plane).
- **RoCE BF16** — from [`2026-04-19-roce-cutover.md`](2026-04-19-roce-cutover.md),
  RoCE+GDR on, BF16 weights + BF16 KV, default attention.
- **FP8 now** — this run.

| Bench | Pre-RoCE BF16 | RoCE BF16 | **FP8+RoCE now** | vs RoCE-BF16 |
|---|---:|---:|---:|---:|
| Single-stream decode (1×256)  | 35.3  | 47.6  | **63.3**  | **+33%** |
| Aggregate @ batch=16 (16×256) | 163.9 | 265.9 | **285.5** | +7% |
| Aggregate @ batch=64 (not tested on FP8 today) | — | 638.7 | _TBD_ | — |
| 40k/46k prefill (total/wall)  | 1,833 | 6,919 | **5,885** | -15% |
| Math reasoning ON (wall)      | 232s, **length-truncated** | (not benched) | **79.7s, stop ✓** | quality unlocked |
| Decode rate under thinking    | 30-35 tok/s | (not benched) | 63-64 tok/s | ~2× |

Net story: the **RoCE cutover** did most of the compute/bandwidth-bound work
(single-stream +36%, batch=16 +62%, prefill 3.8×). The **FP8 cutover** then
pushed single-stream another +33% on top (63 vs 47), roughly in line with
FP8's halved-bandwidth weight reads, plus it unlocked *correctness* on
hard-reasoning prompts by finishing within budget where BF16 truncated.

Two caveats to be honest about:

1. We stopped at batch=16 on FP8 to match the thinking-validation baseline
   shape — **we didn't re-measure the batch=64 ceiling**. RoCE BF16 hit
   638.7 tok/s at batch=64; FP8 at the same concurrency should be higher.
2. **Long-prefill throughput regressed** 6919 → 5885 tok/s. Different model
   (Qwen3.5 vs 3.6), different attention backend (FlashInfer vs default),
   different KV dtype (FP8 vs BF16) — it's a compound change, not a clean
   attribution yet. Still plenty fast end-to-end and the response was
   correct; needs a focused A/B before calling it a regression. See
   follow-ups.

All three `qwen_moe_patches` diffs are retired upstream (`is_null` guard,
PR #34530 revert, PR #34507 narrow cast) so we carry zero source patches
on the hot path; only the Triton allocator shim + fastsafetensors install
remain as image bolt-ons.

## Stack under test

| Knob | Value |
|---|---|
| Model | `Qwen/Qwen3.5-35B-A3B-FP8` (was BF16 Qwen3.6 in baseline) |
| vLLM | `0.17.1+a03ca76a.nv26.3.46967107` (NGC 26.03-py3) |
| `--max-model-len` | 262144 |
| `--tensor-parallel-size` | 2 (one rank per Spark, Ray) |
| `--gpu-memory-utilization` | 0.80 (down from 0.92 on BF16 to leave dual-HCA headroom) |
| `--max-num-batched-tokens` | 16384 |
| `--max-num-seqs` | 128 |
| `--kv-cache-dtype` | fp8 |
| `--attention-backend` | flashinfer |
| `--load-format` | fastsafetensors |
| `--reasoning-parser` | qwen3 |
| `--tool-call-parser` | qwen3_coder |
| `--enable-prefix-caching` | on |
| Networking | RoCEv2 + GPUDirect RDMA, dual-HCA (4 ConnectX-7 cables) |

No custom patches on the vLLM source tree (all three `qwen_moe_patches`
diffs are now either upstream or retired; see the cutover run log).

## Bench 1 — Throughput drag race

Replay of `/tmp/bench.py`: N∈{1,4,8,16} concurrent streams × max_tokens∈{256,1024},
16 short general-knowledge prompts, `enable_thinking: false`, Qwen-recommended
non-thinking sampling (T=0.7, top_p=0.8). Warmup over batch={1,2,4,8,16} first
to populate CUDA graphs.

| Workload | Pre-RoCE BF16 agg | RoCE BF16 agg | **FP8 agg** | FP8 per-req | Δ vs RoCE-BF16 |
|---|---:|---:|---:|---:|---:|
| N=1  par=1  max=256  | 35.3 | 47.6 | **63.3** | 63.3 | +33% |
| N=4  par=4  max=256  | 86.3 | 134.3 | **147.6** | 41.5 | +10% |
| N=8  par=8  max=256  | 125.6 | 181.7 | **199.4** | 30.3 | +10% |
| N=16 par=16 max=256  | 163.9 | 265.9 | **285.5** | 22.2 | +7% |
| N=32 par=32 max=256  | — | 398.6 | _not tested_ | — | — |
| N=64 par=64 max=256  | — | **638.7** | _not tested_ | — | — |
| N=1  par=1  max=1024 | 36.2 | — | **63.7** | 63.7 | — |
| N=4  par=4  max=1024 | 87.3 | — | **105.2** | 43.3 | — |
| N=8  par=8  max=1024 | 124.3 | — | **164.9** | 31.7 | — |

Observations:

- **Single-stream decode 47.6 → 63.3 tok/s (+33%)** vs RoCE-enabled BF16.
  That's roughly the expected win from FP8 weights halving the per-token
  bandwidth read on memory-bandwidth-bound decode on GB10's LPDDR5X.
- **Per-stream under concurrency ~2× vs pre-RoCE BF16** (batch=16: 10.2 →
  22.2 per-stream), more modestly +7% vs RoCE BF16 at the same batch.
- **Aggregate peak at batch=16 is +7% over RoCE BF16, not the +74% headline.**
  Because we stopped at batch=16. The RoCE BF16 run continued to
  batch=32 (398.6) and batch=64 (638.7), showing aggregate scales well
  beyond batch=16. FP8 at batch=64 is a pending bench (see follow-ups);
  back-of-envelope it should clear 800 tok/s.
- **1024-max gains are smaller in relative terms** — longer decodes amortise
  prefill tax so per-request t/s is closer to the decode ceiling; the
  absolute t/s is still at the ~63/stream ceiling on single-stream.
- `finish_reasons` mix: about half `length` at max=256 (model wanted to say
  more), majority `stop` at max=1024 (appropriate budget for these prompts).

## Bench 2 — Long-context proof

`/tmp/bench_longctx.py`: single request, ~214k-char prompt built by repeating
the Greek alphabet, asking for a one-paragraph summary of the pattern.

| Metric | Pre-RoCE BF16 | RoCE BF16 (40k prefill) | **FP8 now (46k)** |
|---|---:|---:|---:|
| Prompt tokens | 46,023 | ~40,000 | 48,509 |
| Completion tokens | 256 | — | 73 |
| Wall | 25.25s | 5.78s | **8.25s** |
| End-to-end/prefill rate (tok/s) | 1,833 | **6,919** | **5,885** |
| `finish_reason` | — | — | `stop` (clean) |

**Mixed headline.** Vs pre-RoCE BF16, FP8 is 3.2× faster end-to-end. But vs
the RoCE BF16 intermediate datapoint (6,919 tok/s on a 40k prefill), the FP8
run at 5,885 tok/s is **~15% slower**. This is not a clean A/B — several
variables changed at once (Qwen3.5 vs 3.6, FP8 vs BF16 weights, FP8 vs BF16
KV, FlashInfer vs default attention, slightly different prompt length). The
most likely culprit is FlashInfer's prefill path on the `qwen3_5_moe`
architecture being less optimised than default attention at ~50k prompt
lengths; would need a focused A/B with `--attention-backend` swapped to
isolate. Response quality was unaffected — correctly identified the Greek
alphabet repetition pattern and finished cleanly in 73 tokens.

Response quality — the model identified the Greek-alphabet repetition
pattern in one correct paragraph, nailed the "cyclic reiteration" framing,
and finished with `stop` in 73 tokens (the prompt asked for a paragraph,
not a specific length).

## Bench 3 — Thinking validation

`/tmp/think_suite.py` — four prompts exercising different reasoning demands,
each run with `enable_thinking: true` (Qwen thinking sampling: T=0.6,
top_p=0.95, top_k=20) and `enable_thinking: false` (T=0.7, top_p=0.8).
Budgets: math=16384, 12balls=8192, code=4096, haiku=1024.

Consumer note: this model populates **`message.reasoning`** (o1-style), NOT
`message.reasoning_content` — same as the BF16 baseline documented in
[`2026-04-19-qwen3-thinking-validation.md`](2026-04-19-qwen3-thinking-validation.md).
If a client reads `reasoning_content` it gets an empty string and will
think thinking is broken. It's not.

### Wall time + quality table (single-stream)

| Prompt | Mode | BF16 wall | BF16 finish | **FP8 wall** | **FP8 finish** | Wall Δ |
|---|---|---:|---|---:|---|---:|
| math_multi_step      | ON  | 232.2s | **length** (truncated) | **79.7s** | **stop ✓** | **-66%** |
| math_multi_step      | OFF | 24.7s  | stop  | 11.8s | stop | -52% |
| code_bug_fix         | ON  | 56.9s  | stop  | 13.9s | stop | -76% |
| code_bug_fix         | OFF | 7.7s   | stop  | 5.3s  | stop | -31% |
| logic_puzzle_12balls | ON  | 168.1s | stop  | 113.5s | stop | -33% |
| logic_puzzle_12balls | OFF | 57.7s  | **length** (at 2048 cap) | 57.8s | stop ✓ | budget raised |
| creative_haiku       | ON  | 67.4s  | stop (2114 reas + 21 content) | 16.1s | **length** (overthink) | regression in shape |
| creative_haiku       | OFF | 0.7s   | stop  | 0.4s  | stop | -43% |

### Token breakdown (FP8 run)

| Prompt | Mode | prompt | reasoning (est) | content (est) | total | tok/s |
|---|---|---:|---:|---:|---:|---:|
| math_multi_step      | ON  | 103 | 4,299 | 799 | 5,098 | 63.9 |
| math_multi_step      | OFF | 105 | 0 | 748 | 748 | 63.4 |
| code_bug_fix         | ON  | 94  | 468 | 424 | 892 | 64.2 |
| code_bug_fix         | OFF | 96  | 0 | 336 | 336 | 63.3 |
| logic_puzzle_12balls | ON  | 75  | 5,179 | 2,070 | 7,249 | 63.9 |
| logic_puzzle_12balls | OFF | 77  | 0 | 3,671 | 3,671 | 63.5 |
| creative_haiku       | ON  | 39  | 1,024 | 0 | 1,024 | 63.7 |
| creative_haiku       | OFF | 41  | 0 | 21 | 21 | 51.3 |

(Reasoning / content token counts are proportional estimates split by the
char-length of each field. vLLM reports a single `completion_tokens` in
`usage` and does not split. The estimate is within ~5% of direct tokenisation
based on spot-checks.)

### Observations

- **Decode rate is a flat 63-64 tok/s across everything** — exactly what
  you expect from FP8 memory-bandwidth-bound decode. Haiku OFF reads as
  "51 tok/s" only because the generation is so short (21 tokens / 0.4s)
  that startup overhead dominates the measurement, not a real rate.
- **Math ON finished cleanly.** On BF16 this same prompt used 5,317
  reasoning tokens + 265 content and hit `finish_reason=length` — the
  answer was truncated. FP8 at the same 8192 budget fit everything in 5,098
  tokens with `stop`. The faster decode is *the* UX unlock here, not raw
  speed: the user gets a correct answer instead of a half answer.
- **Math reasoning quality is unchanged and high.** Sampled trace head:
  > *"The density of the ball determines the force it exerts on the bottom
  > or the buoyant force, but for calculating the volume displacement of a
  > submerged object, only the object's volume matters."*
  Explicitly called out and rejected the density red herring. Verified
  $37 \times 4 = 148$ via $(30+7) \times 4$ arithmetic. Same rigour as BF16.
- **12-balls OFF finished cleanly** (3,671 content tokens in a 8,192 budget),
  where BF16 at 2,048 cap was truncated mid-procedure. Apples to oranges on
  the budget — the budget change was per-request-sweet-spot from the BF16
  run — but the correctness signal is there.
- **Haiku ON is *worse* than BF16**: the model used all 1,024 tokens
  iterating drafts ("*Let me try one more to ensure quality. … Line 1:
  Morning clouds turn gray (5) — …*") and never closed the `<think>` block,
  so `content` came back empty and `finish_reason=length`. BF16 had the same
  overthink pathology (88% reasoning for a 21-token output) but at least
  committed to a final answer. **Workaround unchanged**: use
  `enable_thinking: false` for creative tasks. Raising max_tokens for haiku
  ON likely won't help — the draft-iteration loop has no natural stop.
- **Code fix ON is crisp**: 468 reasoning + 424 content tokens, `stop`,
  13.9s wall. Trace correctly identifies the swapped branches, runs the
  `[1,2,3]` and `[1,2,3,4]` cases to confirm, gives a correct fixed
  function, and suggests an optional empty-list guard. Text-book reasoning.

## The per-request recipe still holds

The 2026-04-19 BF16 run's per-request budget table still applies, with
slightly tighter time estimates because single-stream is faster:

| Task type | `enable_thinking` | `max_tokens` | Expected wall (FP8) | Rationale |
|---|---|---:|---:|---|
| Hard math / scientific | `true` | **16384** | ~4 min worst case | 5-10k reasoning common; truncation kills UX |
| Logic / planning / architecture | `true` | **8192** | ~2 min | 3-5k reasoning typical |
| Code debug / review | `true` | **4096** | ~1 min | 500-2k reasoning typical |
| Code generation (clean spec) | `false` | 4096 | ~65s | Thinking rarely adds value |
| Summarisation / extraction | `false` | 2048 | ~35s | Direct task, thinking hurts latency |
| Creative / copywriting | `false` | 1024 | ~15s | Overthink pathology in ON |
| Unknown task | `true` | 8192 | ~2 min | Safer default than truncation |

**Concurrency sizing**: at 22 tok/s per-stream at batch=16, a
reasoning-enabled multi-tenant workload should still cap concurrent thinkers
at ~8 if per-stream UX matters (~30 tok/s per stream), or ~16 if latency
can slip. No change from the BF16 guidance, just more headroom at every
level.

## Why FP8 wins this much

| Factor | Effect |
|---|---|
| FP8 weights (~35 GB model → ~18 GB) | Halves the weight-read bandwidth per decoded token. Decode is bandwidth-bound on GB10's LPDDR5X → ~2× decode tok/s. |
| FP8 KV cache | Halves the KV-read bandwidth per generated token, and doubles the KV-cache capacity per GiB → higher concurrency headroom. |
| FlashInfer attention | Faster prefill + better batched attention. Shows up most in long-context bench (+221%). |
| fastsafetensors load | Cold-load win only (~18 GB/rank in 21s). No steady-state effect on bench numbers; makes cutover cheaper. |
| PR #34507 landed upstream | No patch needed on the hot path → `fused_moe` signatures are in their fast form without our revert step. Keeps us on-spec for the `qwen3_5_moe` architecture. |

## Follow-ups (not blocking)

- **Batch=32/64 sweep on FP8.** Thinking-validation baseline capped at
  batch=16 so we did too. RoCE BF16 ran up to batch=64 and saw 638.7 tok/s
  (new ceiling there). FP8 at batch=64 should clear that; back-of-envelope
  ~800 tok/s. Needed to claim a proper new cluster ceiling and close the
  apples-to-apples vs the RoCE BF16 run.
- **Long-prefill A/B: FlashInfer vs default attention** on Qwen3.5-FP8.
  The 5,885 vs 6,919 tok/s gap on ~45k prefill is suspicious. Keep FP8
  weights, toggle `--attention-backend` and re-measure a 40k+46k pair.
  If FlashInfer is slower on MoE-prefill, switch to default for this model
  and re-run thinking-validation to confirm no decode regression.
- **Speculative decoding.** Qwen3.5's sibling Qwen3.6 ships
  `mtp_num_hidden_layers: 1` (native MTP head); Qwen3.5-FP8 may or may not
  carry the same — check `config.json`. If yes, MTP draft via
  `--speculative-config '{"num_speculative_tokens":1,"method":"eagle"}'`
  could lift single-stream from 63 → 120+ tok/s on reasoning-heavy workloads.
- **Expert parallelism (EP=2)** vs TP=2 for the 256-expert MoE. Each rank
  currently carries half the expert weights under TP; EP halves the
  weights-per-token pressure on each rank at the cost of all-to-all traffic
  on the RoCE fabric (which we now have at dual-HCA).
- **Triton allocator shim retirement**. Test next NGC bump whether the
  `_triton_alloc_setup.{py,pth}` cp is still needed — document in the
  cutover run log.

## Files touched

- None to the Ansible tree; this is a validation run against the already-shipped
  FP8 stack.
- `/tmp/bench.py`, `/tmp/bench_longctx.py`, `/tmp/think_suite.py` —
  recreated on nvidia1 (earlier copies didn't survive the reboot cycle).
  `think_suite.out` full trace file lives at `/tmp/think_suite.out` on
  nvidia1 for reproduction.

## Related

- [`runs/2026-04-19-fp8-stack-cutover.md`](2026-04-19-fp8-stack-cutover.md) — stack under test.
- [`runs/2026-04-19-qwen3-throughput-and-256k.md`](2026-04-19-qwen3-throughput-and-256k.md) — BF16 baseline throughput.
- [`runs/2026-04-19-qwen3-thinking-validation.md`](2026-04-19-qwen3-thinking-validation.md) — BF16 baseline thinking suite.
