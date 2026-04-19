---
title: 2026-04-18 — NGC container stack bringup (first green)
kind: run
status: active
outcome: partial-success
tags: [ngc, bringup, tinyllama, ncclcommInitRank, tp1]
updated: 2026-04-18
related:
  - runs/2026-04-18-rip-out-bare-metal.md
  - concepts/ngc-stacked-container-stack.md
  - concepts/ngc-image-transformers-lag.md
  - concepts/ncclcommInitRank-abort-tp2.md
---

# NGC container stack bringup

First live local LLM served via the NGC container stack after the bare-metal rip-out.

## Timeline

1. **Ansible run** (`provision_sparks.yml --skip-tags apt`) — clean end-to-end, after
   fixing three small bugs:
   - `ansible.builtin.shell: set -euo pipefail` failed under `/bin/sh` (dash). Added
     `args: executable: /bin/bash` to three `shell` tasks in
     `roles/vllm_stacked_container/tasks/main.yml`.
   - `fastsafetensors` is not in `nvcr.io/nvidia/vllm:26.01-py3` → set
     `vllm_load_format: ""` in `sparks.yml` to omit the flag.
   - `google/gemma-4-31B-it` architecture unknown to NGC 26.01's transformers (new
     failure mode filed: [concepts/ngc-image-transformers-lag.md](../concepts/ngc-image-transformers-lag.md)).
2. **Gemma-3 27B attempt** → gated HF repo, 401/403. Pivoted.
3. **Qwen2.5-32B prefetch** → started in background (~11 MB/s, ~90 min for 64 GB).
4. **TinyLlama TP=2 attempt** (already cached) → `ncclCommInitRank` aborts on both
   ranks despite healthy Ray cluster (2 nodes, 2 GPUs visible). Filed:
   [concepts/ncclcommInitRank-abort-tp2.md](../concepts/ncclcommInitRank-abort-tp2.md).
5. **TinyLlama TP=1** (leader only, Ray not used for TP but still present) → boots in
   ~90 s, `/v1/models` 200, `/v1/chat/completions` returns `PONG` as requested.

## Final working config (this run)

| Item | Value |
|---|---|
| Container image | `nvcr.io/nvidia/vllm:26.01-py3` |
| Stack | `vllm-ngc-ray-head` (leader) + `vllm-ngc-ray-worker-nvidia2` (follower) |
| Ray cluster | 2 nodes, 2 GPUs, 40 CPU, 219 GiB memory, 19.5 GiB object store |
| Model served | `TinyLlama/TinyLlama-1.1B-Chat-v1.0`, TP=1 |
| API | `http://nvidia1:8000/v1` |
| Smoke test | `curl .../v1/chat/completions … {"content":"PONG"}` |

## What we know now

- NGC container stack + Docker-managed Ray works end-to-end.
- The `docker exec -d bash -lc 'vllm serve …'` pattern **loses stdout/stderr** — fine
  for successful runs but makes diagnostics impossible. Mitigation: `exec > /tmp/vllm-serve.log 2>&1`
  inside the detached shell. The role should be updated to always redirect.
- `vllm` (from NGC) supports `--distributed-executor-backend ray` and TP=1 cleanly.
- Gemma-4 requires `vllm/vllm-openai:gemma4-cu130` (custom image), **not** NGC 26.01.

## Follow-ups

- [ ] **Fix cross-node NCCL** — see
      [concepts/ncclcommInitRank-abort-tp2.md](../concepts/ncclcommInitRank-abort-tp2.md).
      Candidates: interface `enp1s0f1np1` vs `enp1s0f0np0`,
      `NCCL_P2P_DISABLE=1`, `NCCL_CUMEM_ENABLE=0`, `NCCL_NET=Socket`,
      disabling AWS OFI NCCL plugin. Try them one at a time against the role's env file.
- [ ] **Harden role** — always redirect `vllm serve` output to a persistent log file
      so future crashes are diagnosable without re-running attached.
- [ ] **Qwen2.5-32B**: once prefetch completes (~90 min), rerun with `TP=2` as the real
      stacked test. Blocked on NCCL fix above.
- [ ] **Gemma-4 future**: add `vllm_stacked_container_image` override in
      `host_vars/` for when we want `vllm/vllm-openai:gemma4-cu130`; cross-validate
      against the stacked flow.
- [ ] Flip `vllm_stacked_container_recreate: true` back to `false` after stable run.
- [ ] Drop `NCCL_DEBUG: INFO` back to `WARN` after the NCCL issue is solved.
