# Qwen3.5-35B-A3B-FP8 adoption: full spark-vllm-docker recipe lift

Date: 2026-04-19
Topology: nvidia1 + nvidia2 (GB10 / Grace-Blackwell), NGC vllm 26.03-py3
Status: **staged in ansible, FP8 download running in background, cutover pending weights**

## Why

BF16 Qwen3.6-35B-A3B on dual-HCA regressed prefill -16 % vs single-HCA, despite
raw NCCL all-reduce going from 13.93 → 23.75 GB/s. Root cause investigation
pointed at:

1. un-patched Triton fused_moe kernel (PR #34279 regression → ~2× MoE slowdown)
2. no FlashInfer attention backend
3. BF16 → decode capped at ~45 tok/s by 273 GB/s LPDDR5X memory bandwidth

The reference repo `/Users/casibbald/Workspace/microscaler/spark-vllm-docker`
has the exact same 2-Spark topology (they also run dual-HCA) working at full
speed. We lifted their recipe wholesale.

## What changed

### inventory/group_vars/sparks.yml

- `vllm_default_model`: `Qwen/Qwen3.6-35B-A3B` → `Qwen/Qwen3.5-35B-A3B-FP8`
- `vllm_load_format`: `""` → `"fastsafetensors"` (ships in 26.03-py3)
- `vllm_api_server_extra_args`: added
    - `--kv-cache-dtype fp8`
    - `--attention-backend flashinfer`
    - `--enable-prefix-caching`
    - `--chat-template /vllm-patches/unsloth.jinja`
    - `--enable-auto-tool-choice`
    - `--tool-call-parser qwen3_coder`
- `vllm_distributed_extra_env`: added
    - `NCCL_IGNORE_CPU_AFFINITY: "1"`
    - `VLLM_MARLIN_USE_ATOMIC_ADD: "1"`
    - `RAY_num_prestart_python_workers: "0"`
    - `RAY_object_store_memory: "1073741824"`
- `spark_dual_hca_enabled`: `false` → `true`
- `hf_prefetch_models`: dropped Qwen3.6-35B-A3B + Qwen3-Coder-30B-A3B-Instruct,
  added `Qwen/Qwen3.5-35B-A3B-FP8`. TinyLlama smoke model kept.

### roles/vllm_stacked_container

- New `defaults/main.yml` flag `vllm_stacked_container_qwen_moe_patches_enabled: true`
  (+ host_dir and container_dir vars).
- Vendored patches under `files/qwen_moe_patches/`:
    - `fix_crash.diff`          — KV-cache null-block assertion (vllm-project/vllm @ main)
    - `fix_slowness.diff`       — reverts PR #34279 (Qwen3-Coder-Next Triton slowdown)
    - `_triton_alloc_setup.py`  — Triton → torch caching-allocator shim
    - `_triton_alloc_setup.pth` — site.py autoload for the shim
    - `unsloth.jinja`           — Qwen3.5 chat template with tool-calls + thinking
    - `README.md`               — per-file rationale + when to drop each patch
- `tasks/main.yml`:
    - Stages the files on each Spark at `/opt/vllm-stack/patches/`
    - Bind-mounts `…:/vllm-patches:ro` into both Ray head + worker containers
    - New task "Apply Qwen3 MoE patches inside Ray containers" runs after
      `ray start` but before `vllm serve`, idempotent (patch `|| true`).

### playbooks/refresh_hf_prefetch.yml (new)

One-shot playbook that invokes `hf_prefetch_service` directly, working around
the same include_role tag-propagation issue that `cutover_roce.yml` works
around for `vllm_stacked_container`. Used to push model-list changes to the
leader daemon.

## Rollout sequence (executed so far)

1. ✅ `ansible-playbook playbooks/refresh_hf_prefetch.yml` — pushed new
   `hf_prefetch_models` list. Leader daemon dropped state for Qwen3.6 +
   Qwen3-Coder, started `hf download Qwen/Qwen3.5-35B-A3B-FP8`.
2. ⏳ **CURRENT**: FP8 weights downloading on nvidia1 (~2.5 MB/s HF-side;
   total ~35 GB → ETA a few hours). State poll:
    ```
    ssh casibbald@nvidia1 'sudo jq ".models[\"Qwen/Qwen3.5-35B-A3B-FP8\"]" \
        /var/lib/hf-prefetch/state.json'
    ```
3. 🟡 PENDING: once state shows `"status": "ready"`, daemon will rsync the
   `hub/models--Qwen--Qwen3.5-35B-A3B-FP8/` subtree to nvidia2 over the
   QSFP interconnect (~30 s at 23.75 GB/s).
4. 🟡 PENDING: run the cutover
    ```
    ansible-playbook playbooks/cutover_roce.yml \
        -e vllm_stacked_container_recreate=true
    ```
   This will:
    - Recreate both Ray containers with the new env vars (dual-HCA
      `NCCL_IB_HCA=rocep1s0f0,roceP2p1s0f0`, Marlin atomic-add, Ray trimming,
      etc.) and the `/vllm-patches` bind-mount.
    - Apply fix_crash + fix_slowness + triton-alloc shim inside both
      containers (idempotent).
    - Start `vllm serve Qwen/Qwen3.5-35B-A3B-FP8` with the full FP8 flag set
      (kv-cache-dtype fp8, flashinfer, fastsafetensors, prefix caching,
      unsloth chat template, qwen3_coder tool parser).

## What we expect post-cutover

Numbers from spark-vllm-docker's published runs on the same topology (order
of magnitude; ours will differ based on LPDDR5X timings):

- Prefill: significantly faster than BF16 baseline (6897 tok/s) — FlashInfer
  + FP8 weights compound.
- Decode: 80-100 tok/s (FP8 halves per-token memory bandwidth requirement vs
  BF16's 45 tok/s wall).
- KV cache: ~2× more concurrent sequences at same context length (fp8 KV).
- Prefix caching: large TTFT win on agent workloads with shared system prompts.

## Rollback

If dual-HCA regresses again (unlikely with patches in place but possible), just:
```
# In inventory/group_vars/sparks.yml
spark_dual_hca_enabled: false
```
then rerun `playbooks/cutover_roce.yml -e vllm_stacked_container_recreate=true`.
All fabric-2 plumbing (netplan MTU 9000, NM connection, pinned GID 3) stays
provisioned either way.

If FP8 model itself is bad for any reason, swap `vllm_default_model` back to
`Qwen/Qwen3.6-35B-A3B` and re-add it to `hf_prefetch_models`, then refresh
hf_prefetch + cutover.

## Related

- [2026-04-19-dual-pcie-path-cutover.md](2026-04-19-dual-pcie-path-cutover.md)
  — the BF16 dual-HCA regression story that motivated this investigation.
- [2026-04-19-roce-cutover.md](2026-04-19-roce-cutover.md) — initial RoCE
  bring-up (TCP → RoCE v2 + GDR).
- Upstream tracking:
    - vllm-project/vllm#33857 (Qwen3-Coder-Next slowdown / PR #34279 revert)
    - vllm-project/vllm (KV-cache-manager null-block assertion — no issue link yet)
