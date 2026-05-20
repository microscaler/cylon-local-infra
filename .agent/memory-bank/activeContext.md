# Active Context

**Last updated:** 2026-05-20 — tagged Ray baseline; torchrun migration + 8-Spark onboarding started.

**Git baseline:** tag `spark-ray-baseline-2026-05-20` (commit `223505e`).

**Active workstreams:**

1. **Move away from Ray** — [llmwiki/runs/2026-05-20-torchrun-migration-kickoff.md](../llmwiki/runs/2026-05-20-torchrun-migration-kickoff.md). Validate `vllm_torchrun_stacked` on nvidia1+2, bump to 26.04-py3, flip `vllm_stack_kind: torchrun`.
2. **8× Ascent GX10 arrival** — [llmwiki/runs/2026-05-20-8-spark-hardware-arrival.md](../llmwiki/runs/2026-05-20-8-spark-hardware-arrival.md). Discovery checklist for Sparks 3–8; inventory onboarding deferred until IPs/serials recorded.

**Production default (unchanged until Phase 1 torchrun A/B):** `vllm_stack_kind: ray` on `26.03-py3`, TP=2 on nvidia1+nvidia2.

**Recent stack hardening (in baseline tag):** kernel 1018 pin, Wi-Fi off, GPU clock cap 2000 MHz, spark_assert gate, vllm serve reliability fixes, torchrun role wired behind toggle.
