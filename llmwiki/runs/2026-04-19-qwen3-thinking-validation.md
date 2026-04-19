# 2026-04-19 — Qwen3.6-35B-A3B: thinking validation, field naming, per-request sweet spot

## Goal

Follow-up to [`2026-04-19-qwen3-throughput-and-256k.md`](2026-04-19-qwen3-throughput-and-256k.md).
User feedback: *"lets get to the sweet spot of: tokens per second, context
size, best thinking output, and actually validate we are getting a valid
response from the thinking."*

Three questions to answer:

1. Is the `qwen3` reasoning parser actually extracting `<think>…</think>`?
2. What does a good vs bad reasoning trace look like on this model?
3. What is the operator/consumer-facing sweet spot — one config, or per-request?

## The field-naming gotcha (read this first)

vLLM's `qwen3` reasoning parser on 26.03-py3 / vLLM 0.17.1 populates the
**OpenAI o1-style `message.reasoning` field**, not the DeepSeek-R1-style
`message.reasoning_content` field.

Looking for `reasoning_content` gets you empty strings and makes you think
the parser is broken. It is not. Example response body (trimmed):

```json
"message": {
  "role": "assistant",
  "content": "To calculate **37 × 41**, ...",
  "reasoning": "Here's a thinking process:\n1. Analyze User Input...\n..."
}
```

**Consumer code must read `choices[0].message.reasoning`.** Treat
`reasoning_content` as a Qwen2.x / DeepSeek legacy. If a downstream client
expects `reasoning_content`, map it in an adapter layer.

## How thinking is toggled

Per-request, via the standard `chat_template_kwargs`:

```json
{
  "model": "Qwen/Qwen3.6-35B-A3B",
  "messages": [...],
  "chat_template_kwargs": {"enable_thinking": true}
}
```

`enable_thinking: true` → template injects `<think>\n` into the assistant
turn, model continues with reasoning, then emits `</think>\n\n` and the
final answer. Parser splits on those tokens.

`enable_thinking: false` → template pre-fills `<think>\n\n</think>\n\n` so
the model sees an empty thinking block and jumps straight to the answer.
Response comes back with `message.reasoning = null`.

Raw-completions probe (hand-built prompt, no chat template) showed the
model's default behaviour is an **empty** `<think>\n\n</think>\n\n` block
followed by the answer — the chat template is what forces meaningful
thinking when `enable_thinking: true` is set.

## Qwen's recommended sampling

Two canonical profiles — both were validated in the suite below:

| Mode | `temperature` | `top_p` | `top_k` | `min_p` | `presence_penalty` |
|---|---|---|---|---|---|
| Thinking ON | 0.6 | 0.95 | 20 | 0 | 0 |
| Thinking OFF | 0.7 | 0.8 | 20 | 0 | — |

Using `temperature ≤ 0.2` (as we were with the quicksort smoke) suppresses
the diversity the reasoning pathway needs — output quality was fine for
that simple task but would degrade on harder ones.

## Validation matrix

Four prompts exercising different reasoning demands, each run ON and OFF
with the appropriate sampling:

| Prompt | Mode | Prompt tok | Reasoning tok (est) | Content tok (est) | Total | Wall | tok/s | Finish |
|---|---|---|---|---|---|---|---|---|
| math_multi_step       | ON  |  88 | **5,317** |   265 |  8,192 | 232.2s | 35.3 | **length** (truncated) |
| math_multi_step       | OFF |  90 |     1 |   588 |    796 |  24.7s | 32.2 | stop |
| code_bug_fix          | ON  |  93 | 1,573 |   203 |  1,912 |  56.9s | 33.6 | stop |
| code_bug_fix          | OFF |  95 |     1 |   217 |    263 |   7.7s | 34.0 | stop |
| logic_puzzle_12balls  | ON  |  69 | 3,409 |   921 |  5,949 | 168.1s | 35.4 | stop |
| logic_puzzle_12balls  | OFF |  71 |     1 | 1,536 |  2,048 |  57.7s | 35.5 | **length** (truncated) |
| creative_haiku        | ON  |  24 | 2,114 |    21 |  2,382 |  67.4s | 35.3 | stop |
| creative_haiku        | OFF |  26 |     1 |    23 |     22 |   0.7s | 30.2 | stop |

Full reasoning traces + generated answers captured in
`/tmp/think_suite2.out` on nvidia1 and reproducible via `/tmp/think_suite2.py`.

### Observations

- **Decode rate is flat at 30-35 tok/s** regardless of mode or workload.
  This is the cluster's ceiling for a single stream on this model — the
  164 tok/s peak from the previous run was aggregate-across-16-streams.
- **Thinking adds a 7-96× wall-time multiplier**, depending on whether
  the model decides to think:
  - bug fix: +7.4× (56.9s vs 7.7s) — good trade, bug explanation + fix was
    better in ON mode (examples, verification)
  - math: +9.4× (and hit length cap) — would be higher if it finished
  - haiku: +96× (67.4s vs 0.7s) for a 21-token output — **overthink**,
    88.7% of tokens were reasoning for a creative task that doesn't need it
- **Quality is genuinely high when thinking is warranted**. Sampled from
  the math reasoning trace:
  > *"This might be irrelevant for the rise, but good to note"* on the
  > initial water depth; considered the "no water underneath" edge case
  > and explicitly rejected it as outside the standard displacement
  > assumption; verified $37 \times 4 = 148$ via $(30+7) \times 4$.
