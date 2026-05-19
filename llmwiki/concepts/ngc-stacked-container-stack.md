---
title: NGC stacked-container stack
kind: concept
status: active
tags: [ngc, docker, vllm, ray, recommended]
updated: 2026-05-19
related:
  - concepts/bare-metal-venv-stack.md
  - concepts/restart-unless-stopped-after-manual-stop.md
  - entities/ngc-vllm-image.md
  - sources/nvidia-stacked-sparks.md
---

# NGC stacked-container stack

Docker-based TP=2 vLLM using the NGC image and host-network Ray, no systemd for the
stack itself. Implemented by `roles/vllm_stacked_container/`.

## Why we prefer this (2026-04-18)

The bare-metal venv path keeps breaking on pip resolution
([transformers-huggingface-hub-mismatch](./transformers-huggingface-hub-mismatch.md),
uvloop, fastsafetensors, …). The NGC image ships a tested, coherent set of wheels per
tag. One knob (`vllm_stacked_container_image`) moves the entire stack.

## Enable

`inventory/group_vars/sparks.yml`:

```yaml
spark_provision_vllm_stack: false              # disable bare-metal stack
spark_provision_vllm_stacked_container: true   # enable NGC container stack
# Optional: skip the venv install entirely
# spark_provision_vllm: false
# Firewall — NGC binds 8000 by default
firewall_allow_tcp_ports: [22, "{{ vllm_ray_port }}", "{{ vllm_api_port }}", 8000]
firewall_trusted_lan_tcp_ports: [22, "{{ vllm_api_port }}", "{{ vllm_ray_port }}", 8000]
```

Run: `ansible-playbook playbooks/provision_sparks.yml --tags vllm_ngc_stack`.

## What the role does

1. Asserts `spark_provision_vllm_stack: false` (no dual-ownership of Ray).
2. Stops + disables `vllm-stacked`, `ray-head`, `ray-worker`, `vllm` systemd units.
3. Discovers each host's IPv4 on `nccl_interface` → `spark_interconnect_ip` fact.
4. Pulls `nvcr.io/nvidia/vllm:<tag>` when `vllm_stacked_container_pull_image: true`.
5. Renders `head.env` (leader) and `worker-<host>.env` (follower) from
   `vllm_distributed_extra_env`, then **appends** `NCCL_IB_GID_INDEX` **per host**
   from `inventory/host_vars/nvidia*.yml` (`spark_nccl_ib_gid_index`, default `"3"`).
   **Important (2026-05-19):** NGC/vLLM’s Ray executor **copies `NCCL_*`** from the
   driver into remote workers unless listed in
   `/root/.config/vllm/ray_non_carry_over_env_vars.json`. The Ansible role bind-mounts
   that JSON from `/etc/vllm-ngc-stacked/` (see `roles/vllm_stacked_container/`) so
   followers **keep** follower-specific `NCCL_IB_GID_INDEX` — symmetric GID pinning
   in `sparks.yml` alone is insufficient on asymmetric `show_gids` tables. **Recreate**
   containers to pick up new volume mounts (`just spark-vllm-provision-recreate`).
6. `docker run -d --restart unless-stopped --network host --gpus all --shm-size 10.24g`
   with env-file + HF cache mount:
   - Leader: `ray start --block --head --node-ip-address=<leader ip> --port=6379`.
   - Follower: `ray start --block --address=<leader ip>:6379 --node-ip-address=<this ip>`.
7. Waits for Ray GCS on leader (`127.0.0.1:6379`), pauses for worker join.
8. `docker exec -d vllm-ngc-ray-head bash -lc 'vllm serve <model> --tp 2 --host 0.0.0.0
   --port 8000 --distributed-executor-backend ray ...'`.

## Operating

Operator surface lives in the top-level `justfile` as `spark-vllm-*`
recipes (added 2026-04-27 in
[runs/2026-04-27-ray-head-exited-postmortem.md](../runs/2026-04-27-ray-head-exited-postmortem.md)):

| Recipe | What |
|---|---|
| `spark-vllm-ps` | head + worker `docker ps -a` on both Sparks. |
| `spark-vllm-start` / `spark-vllm-stop` | `docker start` / `docker stop` head + worker. |
| `spark-vllm-head-start` / `-restart` | head only. |
| `spark-vllm-worker-start` / `-restart` | worker only. |
| `spark-vllm-restart` | head + worker `docker restart`. **Does not** re-launch the detached `vllm serve` — follow with `spark-vllm-provision`. |
| `spark-vllm-api-kill` / `-api-restart` | kill the in-container `vllm serve` (Ray stays up; weights + torch.compile cache stay warm). |
| `spark-vllm-dashboard` | open the LAN Ray dashboard URL. |
| `spark-vllm-provision` / `-recreate` | full role apply / forced recreate. |

- Logs: `just spark-vllm-logs` (Ray head bootstrap) /
  `just spark-vllm-logs-serve` (`vllm serve` stdout inside the head).
- Recreate (image / env / `ray start` flag change): set
  `vllm_stacked_container_recreate: true` (or use
  `just spark-vllm-provision-recreate`).
- **Manual stop semantics**: `--restart unless-stopped` does **not**
  relaunch a manually-stopped container across host reboots. After
  `just spark-vllm-stop` the cluster stays stopped until
  `just spark-vllm-start` (or any `docker start`). Role start path
  reuses an Exited container by name via `docker start` (head and
  worker both, since 2026-04-27). See
  [concepts/restart-unless-stopped-after-manual-stop.md](./restart-unless-stopped-after-manual-stop.md).

## Ray dashboard exposure

Default since 2026-04-27: head's `ray start --head` adds
`--dashboard-host=0.0.0.0 --dashboard-port=8265`, and 8265 is allow-listed
in `firewall_trusted_lan_tcp_ports` (LAN-only — same posture as `:8000`,
not in the global `firewall_allow_tcp_ports`). Reachable at
`http://<leader-lan-ip>:8265/` from any 192.168.1.0/24 host. Defaults
live in `roles/vllm_stacked_container/defaults/main.yml`
(`vllm_stacked_container_dashboard_host`,
`vllm_stacked_container_dashboard_port`); operator hook is
`just spark-vllm-dashboard`. **Recreate** the head when changing these
flags — `ray start` argv is baked into the running container's command.

## Image tag discipline

- Role default: `nvcr.io/nvidia/vllm:25.11-py3`.
- **Currently on hosts**: `nvcr.io/nvidia/vllm:26.03-py3` (pulled +
  validated 2026-04-19; promoted in inventory at the same time and
  managed by `ngc-image-sync.service` thereafter — see
  [runs/2026-04-19-26.03-py3-upgrade.md](../runs/2026-04-19-26.03-py3-upgrade.md)
  and `entities/ngc-image-sync-service.md`).

## Open questions

- Can we drop `--distributed-executor-backend ray` in newer vLLM? Probably
  not for TP=2 across nodes.
