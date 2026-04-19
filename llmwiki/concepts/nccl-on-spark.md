---
title: NCCL on Spark (sm_121, sockets, no IB verbs)
kind: concept
status: active
tags: [nccl, spark, networking, gb10]
updated: 2026-04-18
related: [concepts/spark-interconnect.md, sources/nvidia-stacked-sparks.md]
sources: [../../docs/vllm-multi-node.md]
---

# NCCL on Spark

Config we've proven works for TP=2 across two Sparks over the QSFP link-local
interconnect.

## Build (bare-metal reference)

- `nccl_version: v2.28.9-1`.
- `nccl_gencode: -gencode=arch=compute_121,code=sm_121` (Blackwell on GB10).
- Source tree under `/home/nvidia/nccl`, build under `/home/nvidia/nccl/build`.
- `nccl-tests` under `/home/nvidia/nccl-tests` (`all_gather_perf` smoke).
- OpenMPI at `/usr/lib/aarch64-linux-gnu/openmpi`.

Managed by `roles/nccl_sparks/` and `playbooks/nccl_sparks.yml`. Skipped on reruns if
`libnccl.so` exists and the tree is checked out at the exact tag.

## Runtime env (container stack — proven green 2026-04-18)

All of these are set in `vllm_distributed_extra_env` and rendered into the
`head.env` + `worker-<host>.env` files used by `--env-file`:

| Var | Value | Why |
|---|---|---|
| `NCCL_SOCKET_IFNAME` | `enp1s0f0np0` | Force socket transport over QSFP. |
| `GLOO_SOCKET_IFNAME` | same | PyTorch gloo fallback uses same iface. |
| `UCX_NET_DEVICES` | same | UCX (if used) stays off loopback. |
| `OMPI_MCA_btl_tcp_if_include` | same | OpenMPI TCP transport scoped. |
| `TP_SOCKET_IFNAME` | same | vLLM TP socket path. |
| `NCCL_IB_DISABLE` | `1` | GB10 + ConnectX RoCE verbs path fails with ENOMEM under this vLLM TP. |
| `NCCL_NET_GDR_LEVEL` | `0` | Disable GPUDirect RDMA. |
| `NCCL_NVLS_ENABLE` | `0` | Disable NVLink Sharp (not usable here). |
| **`NCCL_NET_PLUGIN`** | **`none`** | **Root-cause fix.** Stops NCCL from auto-loading any of the four net plugins bundled in the NGC image (`aws-ofi-nccl`, `hpcx rdma-sharp`, `spectrum-x`, `ibext`). Without this, `ncclCommInitRank` aborts before the logger starts — see [`concepts/ncclcommInitRank-abort-tp2.md`](./ncclcommInitRank-abort-tp2.md). |
| `NCCL_P2P_DISABLE` | `1` | Defensive (set alongside the fix). 1 GPU per node so intra-node P2P is moot. |
| `NCCL_CUMEM_ENABLE` | `0` | Defensive. Disables NCCL's VMM allocator; known to misbehave on aarch64 UMA on some stacks. |
| `NCCL_SHM_DISABLE` | `1` | Defensive. Skip shared-memory transport; sockets do all inter-node work. |
| `NCCL_DEBUG` | `WARN` | Bump to `INFO` when diagnosing. |

## Why sockets instead of IB verbs

`ibv_reg_mr` / `ibv_create_qp` on GB10 + ConnectX currently return ENOMEM for this
vLLM+Ray TP collective pattern. Socket NCCL works and is fast enough at TP=2 for GB10
bandwidth. NVIDIA's stacked-sparks guide mirrors this choice.

## Interaction with firewall

ufw allows the full interconnect segment `169.254.0.0/16` — Ray opens many ports
beyond the named `6379` GCS.

## Cross-refs

- [`docs/vllm-multi-node.md`](../../docs/vllm-multi-node.md) — authoritative reference.
- `roles/nccl_sparks/tasks/`.