- **max_tokens=8192 is not enough for hard math**. The tank-ball problem
  used 5,317 tokens reasoning, leaving 265 for the answer → truncation.
  **Min safe budget for hard reasoning is ~16,000 tokens.**
- **Non-thinking mode also runs out of budget on complex tasks**. The
  12-ball puzzle at 2048 tokens got cut off mid-procedure. So even OFF
  needs task-aware budgets.

### Reasoning quality samples

**math_multi_step** (reasoning head):

> *Here's a thinking process to solve the problem:*
>
> *1. Understand the Goal: The objective is to calculate the rise in water
> level (in millimetres)…*
> *2. Identify the Given Information: … Initial water depth = 0.6 m (This
> might be irrelevant for the rise, but good to note).*
> *…*

**code_bug_fix** (reasoning + final content were both clean):

The reasoning trace walks through the Python median definition, tests the
current code with `[1,2,3]` and `[1,2,3,4]`, determines the branches are
swapped, drafts the fix, and considers an optional empty-list guard.

Final answer is 203 tokens — exactly the right length for "bug + fix".
No padding, no over-explaining.

**12-ball puzzle** (final content):

Complete solution covering all three first-weighing outcomes (balances /
left heavy / right heavy), each with three sub-branches covering all
odd-ball + direction combinations. The kind of answer you'd want in a
textbook.

## The actual sweet spot: per-request, not cluster-wide

No single config serves all four prompts optimally. The right answer is to
leave cluster-wide settings at today's throughput-tuned values and control
thinking per request.

**Cluster settings** (unchanged from
[`2026-04-19-qwen3-throughput-and-256k.md`](2026-04-19-qwen3-throughput-and-256k.md) — keep):

- `--max-model-len 262144`
- `--gpu-memory-utilization 0.92`
- `--max-num-batched-tokens 16384`
- `--max-num-seqs 128`
- `--reasoning-parser qwen3`

**Per-request recipe**:

| Task type | `enable_thinking` | `max_tokens` | Sampling | Rationale |
|---|---|---|---|---|
| Hard math / scientific | `true` | **16384** | Thinking | 5-10k reasoning common; truncation kills UX |
| Logic / planning / architecture | `true` | **8192** | Thinking | 3-5k reasoning typical |
| Code debug / review | `true` | **4096** | Thinking | 1-2k reasoning typical |
| Code generation (clean spec) | `false` | **4096** | Non-thinking | Reasoning rarely adds value, 7× slower |
| Summarisation / extraction | `false` | **2048** | Non-thinking | Direct task, thinking hurts latency |
| Creative / copywriting | `false` | **1024** | Non-thinking | Overthink pathology (88% reasoning on a haiku) |
| Unknown task | `true` | 8192 | Thinking | Safer default than truncation |

**TPS budget planner**: at 35 tok/s single-stream, 16384-token max corresponds
to ~468s worst-case wall time (~8 min). For interactive use, prefer capping
reasoning-heavy requests to 8192 tokens (~235s = ~4 min) and letting the
consumer retry with a higher budget if the response truncates. The
`finish_reason == "length"` signal is the retry trigger.

**Concurrency sizing**: the 30-35 tok/s per-stream number holds through
modest concurrency; wire-level benchmark from the previous run shows
batch=16 peaks at 163 tok/s aggregate (≈10 tok/s per stream), so a
reasoning-enabled multi-tenant workload should cap concurrent thinkers at
~8 if per-stream UX matters, or ~16 if latency can slip.

## Non-thinking failures worth calling out

- **12-ball puzzle OFF** hit the 2048 length cap mid-procedure. A correctly
  reasoned problem on its own, but the answer shape (long step-by-step
  instructions) needs more budget than the OFF default allows.
- **Math OFF** got the right answer but with shallower verification than
  ON. Both arrived at 7 mm; ON was more rigorous in noting the density was
  not needed.

## What we're *not* doing today (noted for later)

- **Speculative decoding**: Qwen3.6 ships with `mtp_num_hidden_layers: 1`
  in its config (native multi-token-prediction head). vLLM 0.17 supports
  MTP draft via `--speculative-config '{"num_speculative_tokens":1,
  "method":"eagle"}'` or similar. Could lift single-stream decode 2-3×,
  which would cut thinking wall-times from 232s to maybe 80s on the math
  problem. Needs a bake-off because MTP interacts with CUDA graphs.
- **FP8 weights**: 26.03-py3 ships FBGEMM-GPU FP8 kernels. Untested on
  Qwen3_5_moe's linear-attention blocks. Worth a smoke; ~2× bandwidth
  gain if it works.
- **Expert parallelism (EP=2)** instead of TP=2 for the 256-expert MoE.
  Each rank currently holds half the expert weights under pure TP; EP
  would shard experts instead, halving weight-load bandwidth per token
  at the cost of all-to-all traffic on the QSFP socket-NCCL fabric.

## Files changed

None. This run is pure validation — no inventory changes needed.
Suite script (`/tmp/think_suite2.py`) and output (`/tmp/think_suite2.out`)
live on nvidia1 for reproduction.

## Related

- [`runs/2026-04-19-qwen3-throughput-and-256k.md`](2026-04-19-qwen3-throughput-and-256k.md) — config that made this test possible.
- [`runs/2026-04-19-qwen3_6-35b-a3b-promoted.md`](2026-04-19-qwen3_6-35b-a3b-promoted.md) — model went live.
