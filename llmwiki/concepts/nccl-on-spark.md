---
title: NCCL on Spark (RoCE v2 + GPUDirect RDMA)
kind: concept
status: active
tags: [nccl, spark, networking, gb10, rdma, roce, connectx-7]
updated: 2026-05-19
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

## Runtime env — container stack, RoCE+GDR (canonical; refreshed 2026-05-19 for GID + Ray carry-over)

Canonical values live in `inventory/group_vars/sparks.yml` under
`vllm_distributed_extra_env`; templates render **`head.env` / `worker-<host>.env`**
and **append** `NCCL_IB_GID_INDEX` **last** (`roles/vllm_stacked_container/templates/ngc-ray-*.env.j2`).

| Var | Typical value | Role |
|---|---|---|
| `NCCL_SOCKET_IFNAME` | `enp1s0f0np0` | **Bootstrap only** — ncclUniqueId / OOB over QSFP link-local (`169.254.0.0/16`). |
| `GLOO_SOCKET_IFNAME` | same | PyTorch gloo fallback. |
| `UCX_NET_DEVICES` | same | UCX scoped to the QSFP IF. |
| `OMPI_MCA_btl_tcp_if_include` | same | OpenMPI TCP transport scoped. |
| `TP_SOCKET_IFNAME` | same | vLLM TP socket path. |
| **`NCCL_IB_DISABLE`** | **`0`** | Enable NCCL's *internal* verbs transport (`NET/IB`). |
| **`NCCL_IB_HCA`** | **`rocep1s0f0`** *or* **`rocep1s0f0,roceP2p1s0f0`** | Single-rail vs **dual PCIe path into Cage A** — driven by `spark_dual_hca_enabled` / `nccl_ib_hca` **in sparks.yml**. |
| **`NCCL_IB_GID_INDEX`** | **per host** (`spark_nccl_ib_gid_index`) | **Must equal** the **`show_gids`** column index for **RoCE v2 + IPv4** on **`rocep1s0f0`** (and match **`roceP2p1s0f0`** when dual‑HCA). Live GX10 pair **2026‑05‑19:** **`gx10-e1ce` (`nvidia1`) → `3`**, **`gx10-47b5` (`nvidia2`) → `3`** — inventory briefly pinned **`4`** on `nvidia2` when tables looked asymmetric; **`4`** today selects **no IPv4 v2 row** → `ibv_modify_qp` EINVAL / **`remote GID ::`**. Wrong index ⇒ `PyNcclCommunicator` / `ncclCommInitRank` “unhandled system error”. **Do not** set this inside `vllm_distributed_extra_env` dict — templates own it so it stays last in the env file. |
| **`NCCL_NET_GDR_LEVEL`** | **`PHB`** | GPUDirect RDMA at PCIe-host-bridge permissive floor (GB10 `nvidia-smi topo -m`). |
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

### vLLM Ray + NCCL env carry-over (failure mode fixed 2026-05-19)

vLLM 0.17.x logs (Ray backend) explicitly **copy prefixed env vars onto workers**:

> `Env var prefixes to copy: ['HF_', ..., 'NCCL_', 'UCX_', 'VLLM_']`

That includes **`NCCL_IB_GID_INDEX`**, overwriting the follower container's
`--env-file` with the leader's index. Mitigation wired in Ansible:
`/etc/vllm-ngc-stacked/ray_non_carry_over_env_vars.json` mounted read-only at
`/root/.config/vllm/ray_non_carry_over_env_vars.json` listing `NCCL_IB_GID_INDEX`,
per upstream hint in logs. **`docker run` must include the bind-mount** —
re-provision **with recreate** after pulling the role change.

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
rocep1s0f0   → enp1s0f0np0     ACTIVE LINK_UP  ← Cage A, fabric 1 (link-local QSFP transport)
rocep1s0f1   → enp1s0f1np1     DOWN           ← Cage B (intentionally uncabled HA layout)
roceP2p1s0f0 → enP2p1s0f0np0   ACTIVE LINK_UP  ← same **Cage A** wire, second PCIe path (fabric 2 /30 IPs)
roceP2p1s0f1 → enP2p1s0f1np1   DOWN
```

`link_layer: Ethernet` → RoCE v2 (not native IB). Jumbo framing + pinned GID indices
were qualified in **`runs/2026-04-19-dual-rail-cutover.md`** (may differ slightly on disk name).
If this table disagrees with `rdma link show` on-metal, **`rdma link show` wins**.

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
