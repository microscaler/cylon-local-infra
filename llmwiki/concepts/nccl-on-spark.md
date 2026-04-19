---
title: NCCL on Spark (RoCE v2 + GPUDirect RDMA)
kind: concept
status: active
tags: [nccl, spark, networking, gb10, rdma, roce, connectx-7]
updated: 2026-04-19
related:
  - concepts/spark-interconnect.md
  - runs/2026-04-19-roce-cutover.md
  - concepts/ncclcommInitRank-abort-tp2.md
  - sources/nvidia-stacked-sparks.md
sources:
  - ../../docs/vllm-multi-node.md
---

# NCCL on Spark

Authoritative runtime config for TP=2 across two GB10 Sparks.

**Current data path: RoCE v2 + GPUDirect RDMA over the ConnectX-7 / QSFP link.**
Replaced the 2026-04-18 sockets-only workaround on 2026-04-19 — see
[`runs/2026-04-19-roce-cutover.md`](../runs/2026-04-19-roce-cutover.md) for the
before/after numbers (3.9× peak decode, 3.8× long-prefill).

## Build (bare-metal reference)

Still present for the `nccl-tests` binaries used by the bring-up and cutover
benches. Not on the stacked-container hot path — that uses NCCL bundled in the
NGC image (`NCCL_VERSION=2.29.7`).

- `nccl_version: v2.28.9-1`.
- `nccl_gencode: -gencode=arch=compute_121,code=sm_121` (Blackwell on GB10).
- Source tree under `/home/nvidia/nccl`, build under `/home/nvidia/nccl/build`.
- `nccl-tests` under `/home/nvidia/nccl-tests`.
- OpenMPI at `/usr/lib/aarch64-linux-gnu/openmpi`.

Managed by `roles/nccl_sparks/` and `playbooks/nccl_sparks.yml`.

## Runtime env — container stack, RoCE+GDR (canonical, 2026-04-19)

Lives in `inventory/group_vars/sparks.yml` under `vllm_distributed_extra_env`;
rendered into `head.env` + `worker-<host>.env` via `--env-file`.

| Var | Value | Role |
|---|---|---|
| `NCCL_SOCKET_IFNAME` | `enp1s0f0np0` | **Bootstrap only.** ncclUniqueId / OOB over QSFP IP. |
| `GLOO_SOCKET_IFNAME` | same | PyTorch gloo fallback. |
| `UCX_NET_DEVICES` | same | UCX scoped to the QSFP IF. |
| `OMPI_MCA_btl_tcp_if_include` | same | OpenMPI TCP transport scoped. |
| `TP_SOCKET_IFNAME` | same | vLLM TP socket path. |
| **`NCCL_IB_DISABLE`** | **`0`** | Enable NCCL's *internal* verbs transport (`NET/IB`). |
| **`NCCL_IB_HCA`** | **`rocep1s0f0`** | Pin to the cabled ConnectX-7 port (others are DOWN or uncabled). |
| **`NCCL_NET_GDR_LEVEL`** | **`PHB`** | Enable GPUDirect RDMA at PCIe-host-bridge distance — per `nvidia-smi topo -m` GB10 GPU↔NIC sits at `NODE`, so `PHB` is the permissive floor. |
| **`NCCL_NET_PLUGIN`** | **`none`** | **Keep external plugins off.** The 26.01-py3 abort that forced us to sockets was inside `aws-ofi-nccl` / `hpcx rdma-sharp` / `spectrum-x` / `ibext` — not inside NCCL's internal IB path. Belt-and-braces. |
| `NCCL_P2P_DISABLE` | `1` | No NVLink between separate Sparks. |
| `NCCL_CUMEM_ENABLE` | `0` | Defensive. NCCL VMM allocator disabled — preserved from the 26.01 era, not re-qualified. |
| `NCCL_SHM_DISABLE` | `1` | No shared-mem transport between separate nodes. |
| `NCCL_NVLS_ENABLE` | `0` | No NVLink Sharp. |
| `NCCL_DEBUG` | `WARN` | Bump to `INFO` when diagnosing. |
| `RAY_memory_monitor_refresh_ms` | `0` | Disable Ray's periodic memory poll. |
| `RAY_CGRAPH_get_timeout` | `900` | Ray v1 compiled-DAG default (300 s) can kill TP workers on long steps. |

## Container wiring

The Ray head and worker containers need verbs device access and pinned-memory
capability. Managed by `vllm_stacked_container_rdma_enabled: true` in
`roles/vllm_stacked_container/defaults/main.yml` (default on):

```
--device /dev/infiniband
--cap-add IPC_LOCK
--ulimit memlock=-1:-1
```

Without these, `ibv_open_device` fails and NCCL silently falls back to sockets
(or crashes, depending on version). Confirm via `docker inspect ... --format
'{{.HostConfig.Devices}}'` after a recreate.

## The plugin-vs-transport distinction (important)

`NCCL_NET_PLUGIN` and `NCCL_IB_DISABLE` are **orthogonal**:

- `NCCL_NET_PLUGIN` toggles *external* tertiary NCCL net plugins (shared
  objects the image loads at runtime: `libnccl-net-*.so`). The NGC image
  ships four of them; on GB10 one of them aborted on init — see
  [`ncclcommInitRank-abort-tp2`](./ncclcommInitRank-abort-tp2.md).
- `NCCL_IB_DISABLE` toggles NCCL's *internal* verbs code path (statically
  linked into `libnccl.so`). This one works fine on Blackwell.

2026-04-18 we disabled **both** to get the cluster off the ground. 2026-04-19
we re-enabled only the internal one after proving it via a standalone two-rank
`all_reduce_perf` test (13.93 GB/s vs 2.02 GB/s sockets).

## Fabric

Each Spark has 4× ConnectX-7 ports at 200 Gb/s:

```
rocep1s0f0  → enp1s0f0np0      ACTIVE LINK_UP  ← cabled (QSFP link-local)
rocep1s0f1  → enp1s0f1np1      DOWN
roceP2p1s0f0 → enP2p1s0f0np0   ACTIVE LINK_UP  ← uncabled (second ASIC)
roceP2p1s0f1 → enP2p1s0f1np1   DOWN
```

`link_layer: Ethernet` → RoCE v2 (not native IB). `max_mtu: 4096`, but
`active_mtu: 1024` today — lifting that is a deferred follow-up in the cutover
run doc.

## Firewall

ufw allows the full interconnect segment `169.254.0.0/16`. RoCE v2 data runs
directly on the NIC (skipping the kernel socket path) but the NCCL OOB
bootstrap still rides the socket IF.

## Rollback (sockets-only path)

If something regresses on RoCE, revert to the proven-green socket config by
flipping two env vars + one role flag:

```yaml
# inventory/group_vars/sparks.yml
NCCL_IB_DISABLE: "1"
NCCL_NET_GDR_LEVEL: "0"

# Anywhere overriding the role default:
vllm_stacked_container_rdma_enabled: false
```

Then `ansible-playbook playbooks/cutover_roce.yml` (the same playbook — it
just runs with the other values). Two-minute revert.

## Cross-refs

- [`runs/2026-04-19-roce-cutover.md`](../runs/2026-04-19-roce-cutover.md) —
  the cutover run with measurements.
- [`docs/vllm-multi-node.md`](../../docs/vllm-multi-node.md) — in-repo
  authoritative reference.
- `roles/nccl_sparks/tasks/` — host-side NCCL build (reference only; the
  container stack uses the NGC image's NCCL).
