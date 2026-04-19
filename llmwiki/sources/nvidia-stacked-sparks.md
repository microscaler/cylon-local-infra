---
title: NVIDIA — vLLM on stacked Sparks
kind: source
status: active
tags: [vllm, spark, ngc, ray, nccl]
updated: 2026-04-18
url: https://build.nvidia.com/spark/vllm/stacked-sparks
---

# NVIDIA — vLLM on stacked Sparks

Canonical NVIDIA walkthrough for running vLLM with tensor-parallel=2 across two DGX
Sparks using the NGC `nvcr.io/nvidia/vllm` image and host-network Docker Ray.

## Flow (as implemented in `roles/vllm_stacked_container/`)

1. QSFP interconnect up, link-local IPv4 on `enp1s0f0np0` on both Sparks
   ([concepts/spark-interconnect.md](../concepts/spark-interconnect.md)).
2. Pull NGC vLLM image on both Sparks: `docker pull nvcr.io/nvidia/vllm:<tag>`.
3. Start Ray **head** container on the leader with `--network host --gpus all`,
   env file with `VLLM_HOST_IP=<leader interconnect IP>` + NCCL/socket vars,
   command `ray start --block --head --node-ip-address=<leader ip> --port=6379`.
4. Start Ray **worker** container on each follower with
   `ray start --block --address=<leader ip>:6379 --node-ip-address=<this host ip>`.
5. `docker exec -d` into the head: `vllm serve <model> --tensor-parallel-size 2
   --distributed-executor-backend ray --host 0.0.0.0 --port 8000`.

No systemd units for Ray or vLLM in this mode; Docker `--restart unless-stopped` owns
lifecycle. Stop any bare-metal `ray-head`/`ray-worker`/`vllm-stacked`/`vllm` units first
(the role does this when `vllm_stacked_container_stop_bare_metal_systemd: true`).

## Image tags we've used

- `nvcr.io/nvidia/vllm:25.11-py3` — role default (2025-11 image).
- `nvcr.io/nvidia/vllm:26.01-py3` — currently pulled on both Sparks
  ([runs/2026-04-18-state-of-cluster.md](../runs/2026-04-18-state-of-cluster.md)).
  Update `vllm_stacked_container_image` if you want Ansible to pin this.

## Env vars carried in via `env-file`

From `templates/ngc-ray-head.env.j2` / `templates/ngc-ray-worker.env.j2`, merging
`vllm_distributed_extra_env` from `inventory/group_vars/sparks.yml`:

- `VLLM_HOST_IP` — this host's interconnect IP (always).
- `MASTER_ADDR` — leader's interconnect IP on both head and worker.
- `NCCL_SOCKET_IFNAME=enp1s0f0np0`, `GLOO_SOCKET_IFNAME`, `UCX_NET_DEVICES`,
  `OMPI_MCA_btl_tcp_if_include`, `TP_SOCKET_IFNAME` — force socket networking over QSFP.
- `NCCL_IB_DISABLE=1`, `NCCL_NET_GDR_LEVEL=0`, `NCCL_NVLS_ENABLE=0` — GB10+ConnectX RoCE
  verbs path fails with ENOMEM for this vLLM TP path; socket works.
- `RAY_CGRAPH_get_timeout=900` — see
  [concepts/ray-cgraph-timeout.md](../concepts/ray-cgraph-timeout.md).
- `RAY_memory_monitor_refresh_ms=0` — disable Ray's memory killer during load.

## Open questions (tracked for lint)

- Is `VLLM_USE_V1` implicit in 26.01-py3? Role doesn't set it.
- Does `vllm serve` in 26.01 need `--load-format fastsafetensors` for Spark-native loads?
  Bare-metal stack uses it; container stack currently passes it through only if
  `vllm_load_format` is set — verify works inside the NGC image.
