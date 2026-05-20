# Active Context

**Last updated:** 2026-05-20 — torchrun migration in progress; API not yet green.

**Git baseline:** tag `spark-ray-baseline-2026-05-20` (commit `223505e`).

**Active workstreams:**

1. **Move away from Ray** — [llmwiki/runs/2026-05-20-torchrun-migration-kickoff.md](../llmwiki/runs/2026-05-20-torchrun-migration-kickoff.md). Validate `vllm_torchrun_stacked` on nvidia1+2, bump to 26.04-py3, flip `vllm_stack_kind: torchrun`.
2. **8× Ascent GX10 arrival** — [llmwiki/runs/2026-05-20-8-spark-hardware-arrival.md](../llmwiki/runs/2026-05-20-8-spark-hardware-arrival.md). Discovery checklist for Sparks 3–8; inventory onboarding deferred until IPs/serials recorded.

**Production default (inventory):** `vllm_stack_kind: torchrun` on `26.03-py3` (26.04 bump pending validation). **API :8000 down** — torchrun containers crash-loop after ~2 min.

**Fixes landed (commits `41bfabc`, `0c51b68`, `156c30c`):**

- Ray pgrep self-match → `[v]llm serve`
- Torchrun leader-first + headless follower + master-addr + /v1/models wait
- Qwen patch `failed_when: false`; torchrun defensive `docker rm` + run_once pause

**Blockers:**

- Concurrent provision runs flip `vllm_stack_kind` and destroy containers mid-playbook — coordinate single operator
- Torchrun crash-loop root cause TBD (empty `/root/vllm-torchrun.log`; suspect HF cache parity on nvidia2 or pip/fastsafetensors OOM at startup)
- Ray rollback available via tag + `vllm_stack_kind: ray` + `just spark-provision-recreate`

**Next:** single maintenance window — `just spark-provision-recreate` with stable inventory; if torchrun fails, check HF cache on nvidia2 via `hf-prefetch` rsync from nvidia1.
