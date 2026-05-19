---
title: NCCL_IB_GID_INDEX asymmetry + vLLM Ray NCCL env carry-over
kind: run
status: shipped
updated: 2026-05-19
hosts: [nvidia1, nvidia2]
tags: [nccl, vllm, ray, roce]
related:
  - ../concepts/nccl-on-spark.md
  - ../concepts/ngc-stacked-container-stack.md
---

# 2026-05-19 — NCCL_IB_GID_INDEX + Ray carry-over regression

## Symptom

Stacked TP=2 on NGC/`--distributed-executor-backend ray`:

- `PyNcclCommunicator` / `RuntimeError: NCCL error: unhandled system error`
  (`ncclCommInitRank`) during `initialize_model_parallel` on the **remote**
  worker (`RayWorkerWrapper`, `ip=169.254.37.109`).

## Root causes

1. **Asymmetric RoCE GID tables** — `show_gids` differs by GX10 serial:
   `gx10-e1ce` (`nvidia1`) IPv4 RoCE v2 lands at **index `3`** on both
   `rocep1s0f0` and `roceP2p1s0f0`; `gx10-47b5` (`nvidia2`) uses **index `4`**
   (no row at `3` on either rail). Inventory cannot pin **one global**
   `NCCL_IB_GID_INDEX` for both nodes without breaking one side.

2. **vLLM Ray executor env propagation** — vLLM copies `NCCL_*` prefixes from the
   driver (`EngineCore`/APIServer env) onto Ray workers (`ray_env.py` log line lists
   `NCCL_IB_GID_INDEX` explicitly). Result: follower workers **overwrite** follower
   `spark_nccl_ib_gid_index` with the leader’s **`3`** even when `worker.env` says **`4`**.
   Fixing (1) in Ansible templates alone **does not work** until (2) is blocked.

## Fix (infra)

Per **AGENTS workflow + operator request to sync wiki with code**:

| Layer | Mechanism |
|------|-----------|
| Inventory | Remove `NCCL_IB_GID_INDEX` from dict `vllm_distributed_extra_env`; emit per-host last line via `templates/ngc-ray-{head,worker}.env.j2`; `inventory/host_vars/nvidia{1,2}.yml` set **`spark_nccl_ib_gid_index: "3"|"4"`**. |
| vLLM exclusion | **`roles/vllm_stacked_container/files/ray_non_carry_over_env_vars.json`** deployed to **`/etc/vllm-ngc-stacked/`** and bind-mounted to **`/root/.config/vllm/ray_non_carry_over_env_vars.json`** (`["NCCL_IB_GID_INDEX"]`) on **both** head + worker **`docker run`**. Controlled by **`vllm_stacked_container_ray_nccl_gid_carryover_exclude`** default **true**. |
| Adoption | Mandatory **`just spark-vllm-provision-recreate`** (new volume mount → cannot hot-patch into existing containers cleanly). |

## Verification checklist

```bash
# On each Spark
show_gids | sed -n '1,14p'

# follower container retains 4 regardless of driver's copy attempts
docker exec vllm-ngc-ray-worker-nvidia2 bash -lc 'echo $NCCL_IB_GID_INDEX; cat /root/.config/vllm/ray_non_carry_over_env_vars.json'
docker exec vllm-ngc-ray-head bash -lc 'cat /root/.config/vllm/ray_non_carry_over_env_vars.json'
```
