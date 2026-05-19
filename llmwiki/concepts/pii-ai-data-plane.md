# pii-ai-data-plane

**Why the self-hosted vLLM stack exists** — the single architectural invariant that justifies the nvidia1/nvidia2 hardware, the FP8 model, the NCCL tuning, and every IDE integration page in this wiki.

Read this before touching the vLLM serving path, before adding any new LLM call-site in `hauliage`, and before proposing a cloud-LLM substitute "just for dev".

## The rule

> **Third-party documents uploaded to hauliage contain PII, financial data, and compliance-regulated content. That material MUST NOT cross a network boundary to any LLM vendor we don't control.**

This is a data-protection obligation, not a performance or cost choice. Under the processor contracts we sign with haulier/shipper customers (and under GDPR Art. 28 / POPIA §19–22 / typical enterprise DPAs):

- We are the **processor** of customer documents.
- Any onward transfer to a sub-processor (OpenAI, Anthropic, Google, Cohere, Mistral SaaS, etc.) would require enumerated DPA flow-downs, sub-processor lists published to customers, audit rights, and regional data-residency guarantees that most frontier-LLM vendors do not provide on their default API tiers.
- The simplest, defensible posture is: **customer document bytes never leave our infrastructure.**

Self-hosted vLLM on the Sparks is the enforcement mechanism.

## The two data planes

The topology splits cleanly by **what data is in the prompt**, not by what tool the human is using:


| Plane                       | Prompt contains                                                       | Destination                                                       | Example call-sites                                                                                                       |
| --------------------------- | --------------------------------------------------------------------- | ----------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------ |
| **PII runtime plane**       | Customer document text, extracted entities, compliance-flagged fields | Self-hosted vLLM only (`192.168.1.104:8000`)                      | `hauliage` document-extraction worker, any future `hauliage` microservice that summarises/classifies customer content    |
| **Code/UI authoring plane** | Our own source code, diffs, config, architectural questions           | Cloud LLMs are acceptable (Cursor / Antigravity / Codex / Claude) | Cursor daily driver (backend Rust in `hauliage/microservices/`), Antigravity (frontend/portal), agent-assisted refactors |


The key observation: Cursor prompting Claude with a diff of `migrator/src/main.rs` is **not** a PII event — it's our own IP, covered by our own NDAs with the vendor. The same engineer pasting a haulier's shipping manifest into that same chat window **would be** a PII event. The tool is the same; the data class is different.

Engineering discipline: any runtime code path in `hauliage` that calls an LLM must target the self-hosted endpoint. IDEs are for authoring, not for processing customer data at runtime.

## The data flow

```
┌───────────────────────────┐
│  Customer (web / mobile)  │
│    uploads document       │
└─────────────┬─────────────┘
              │ signed URL PUT
              ▼
┌───────────────────────────┐
│  GCS bucket               │   (encrypted at rest, GCP CMEK)
│  hauliage-customer-docs/  │
└─────────────┬─────────────┘
              │ object-created event
              ▼
┌───────────────────────────┐
│  hauliage extraction      │   Rust microservice in
│  worker (on ms02 Kind)    │   hauliage/microservices/<name>
│                           │
│  1. download doc          │
│  2. text extraction       │   (pdf/ocr/parser — runs local)
│  3. LLM call ────────────┐│
│  4. validate JSON         ││
│  5. DB insert             ││
└─────────────┬─────────────┘│
              │              │   OpenAI-compatible HTTP
              │              │   POST /v1/chat/completions
              │              │   response_format: json_schema
              │              ▼
              │     ┌─────────────────────────┐
              │     │ vLLM on nvidia1 + nvidia2│
              │     │ Qwen3.5-35B-A3B-FP8      │
              │     │ RoCE v2 / GDR / TP=2     │
              │     └─────────────────────────┘
              ▼
    ┌───────────────────────┐
    │ Hauliage Postgres     │
    │  (extracted entities, │
    │  PII fields, flags)   │
    └───────────────────────┘
```

Network path is LAN-direct, no tunnel, no cloud hop: `ms02` (Kind pod) → `192.168.1.104:8000` (nvidia1). Zero egress from the PII plane to the public internet.

