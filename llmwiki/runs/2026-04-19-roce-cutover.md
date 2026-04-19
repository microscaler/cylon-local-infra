---
title: 2026-04-19 — RoCE v2 + GPUDirect RDMA cutover
kind: run
date: 2026-04-19
status: shipped
tags: [nccl, rdma, roce, spark, gb10, connectx-7, qsfp, perf]
related:
  - concepts/nccl-on-spark.md
  - concepts/spark-interconnect.md
  - runs/2026-04-19-qwen3-throughput-and-256k.md
  - concepts/ncclcommInitRank-abort-tp2.md
---

# RoCE v2 + GPUDirect RDMA cutover

## TL;DR

Flipped the NCCL data plane from TCP sockets (over the QSFP link-local IP) to
RoCE v2 with GPUDirect RDMA. **Peak aggregate decode throughput 164 → 638
tok/s (3.9×)**. **Single-stream decode 35 → 47.6 tok/s (+36%)**. **40k-token
prefill ~22 s → 5.78 s (3.8×)**. No application-level knobs changed — this is
all from letting Blackwell talk to ConnectX-7 directly over verbs instead of
pushing every all-reduce through the kernel socket buffer.

## Why this was still on the floor

The [2026-04-18 TP=2 bring-up](./2026-04-18-tp2-nccl-solved.md) landed the
stack on TCP sockets because the external NCCL net plugins in the NGC image
(`aws-ofi-nccl`, `hpcx rdma-sharp`, `spectrum-x`, `ibext`) were aborting
`ncclCommInitRank` before NCCL's own logger started — see
[`ncclcommInitRank-abort-tp2`](../concepts/ncclcommInitRank-abort-tp2.md). The
workaround was `NCCL_NET_PLUGIN=none` + `NCCL_IB_DISABLE=1`: plugins off, IB
verbs off, fall back to sockets.

That worked — but it conflated two independent things. `NCCL_NET_PLUGIN` only
disables *external* NCCL net plugins. NCCL's *internal* verbs transport (the
`NET/IB` code path statically linked into libnccl) is controlled by
`NCCL_IB_DISABLE` and was never the crasher.

## Fabric survey

```
4× Mellanox ConnectX-7 per Spark (MT2910 / PCI 15b3:1021, FW 28.45.4028)
├── rocep1s0f0/1  → enp1s0f0np0 (169.254.x/16)  ACTIVE LINK_UP  200 Gb/s  ← cabled
├── rocep1s0f1/1  → enp1s0f1np1                 DOWN
├── roceP2p1s0f0/1 → enP2p1s0f0np0              ACTIVE LINK_UP  200 Gb/s  ← uncabled
└── roceP2p1s0f1/1 → enP2p1s0f1np1              DOWN

libibverbs, librdmacm, ibverbs-utils, nvidia-mlnx-tools all installed.
```

`rdma link show`, `ibv_devinfo` on both nodes report `PORT_ACTIVE`,
`link_layer: Ethernet` (so this is RoCE v2, not native InfiniBand). Fabric
userspace was already in place; we just had to use it.

## Measurements that justified the cutover

### iperf over the QSFP IP (TCP)

```
single stream          24.1 Gb/s
  8 streams (sat)     107   Gb/s   <-- ceiling, CPU-bound at MTU 1500
 16 streams           107   Gb/s
```

### Real inference traffic (TCP path)

A 3001-token prefill pushed **2.08 GB** of cross-node bytes in 0.85 s — **~20
Gb/s, exactly the single-stream TCP ceiling.** NCCL's ring all-reduce at 2
ranks effectively runs one stream per direction, so the socket path topped out
single-stream.

### NCCL `all_reduce_perf` bench (2 ranks, 1 GPU per rank, BF16, PyTorch)

| msg size | sockets | RoCE + GDR | speedup |
|---:|---:|---:|---:|
|    1 MB | 0.59 GB/s | **7.14 GB/s** | **12.1×** |
|    4 MB | 1.83 | 12.70 | 6.9× |
|   16 MB | 2.01 | 13.22 | 6.6× |
|   64 MB | 2.00 | 13.38 | 6.7× |
|  256 MB | 2.03 | 13.80 | 6.8× |
| 1024 MB | 2.02 | **13.93 GB/s** | **6.9×** |

