---
title: 2026-04-18 — cross-node TP=2 NCCL fix (first stacked green)
kind: run
status: active
outcome: success
tags: [nccl, tp2, stacked, solved]
updated: 2026-04-18
related:
  - runs/2026-04-18-ngc-container-bringup.md
  - concepts/ncclcommInitRank-abort-tp2.md
  - concepts/nccl-on-spark.md
  - concepts/ngc-stacked-container-stack.md
---

# Cross-node TP=2 NCCL fix — first stacked green

## Context

After the [NGC container bringup](./2026-04-18-ngc-container-bringup.md) got TP=1
serving, TP=2 still aborted in `ncclCommInitRank` with **zero** `NCCL INFO` output —
the abort was firing before NCCL's logger started.

## Diagnostic path

1. **Interface check** — `ibdev2netdev` + `ip -br link` confirmed the live QSFP
   interface is `enp1s0f0np0` on both hosts (the down interface is `enp1s0f1np1`).
   Our `nccl_interface` was already correct.
2. **Plugin inventory inside the container**:
   ```
   find / -name 'libnccl-net*.so*'
   /opt/amazon/aws-ofi-nccl/lib/libnccl-net-ofi.so
   /opt/hpcx/nccl_rdma_sharp_plugin/lib/libnccl-net.so.*
   /opt/hpcx/nccl_spectrum-x_plugin/lib/libnccl-net.so.*
   /opt/hpcx/nccl_spectrum-x_plugin/lib/libnccl-net-spcx.so
   /opt/hpcx/nccl_rdma_sharp_plugin/lib/libnccl-net-ibext.so
   ```
   Four net plugins. On GB10 + ConnectX link-local, plugin auto-load probes
   RoCE/IB devices and aborts before the NCCL logger runs.
3. **Critical insight**: `docker exec -e FOO=bar …` only affects the exec'd shell — it
   **does not** propagate to Ray actor subprocesses spawned by raylet (which is the
   container's PID 1). Env for Ray actors must be on the container itself, either via
   `--env-file` (what Ansible renders as `head.env` / `worker-*.env`) or via `-e` at
   `docker run`. **Every time we tweak NCCL env to test, we have to recreate the
   container** — `docker exec -e` experiments are a waste of time.

## Fix

Added to `vllm_distributed_extra_env` in `inventory/group_vars/sparks.yml`:

```yaml
NCCL_NET_PLUGIN: "none"       # root cause
NCCL_P2P_DISABLE: "1"         # defensive
NCCL_CUMEM_ENABLE: "0"        # defensive
NCCL_SHM_DISABLE: "1"         # defensive
```

With those in the container env, `NCCL_DEBUG=INFO` started producing output:

```
NCCL INFO NCCL_NET_PLUGIN set by environment to none
NCCL INFO Failed to initialize NET plugin IB     # expected (NCCL_IB_DISABLE=1)
NCCL INFO NET/Socket : Using [0]enp1s0f0np0:169.254.102.149<0>
NCCL INFO Initialized NET plugin Socket
NCCL INFO Using network Socket
NCCL INFO [Rank 0] ncclCommInitRank comm 0x… rank 0 nranks 2 cudaDev 0 - Init START
NCCL INFO Channel 00/08 : 0 1   (… through Channel 07/08)
NCCL INFO Channel 00/0 : 0[0] -> 1[0] [send] via NET/Socket/0
…
```

All 8 socket channels established bidirectionally, `ncclCommInitRank` completed on
both ranks.

## Second gotcha: HF cache asymmetry

TP=2 still hung with `GPU utilization = 0%`. `py-spy dump` on the stuck Ray worker
processes showed the **follower (nvidia2)** was blocked in `huggingface_hub.file_download.xet_get`
— it had NO TinyLlama weights locally, and HF xet was slow under contention with the
running Qwen prefetch. Meanwhile the leader (nvidia1) had finished its weight load and
was sitting in `main_loop` waiting for the follower to finish its own load so
`collective_rpc` could return.

Fix:
1. Killed the Qwen prefetch on both hosts (stalled for unrelated reasons).
2. `tar | ssh | tar -x` the TinyLlama cache from `nvidia1` to `nvidia2` via my Mac.
3. Re-exec `vllm serve` — rank 1 loads locally, `collective_rpc` returns, engine
   initialises, CUDA graphs captured.

Lesson for the role: **prefetch has to succeed on every host in the group**, not just
the leader. The current `hf_spark` role already loops over all Sparks, but TinyLlama
was a pre-existing manual cache on `nvidia1` only. Added a run follow-up: fold
`TinyLlama` into `hf_prefetch_models` when we next run a TP=2 smoke test.

## Smoke test

```
$ curl -s http://nvidia1:8000/v1/models | jq '.data[0].id'
"TinyLlama/TinyLlama-1.1B-Chat-v1.0"

$ curl -s http://nvidia1:8000/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"TinyLlama/TinyLlama-1.1B-Chat-v1.0",
       "messages":[{"role":"user","content":"Reply with exactly the word STACKED and nothing else."}],
       "max_tokens":8,"temperature":0.0}' | jq '.choices[0].message.content'
"STACKED"
```

## Final state

| Item | Value |
|---|---|
| Container image | `nvcr.io/nvidia/vllm:26.01-py3` |
| Model | `TinyLlama/TinyLlama-1.1B-Chat-v1.0` |
| TP | 2 (rank 0 on `nvidia1`, rank 1 on `nvidia2`) |
| NCCL transport | Socket via `enp1s0f0np0` (link-local 169.254/16) |
| NCCL channels | 8 bidirectional over NET/Socket/0 |
| API | `http://nvidia1:8000/v1` |

## Follow-ups

- [ ] **Minimise defensive NCCL env**: test `NCCL_NET_PLUGIN=none` **alone** on a
      clean container recreation. If it still works, remove `NCCL_P2P_DISABLE`,
      `NCCL_CUMEM_ENABLE`, `NCCL_SHM_DISABLE` overrides from
      [`concepts/nccl-on-spark.md`](../concepts/nccl-on-spark.md) and the inventory.
- [ ] **HF cache symmetry**: add a role check that every model in
      `hf_prefetch_models` is present on every Spark before starting `vllm serve`, or
      always let the in-container download finish from the leader first and rsync to
      followers over the interconnect.
- [ ] **Qwen2.5-32B**: rerun the prefetch now that we know TP=2 works. Consider
      using `hf-xet` local cache more aggressively, or just `tar | ssh` from leader
      after first host finishes.
- [ ] **Gemma-4**: try `vllm/vllm-openai:gemma4-cu130` image (per NVIDIA's own docs;
      see [runs/2026-04-18-ngc-container-bringup.md](./2026-04-18-ngc-container-bringup.md)).
- [ ] `NCCL_DEBUG` back to `WARN` (already done in latest inventory).
