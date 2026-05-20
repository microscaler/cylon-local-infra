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

## Fleet drift correction (same evening)

After PD/USB‑C firmware + cold boots, **`show_gids` on `gx10-47b5` (`nvidia2`)
again places IPv4 RoCE **v2** on **`rocep1s0f0` at index `3`** (same pattern as `nvidia1`).
Keeping **`spark_nccl_ib_gid_index: "4"`** there meant **`NCCL_IB_GID_INDEX=4`** pointed past the IPv4 row → **`ibv_modify_qp` EINVAL**, **`remote GID ::`**. Inventory corrected to **`"3"`** for **`nvidia2`**. **Ray env carry‑over exclusion remains mandatory** whenever indices differ across hosts — verify after every FW/driver churn.

## Symptom

Stacked TP=2 on NGC/`--distributed-executor-backend ray`:

- `PyNcclCommunicator` / `RuntimeError: NCCL error: unhandled system error`
  (`ncclCommInitRank`) during `initialize_model_parallel` on the **remote**
  worker (`RayWorkerWrapper`, `ip=169.254.37.109`).

## Root causes

1. **Asymmetric RoCE GID tables** — `show_gids` **can** differ by GX10 serial / FW / driver epoch:
   Earlier May **2026** we observed **`gx10-e1ce` (`nvidia1`) IPv4 RoCE v2 at index `3`** on both rails while **`gx10-47b5` (`nvidia2`) used index `4`** (IPv4 row absent at `3`). Later **the same evening**, **`nvidia2`'s table matched `nvidia1` at index `3`** — see **Fleet drift correction** above. Inventory **cannot** assume symmetry — pin **`spark_nccl_ib_gid_index` from live `show_gids`**.

2. **vLLM Ray executor env propagation** — vLLM copies `NCCL_*` prefixes from the
   driver (`EngineCore`/APIServer env) onto Ray workers (`ray_env.py` log line lists
   `NCCL_IB_GID_INDEX` explicitly). Result: follower workers **overwrite** follower
   `spark_nccl_ib_gid_index` with the leader’s **`3`** even when `worker.env` says **`4`**.
   Fixing (1) in Ansible templates alone **does not work** until (2) is blocked.

## Fix (infra)

Per **AGENTS workflow + operator request to sync wiki with code**:

| Layer | Mechanism |
|------|-----------|
| Inventory | Remove `NCCL_IB_GID_INDEX` from dict `vllm_distributed_extra_env`; emit per-host last line via `templates/ngc-ray-{head,worker}.env.j2`; `inventory/host_vars/nvidia{1,2}.yml` set **`spark_nccl_ib_gid_index`** from **`show_gids`** (pair saw **`"3"`/`"3"`** after May **2026** maintenance — historically **`"3"`/`"4"`**). |
| vLLM exclusion | **`roles/vllm_stacked_container/files/ray_non_carry_over_env_vars.json`** deployed to **`/etc/vllm-ngc-stacked/`** and bind-mounted to **`/root/.config/vllm/ray_non_carry_over_env_vars.json`** (`["NCCL_IB_GID_INDEX"]`) on **both** head + worker **`docker run`**. Controlled by **`vllm_stacked_container_ray_nccl_gid_carryover_exclude`** default **true**. |
| Adoption | Mandatory **`just spark-vllm-provision-recreate`** (new volume mount → cannot hot-patch into existing containers cleanly). |

## Verification checklist

```bash
# On each Spark
show_gids | sed -n '1,14p'

# follower container retains inventory `spark_nccl_ib_gid_index` regardless of driver's copy attempts
docker exec vllm-ngc-ray-worker-nvidia2 bash -lc 'echo $NCCL_IB_GID_INDEX; cat /root/.config/vllm/ray_non_carry_over_env_vars.json'
docker exec vllm-ngc-ray-head bash -lc 'cat /root/.config/vllm/ray_non_carry_over_env_vars.json'
```