NCCL log line on the RoCE run:
```
NCCL INFO NET/IB : Using [0]rocep1s0f0:1/RoCE [RO]; OOB enp1s0f0np0:169.254.102.149<0>
NCCL INFO Using network IB
```

`13.93 GB/s = 111 Gb/s`, i.e. **56 % of the 200 Gb/s line rate** on small-rank
NCCL ring. Small-message latency (1 MiB) improved **12×** — this is what
actually helps the dozens of all-reduces per token in MoE attention layers.

## The cutover

Every knob is in the repo now; the cutover is driven by one playbook.

### Inventory — `inventory/group_vars/sparks.yml`

```yaml
nccl_ib_hca: rocep1s0f0          # cabled ConnectX-7 port, same name on both Sparks

vllm_distributed_extra_env:
  NCCL_SOCKET_IFNAME:          "{{ nccl_interface }}"   # still used for bootstrap
  GLOO_SOCKET_IFNAME:          "{{ nccl_interface }}"
  UCX_NET_DEVICES:             "{{ nccl_interface }}"
  OMPI_MCA_btl_tcp_if_include: "{{ nccl_interface }}"
  TP_SOCKET_IFNAME:            "{{ nccl_interface }}"
  NCCL_IB_DISABLE:             "0"                       # flipped — internal verbs ON
  NCCL_IB_HCA:                 "{{ nccl_ib_hca }}"       # pin to the cabled port
  NCCL_NET_GDR_LEVEL:          "PHB"                     # GPU↔NIC direct across PCIe HB
  NCCL_NET_PLUGIN:             "none"                    # KEEP: external plugins still off
  NCCL_P2P_DISABLE:            "1"                       # no NVLink between separate Sparks
  NCCL_CUMEM_ENABLE:           "0"                       # defensive (26.01 era)
  NCCL_SHM_DISABLE:            "1"                       # no shm between nodes
  NCCL_NVLS_ENABLE:            "0"                       # no NVLink Sharp
  NCCL_DEBUG:                  "WARN"
  RAY_memory_monitor_refresh_ms: "0"
  RAY_CGRAPH_get_timeout:      "900"
```

### Role — `roles/vllm_stacked_container`

Both the Ray head and worker `docker run` commands now inject (when
`vllm_stacked_container_rdma_enabled: true`, the new default):

```
--device /dev/infiniband \
--cap-add IPC_LOCK \
--ulimit memlock=-1:-1
```

Without these, NCCL can't open `/dev/infiniband/uverbs*` inside the container,
and `ibv_reg_mr` fails to pin the ring buffers.

### One-shot cutover — `playbooks/cutover_roce.yml`

```
ansible-playbook playbooks/cutover_roce.yml
```

This invokes the role directly instead of going through `spark_provision ->
include_role`, sidestepping the known tag-propagation issue noted in the
Qwen3.6 promotion run. Sets `vllm_stacked_container_recreate: true` so both
containers rebuild with the new env-file and device passthrough.

Play recap: `nvidia1 ok=18 changed=4`, `nvidia2 ok=12 changed=3`, 30 s.

### Container wiring verification

```
$ docker inspect vllm-ngc-ray-head --format '{{.HostConfig...}}'
Devices=/dev/infiniband   CapAdd=[CAP_IPC_LOCK]   Ulimits=memlock=-1

$ grep -E NCCL_IB head.env
NCCL_IB_DISABLE=0
NCCL_IB_HCA=rocep1s0f0
NCCL_NET_GDR_LEVEL=PHB
```

## Post-cutover vLLM workload bench (Qwen3.6-35B-A3B, TP=2, 262144 ctx)

Boot: 155 s total, `init engine` 50.86 s (down from ~75 s on sockets). KV cache
71.42 + 72.23 GiB per rank.

| metric | socket baseline | **RoCE+GDR** | change |
|---|---:|---:|---:|
| single-stream decode | 35 tok/s | **47.6** | **+36 %** |
| batch=4 decode (agg) | — | 134.3 | — |
| batch=8 decode (agg) | — | 181.7 | — |
| batch=16 decode (agg, peak of prior run) | 164 | **265.9** | **+62 %** |
| batch=32 decode (agg) | — | 398.6 | — |
| batch=64 decode (agg) | — | **638.7** | new ceiling |
| 3k prefill (best) | 0.85 s / 3 500 tok/s | **0.44 s / 6 732 tok/s** | **1.9×** |
| 40k prefill (best) | ~22 s / 1 833 tok/s | **5.78 s / 6 919 tok/s** | **3.8×** |