## Integration contract (how hauliage microservices call vLLM)

Reuse the exact same endpoint the IDE integrations already exercise — Zed, `pi`, and the Cursor/ngrok fallback all prove the endpoint works end-to-end, every day, from the same Mac LAN. The production microservice uses the same shape:

- **Client**: OpenAI-compatible. In Rust, use `[async-openai](https://docs.rs/async-openai)` with `api_base = "http://192.168.1.104:8000/v1"`. A placeholder bearer (`EMPTY` / `sk-local-vllm`) is fine — vLLM ignores it. Plan to replace with mTLS + real auth when we harden the LAN boundary.
- **Model id**: `Qwen/Qwen3.5-35B-A3B-FP8`. Other aliases (`qwen3`, `gpt-4o-mini`) exist for IDE-validator workarounds only — microservices should use the canonical id so future model swaps are explicit.
- **Structured extraction**: `response_format: {"type": "json_schema", "json_schema": {...}}`. vLLM supports it natively via guided decoding; do not parse prose. PII fields get strict schemas (`string`, `pattern`, `enum`) so malformed model output fails closed at the HTTP boundary.
- **Tool calling**: available via the already-configured `--tool-call-parser qwen3_coder`. Prefer **structured output** over tool calls for extraction — tools are for side-effecting agents, extraction is a pure function.
- **Thinking**: OFF by default (`chat_template_kwargs.enable_thinking: false` via `thinkingFormat: "qwen-chat-template"` convention). Reasoning costs 7–96× wall clock per the [thinking validation run](../runs/2026-04-19-qwen3-thinking-validation.md); document extraction is pattern-matching, not logic puzzles. Turn it back ON per-request only for compliance rules that require derivation.
- **Concurrency budget**: single worker replica, max 8 concurrent LLM calls. Batch=16 tops out at ~285 tok/s aggregate on the FP8 stack ([fp8-drag-race run](../runs/2026-04-19-fp8-drag-race.md)); 8 leaves headroom for the IDE plane sharing the endpoint.
- **Failure mode**: extraction failures **must not** cause the document to fall back to a cloud LLM. If self-hosted is down, the job retries with exponential backoff and paging-alerts on sustained failure. There is no fallback to cloud — that's the whole point.

## Why the IDE integrations are load-bearing

The [Zed](../runs/2026-04-20-zed-vllm-integration.md), `[pi](../runs/2026-04-20-pi-cli-vllm-integration.md)`, and [Cursor](../runs/2026-04-19-cursor-ide-vllm-integration.md) integrations aren't incidental — they're the continuous dogfood that keeps the production endpoint honest. If the IDE plane breaks, the operator notices within minutes. If no one touched the endpoint for weeks, a silent regression would only surface when customer extractions started failing. The daily-driver traffic is a free canary.

## Anti-patterns to reject

- **"Let's use OpenAI for the hard PII cases until Qwen gets good enough."** — No. That's the exact behaviour the data-plane split exists to prevent. Improve the prompt, improve the schema, fine-tune the model; do not leak.
- **"We'll hash/redact the PII before calling OpenAI."** — Redaction is easy to get subtly wrong (name-like locations, non-Latin scripts, compound IDs). Auditable posture is "bytes never left" — not "bytes left but we think we scrubbed them".
- **"Dev/staging can call cloud LLMs since the data is fake."** — Only if you can prove the staging corpus is 100% synthetic. The moment real anonymised customer data enters staging (it always does), staging becomes prod for data-protection purposes.
- **"Just for this one endpoint, use Claude's tool-calling — it's better."** — The quality gap is real today and will close within quarters. The DPA obligations won't.

## See also

- [ngc-stacked-container-stack](./ngc-stacked-container-stack.md) — how the vLLM endpoint is actually served.
- [nccl-on-spark](./nccl-on-spark.md) — why the endpoint is fast enough to matter.
- [2026-04-19 fp8-drag-race](../runs/2026-04-19-fp8-drag-race.md) — current perf envelope (single-stream 63 tok/s, aggregate 285+ tok/s).
- [2026-04-19 qwen3-thinking-validation](../runs/2026-04-19-qwen3-thinking-validation.md) — when `enable_thinking` is worth 10–100× wall time.

