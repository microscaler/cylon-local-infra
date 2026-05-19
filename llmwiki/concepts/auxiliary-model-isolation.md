---
title: Auxiliary-model isolation — keep small / frequent calls off the big-model endpoint
kind: concept
status: proposed
tags: [hermes, vllm, architecture, concurrency, capacity-planning, 8-spark-plan]
updated: 2026-05-01
first_observed: 2026-05-01
related:
  - concepts/8-spark-fabric-and-orchestrator.md
  - concepts/ngc-stacked-container-stack.md
  - concepts/pii-ai-data-plane.md
  - runs/2026-05-01-nvidia2-abrupt-power-off-vllm-long-context.md
  - runs/2026-04-29-cluster-recovery-and-26.04-rollback.md
---

# Auxiliary-model isolation

## The principle

A serving cluster sized for **one heavy primary task** (long-context
reasoning + agentic coding on a 35B-FP8 / 235B-A22B-FP8 / 480B-AWQ MoE)
should not also serve the **dozen small / frequent / latency-tolerant
auxiliary calls** the agent harness emits in the background — title
generation, memory flushes, compression passes, web-extract
summarisations, session-search re-rankings, MCP tool-arg synthesis,
approval-prompt classification, skills-hub lookups, etc.

These two workload classes have **opposite shapes**:

| Property              | Primary (heavy)              | Auxiliary (light)                     |
|-----------------------|-------------------------------|---------------------------------------|
| Per-call tokens       | thousands - tens of thousands | tens - low hundreds                   |
| Latency budget        | seconds - tens of seconds     | sub-second nice-to-have, not critical |
| Quality bar           | very high (the actual answer) | "good enough" classifier / summariser |
| Concurrency pattern   | one at a time per user        | bursty, several concurrent            |
| Cost of call          | high (KV cache, decode time)  | low                                   |
| Cost of being slow    | acceptable                    | acceptable                            |
| Cost of being **down** | session-blocking              | session-blocking (compression / flush) |

When both classes share an endpoint, the auxiliary calls **steal
batching slots, KV-cache memory, and CUDA streams** from the primary
call exactly when the user is waiting on the primary call. Worse, on
the [DGX Spark platform](./8-spark-fabric-and-orchestrator.md), the
cumulative concurrent load is one of the [precursors to the abrupt
host power-off](../runs/2026-05-01-nvidia2-abrupt-power-off-vllm-long-context.md)
documented on NVIDIA forum thread #359785.

The fix is **architectural separation**: a second, smaller, cheaper
model on a separate endpoint, taking the auxiliary load. The primary
endpoint stays exclusively for the user-facing inference call.

## What's wrong today (Hermes default config)