Run-time traffic confirmed on the RoCE HCA counters — NOT the socket interface:

```
3 000-token prefill:
  rocep1s0f0 port_xmit_data delta = 1008 MiB
  rocep1s0f0 port_rcv_data  delta = 1008 MiB
  enp1s0f0np0 tx_bytes      delta =    0 MiB   ← socket path idle for data
```

2 016 MiB total matches the 2.08 GB we measured on the socket path for the
same prompt size — same amount of data, different transport.

## Why decode (memory-bound) ALSO got faster

Naive math said decode is GPU-memory-bandwidth-bound and RDMA shouldn't help —
NCCL all-reduce per token is only ~10 MiB, and the latency delta 0.7 ms →
< 100 µs is "small". But Qwen3.6 runs ~40 all-reduces per decoded token (one
per attention/MoE block). At the old 0.7 ms socket latency that was ~28 ms
purely on collective latency per token — 80 % of the 28 ms/token decode budget
at 35 tok/s. RoCE cuts that to <4 ms, so the same GPU compute lands at
~21 ms/token → ~47 tok/s. The measured 47.6 matches to within rounding.

## Why prefill got MUCH faster

Prefill is network-bound past a few thousand tokens. A 40k-token prefill
moves ~14 GB through the all-reduce ring. At the 2 GB/s socket ceiling that's
~7 s minimum, dominated wall-time. At the 13.93 GB/s RoCE ceiling that's ~1 s,
and the GPU compute finally gets to be the bottleneck.

## Follow-ups (deferred, all +perf, not blockers)

- **MTU 1024 → 4096** on the RoCE port. `ibv_devinfo` reports `max_mtu: 4096`
  but we're sitting at `active_mtu: 1024`. Typical 10–20 % win on large
  messages. Needs `ip link set enp1s0f0np0 mtu 4096` on both sides plus a
  matching tweak at whatever switches the QSFP (currently peer-to-peer, so
  just the two NICs). Do after the first uneventful week on the current
  config.
- **Second-ASIC bond** — `roceP2p1s0f0` is ACTIVE LINK_UP on both Sparks but
  uncabled (no QSFP going between the second ports). A direct-attach QSFP-DD
  between the Sparks on that pair would double the aggregate fabric; NCCL
  supports multi-HCA via `NCCL_IB_HCA=rocep1s0f0,roceP2p1s0f0`. Waiting on
  a second QSFP-DD cable to try.
- **Re-qualify external OFI plugin on 26.03-py3.** The 26.01-era abort was
  inside `aws-ofi-nccl`; 26.03 ships `AWS_OFI_NCCL_VERSION=1.17.3`. With the
  internal path proven, we can re-test the plugin path *independently* and
  see whether it bypasses the PCIe host-bridge hop for an extra few %.
- **Ansible tag propagation in `spark_provision`** still wraps
  `vllm_stacked_container` in `include_role`, so `--tags vllm_ngc_stack`
  hits only the outer include. `playbooks/cutover_roce.yml` dodges this,
  but the proper fix is `apply: { tags: [vllm_ngc_stack] }` on the include.

## Rollback

Edit `inventory/group_vars/sparks.yml`:
```yaml
NCCL_IB_DISABLE: "1"
NCCL_NET_GDR_LEVEL: "0"
vllm_stacked_container_rdma_enabled: false   # under role defaults
```
Then `ansible-playbook playbooks/cutover_roce.yml`. Two-minute revert.

## Cross-refs

- [`concepts/nccl-on-spark.md`](../concepts/nccl-on-spark.md) — updated to the
  RoCE+GDR config as the new canonical runtime env.
- [`concepts/ncclcommInitRank-abort-tp2.md`](../concepts/ncclcommInitRank-abort-tp2.md)
  — the 26.01 external-plugin abort that started us on sockets.
- [`runs/2026-04-19-qwen3-throughput-and-256k.md`](./2026-04-19-qwen3-throughput-and-256k.md)
  — the socket-path baseline numbers this run is compared against.
