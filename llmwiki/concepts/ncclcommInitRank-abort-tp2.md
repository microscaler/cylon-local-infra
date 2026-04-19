---
title: ncclCommInitRank aborts on cross-node TP=2 (Spark, NGC 26.01) — SOLVED
kind: concept
status: superseded
superseded_by: concepts/nccl-on-spark.md
superseded_on: 2026-04-18
tags: [nccl, tp, failure-mode, solved]
updated: 2026-04-18
first_observed: 2026-04-18
solved_on: 2026-04-18
related:
  - concepts/nccl-on-spark.md
  - concepts/spark-interconnect.md
  - runs/2026-04-18-ngc-container-bringup.md
  - runs/2026-04-18-tp2-nccl-solved.md
---

> **SOLVED 2026-04-18.** Root cause: the NGC `26.01-py3` image ships four NCCL net
> plugins (`aws-ofi-nccl`, `hpcx rdma-sharp`, `spectrum-x`, `ibext`) that **auto-load
> during `ncclCommInitRank`** and probe RoCE/IB devices. On GB10 + ConnectX link-local
> the probe aborts **before** NCCL's logger starts (which is why `NCCL_DEBUG=INFO`
> produced no output).
>
> **Fix**: `NCCL_NET_PLUGIN=none` in the container env (applied via `head.env` +
> `worker-<host>.env`). Defensive pairings `NCCL_P2P_DISABLE=1`,
> `NCCL_CUMEM_ENABLE=0`, `NCCL_SHM_DISABLE=1` were set at the same time and not
> individually re-verified — document any minimisation in
> [concepts/nccl-on-spark.md](./nccl-on-spark.md) when tested.
>
> First green run: `TinyLlama` TP=2 across `nvidia1`+`nvidia2`, `/v1/chat/completions`
> returned `STACKED`. See
> [runs/2026-04-18-tp2-nccl-solved.md](../runs/2026-04-18-tp2-nccl-solved.md).
>
> The analysis below is preserved as institutional memory.

# `ncclCommInitRank` SIGABRT on cross-node TP=2

## Symptom

`vllm serve <model> --tensor-parallel-size 2 --distributed-executor-backend ray`
inside `nvcr.io/nvidia/vllm:26.01-py3` on the Spark pair aborts **both** ranks
during `ncclCommInitRank`:

```
(RayWorkerWrapper pid=..., ip=169.254.102.149)     @ 0x... 352  ncclCommInitRank
(RayWorkerWrapper pid=..., ip=169.254.37.109)      @ 0x... 352  ncclCommInitRank
Fatal Python error: Aborted
...
ray.exceptions.ActorDiedError: The actor died unexpectedly before finishing this task.
Worker exit detail: Worker unexpectedly exits with a connection error code 2. End of file.
RuntimeError: Engine core initialization failed.
```

TP=1 on the same container works fine — so Ray placement, GPU visibility, CUDA
initialization, weight loading, torch.compile, CUDA graph capture, and the OpenAI API
are all healthy. The crash is specifically the **cross-node NCCL communicator init**.

`NCCL_DEBUG=INFO` was active but produced **zero** `NCCL INFO` lines before the abort
— the crash is before NCCL's own logger starts.

## Environment (verified inside head container)

```
NCCL_DEBUG=INFO
NCCL_SOCKET_IFNAME=enp1s0f0np0
GLOO_SOCKET_IFNAME=enp1s0f0np0
UCX_NET_DEVICES=enp1s0f0np0
OMPI_MCA_btl_tcp_if_include=enp1s0f0np0
TP_SOCKET_IFNAME=enp1s0f0np0
NCCL_IB_DISABLE=1
NCCL_NET_GDR_LEVEL=0
NCCL_NVLS_ENABLE=0
MASTER_ADDR=169.254.102.149
VLLM_HOST_IP=<this host's 169.254.x>
AWS_OFI_NCCL_VERSION=1.17.0   # <-- present in the image
```

```
nvidia-smi → NVIDIA GB10 (driver 580.142, CUDA 13.1)
torch.cuda: device_count=1, cc=(12, 1) → sm_121 (Blackwell)
NCCL: 2.29.2 (/lib/aarch64-linux-gnu/libnccl.so.2), AWS OFI NCCL plugin 1.17.0
Ray cluster: 2 nodes, 2 GPUs, link-local interconnect via enp1s0f0np0
```

## Hypotheses (ranked)

1. **AWS OFI NCCL plugin auto-loads and crashes** on the Spark link-local interconnect
   (no libfabric transport for 169.254/16).
   Try: `NCCL_NET=Socket` (force socket net and skip plugin auto-detect).
2. **Wrong QSFP port** — NVIDIA's own stacked-sparks reference uses `enp1s0f1np1`
   (the `np1` port). We're on `enp1s0f0np0` (`np0`). Both are link-local IPv4 so Ray
   joins either way, but NCCL may need the specific port that has `(Up)` state in
   `ibdev2netdev`.
3. **GB10 peer-access semantics** — unified-memory CUDA on Blackwell sometimes needs
   explicit `NCCL_P2P_DISABLE=1` / `NCCL_CUMEM_ENABLE=0` / `NCCL_SHM_DISABLE=1` to
   avoid early allocator aborts.
4. **NCCL inter-node rendezvous races** when `MASTER_ADDR` is on the same subnet as the
   container's `--network host` loopback — unlikely but possible.
5. **Dropped UMA cache** — NVIDIA troubleshooting lists this; worth `sync; echo 3 >
   /proc/sys/vm/drop_caches` on both hosts before stacked init.

## Recommended experiments (run one at a time)

```yaml
# inventory/group_vars/sparks.yml — vllm_distributed_extra_env additions
vllm_distributed_extra_env:
  # ... existing keys ...
  NCCL_NET: "Socket"             # experiment A: bypass OFI plugin
  NCCL_P2P_DISABLE: "1"          # experiment B
  NCCL_CUMEM_ENABLE: "0"         # experiment C
  NCCL_SHM_DISABLE: "1"          # experiment D
```

After each edit: set `vllm_stacked_container_recreate: true`, rerun
`ansible-playbook playbooks/provision_sparks.yml --skip-tags apt`, tail
`/tmp/vllm-serve.log` (after enabling persistent redirection — see the follow-up on
[runs/2026-04-18-ngc-container-bringup.md](../runs/2026-04-18-ngc-container-bringup.md)).

Also verify which QSFP is "up":

```bash
ssh casibbald@nvidia1 'ibdev2netdev 2>/dev/null; ip -br link | grep -E "enp1s0"'
```

If `enp1s0f1np1` is the live interface, swap `nccl_interface` in `sparks.yml` and
recreate.

## Update when solved

When we determine the fix, update this page to `status: superseded` and drop the
pointer into `concepts/nccl-on-spark.md` so the general NCCL-on-Spark page keeps the
authoritative working recipe.