Per the operator-pasted config in the
[2026-05-01 postmortem](../runs/2026-05-01-nvidia2-abrupt-power-off-vllm-long-context.md#hermes-config-amplifies-the-precursors-added-0105-eest):

```yaml
auxiliary:
  vision:           {provider: auto, ...}
  web_extract:      {provider: auto, ...}
  compression:      {provider: auto, ...}
  session_search:   {provider: auto, max_concurrency: 3}
  skills_hub:       {provider: auto, ...}
  approval:         {provider: auto, ...}
  mcp:              {provider: auto, ...}
  flush_memories:   {provider: auto, ...}
  title_generation: {provider: auto, ...}
```

`provider: auto` resolves to whatever the active main provider is —
which is currently `qwen-spark` at `http://192.168.1.104:8000/v1`. So
all nine auxiliary task classes funnel onto the same `:8000` endpoint
that the user-facing reasoning call is running on, fighting for the
same KV cache and the same `--max-num-seqs 128` slots.

## Three deployment patterns

Ordered by hardware availability today → after the
[8-spark plan](./8-spark-fabric-and-orchestrator.md) lands.

### Pattern A — today, no new hardware: `ms02` runs Ollama (or llama.cpp)

`ms02` (Threadripper 3970X dev host, no GPU) already exists and is on
the LAN. It can serve a small text-only model on CPU at perfectly
adequate throughput for auxiliary tasks (10-30 tok/s on a quantised
2-7B model is fine for `title_generation` / `compression` /
`session_search`).

Recommended runtime: **Ollama** — OpenAI-compatible API out of the box,
single binary, model pulls are one-liners, doesn't fight any of the
existing ms02 services. Alternatively llama.cpp's `llama-server` if you
want one-fewer-daemon.

Recommended models on this tier (pick one):

| Model | Size on disk | RAM at runtime | Notes |
|---|---|---|---|
| `qwen2.5:3b-instruct` | ~2.2 GB Q4 | ~3 GB | Multi-purpose, strong instruction following |
| `phi-4-mini-instruct` | ~2.4 GB Q4 | ~3 GB | Microsoft's small reasoning model, good at structured outputs |
| `llama-3.2:3b-instruct` | ~2 GB Q4 | ~3 GB | Meta's modern small model |
| `qwen2.5-coder:1.5b` | ~1 GB Q4 | ~2 GB | If auxiliary is mostly code-shaped (skills_hub, MCP arg synth) |

Sketch (do not deploy until decided):

```bash
# on ms02
curl -fsSL https://ollama.com/install.sh | sh
sudo systemctl enable --now ollama
ollama pull qwen2.5:3b-instruct
# Ollama listens on 127.0.0.1:11434 by default
# Bind to LAN with: Environment="OLLAMA_HOST=0.0.0.0:11434" in
# /etc/systemd/system/ollama.service.d/override.conf
```

Then in `~/.hermes/config.yaml`:

```yaml
providers:
  qwen-spark:
    base_url: http://192.168.1.104:8000/v1     # main, stays as is
    models: [...]
  small-aux-ms02:                              # NEW
    base_url: http://192.168.1.103:11434/v1    # ms02 LAN IP, Ollama OpenAI shim
    models:
      - id: qwen2.5:3b-instruct
        context_window: 32768
        max_tokens: 2048

auxiliary:
  vision:           {provider: qwen-spark}     # stays on main (vision needs the big model)
  web_extract:      {provider: small-aux-ms02} # cut over
  compression:      {provider: small-aux-ms02} # cut over
  session_search:   {provider: small-aux-ms02, max_concurrency: 3}  # cut over (concurrency now harmless)
  skills_hub:       {provider: small-aux-ms02} # cut over
  approval:         {provider: small-aux-ms02} # cut over
  mcp:              {provider: small-aux-ms02} # cut over (most MCP arg synth is small)
  flush_memories:   {provider: small-aux-ms02} # cut over
  title_generation: {provider: small-aux-ms02} # cut over
```

**Caveats**:

- ms02 is a dev host. It can be torn down for kind / Tilt experiments.
  When that happens, Hermes auxiliary calls fail. Either fall back to
  `provider: qwen-spark` for those tasks (degraded, original behaviour)
  or treat ms02 as having a contract for these calls.
- ms02 is in the [Cursor / Tilt LAN-tunnel](./starlink-wifi-lan-port-filter.md)
  workaround scope — make sure `:11434` is reachable from the Mac if
  Hermes runs there.
- This is the **least-investment, most-reversible** option. Good
  candidate for a 1-2 week trial right now.

### Pattern B — bridge, low-investment: ComputeBlade Pi cluster runs Ollama

Once the [20-blade ComputeBlade Pi cluster](./8-spark-fabric-and-orchestrator.md)
is online (any Pi blade is plenty for a 1.5-3B Q4 model), deploy Ollama
or llama.cpp as a k8s Deployment with a Service exposing port 11434.
Hermes points `small-aux-pi` at the cluster Service IP. This frees ms02
for dev work and gives the auxiliary endpoint a real availability story
(replicate across blades, k8s reschedules on failure).

This is the same Hermes config as Pattern A, just a different
`base_url` (e.g. `http://aux-llm.lan:11434/v1`).

### Pattern C — final, post-MS-A2: dedicated small model on `orchestrator1`

When the [Minisforum MS-A2](./8-spark-fabric-and-orchestrator.md)
arrives as `orchestrator1` (Phase 2), the small auxiliary model becomes
a first-class systemd-managed service on the orchestrator host. Two
sub-options:

- **C.1 — vLLM-CPU on `orchestrator1`** — the orchestrator already
  runs the nginx gateway, the relocated daemons (`hf-prefetch`,
  `ngc-image-sync`, `vllm-stack-autoupgrade` after Phase 3), and is
  the natural single point of presence for "anything Nvidia-cluster-
  related but not on the GPU hosts". A second vLLM (CPU backend) on
  `:8001` keeps the operator surface uniform with the main `:8000`
  vLLM. Same `vllm serve` argv style, same prefix-caching support,
  same OpenAI API endpoints — Hermes config unchanged from Pattern A
  except for the host.
- **C.2 — Ollama on `orchestrator1`** — simpler ops, one fewer
  vLLM instance to upgrade with the autoupgrade daemon, but a
  different operator mental model than the Sparks. Probably fine.

Pick whichever fits the operator's preference. **C.1 keeps everything
on one technology**; **C.2 keeps everything on one host with two
tools**. Decision can be deferred until MS-A2 actually lands.

This is **Phase 6 of the [8-spark plan](./8-spark-fabric-and-orchestrator.md#migration-phases)**
(or its sibling — the small-model role can deploy any time after
Phase 2 because it doesn't depend on the Spark TP=8 cutover).

## Hermes-side mapping table

Hermes auxiliary task → recommended target tier:

| Hermes auxiliary key | Workload shape | Default target | Notes |
|---|---|---|---|
| `vision` | vision-language understanding | **stays on `qwen-spark`** | Needs Qwen3-VL when we cut over to it. Don't downgrade vision to a small text-only model. |
| `compression` | summarising old turns to fit context | small-aux | Frequent (every time `compression.threshold` hits); a major source of concurrent load on the main model today. Highest-impact cutover. |
| `flush_memories` | classifying which memories are worth keeping | small-aux | Frequent (every `flush_min_turns` = 6 turns). |
| `session_search` | re-ranking retrieved sessions | small-aux | `max_concurrency: 3` becomes safe to keep (or even raise) on a dedicated auxiliary endpoint. |
| `title_generation` | summarising session into a 5-word title | small-aux | One-shot per session, but trivial — don't pay big-model overhead for it. |
| `web_extract` | summarising scraped HTML | small-aux | Bursty when browsing; can be slow without hurting agent UX. |
| `skills_hub` | matching user request to skill descriptions | small-aux | Classification, low token count. |
| `approval` | "is this command safe?" classifier | small-aux | Critical-path latency; fast small model is actually *better* here. |
| `mcp` | synthesising MCP tool arguments | small-aux **with caveat** | Most MCP arg synth is small and fits. **But**: complex MCP tools with rich schemas may benefit from the big model — measure per-tool. |

## What you don't get with this pattern

- **Auxiliary quality regression**. Small models are worse at the same
  task. For most of the auxiliary list this is fine (compression,
  title generation, session_search re-rank are all "good enough"
  shapes). For `mcp` arg synthesis on tools with complex schemas,
  test before committing — degraded MCP arg synth shows up as more
  tool-call retries, which is bad.
- **Operator complexity**. Two endpoints, two model lifecycles, two
  monitoring surfaces. The benefit has to be worth the operator
  overhead — for our specific platform fragility motive (the
  [GX10 abrupt power-off](../runs/2026-05-01-nvidia2-abrupt-power-off-vllm-long-context.md))
  it clearly is.
- **A vendor-side fix**. If NVIDIA fixes the GX10 abrupt-power-off
  bug, the platform-fragility motivation evaporates. The
  capacity-planning and quality-isolation motives remain — auxiliary
  isolation is good practice irrespective.

## When to do this

In rough order of when to act:

1. **Hermes single-user-side config tightening first** (table in the
   [postmortem](../runs/2026-05-01-nvidia2-abrupt-power-off-vllm-long-context.md#recommended-hermes-side-config-changes-no-cluster-impact))
   — context_window, max_tokens, compression threshold, max_concurrency.
   Zero new infra; reversible per-key. Run for 1-2 weeks; measure
   crash MTBF.
2. **If crashes continue OR auxiliary load is visibly impacting
   primary latency**, deploy Pattern A on ms02 as a 1-week trial.
   Cut over auxiliaries one-at-a-time so you can attribute any
   regression to a specific task.
3. **If Pattern A works**, formalise into Pattern B once the Pi
   cluster is live, or Pattern C once MS-A2 is online — whichever
   comes first.

The 8-spark plan's Phase 6 is the **latest** acceptable ship date for
this; Pattern A is the **earliest** without buying new hardware.

## Cross-refs

- [runs/2026-05-01-nvidia2-abrupt-power-off-vllm-long-context.md](../runs/2026-05-01-nvidia2-abrupt-power-off-vllm-long-context.md) — incident this concept is filed from; contains the Hermes config table and the precursor analysis.
- [concepts/8-spark-fabric-and-orchestrator.md](./8-spark-fabric-and-orchestrator.md) — defines the orchestrator host and Phase 6 cutover where Pattern C lands; the deployment topology for the auxiliary endpoint.
- [concepts/ngc-stacked-container-stack.md](./ngc-stacked-container-stack.md) — the primary endpoint this pattern is protecting; auxiliary isolation is *additive* to the existing Docker + Ansible stack and doesn't touch it.
- [concepts/pii-ai-data-plane.md](./pii-ai-data-plane.md) — the auxiliary model is **also self-hosted** and stays on-LAN; no PII boundary crossing implications.
