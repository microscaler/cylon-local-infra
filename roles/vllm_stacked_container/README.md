# vllm_stacked_container — NVIDIA stacked Sparks (container, NGC image)

The **only supported** vLLM stack on Sparks in this repo. Implements the
[NVIDIA stacked-sparks](https://build.nvidia.com/spark/vllm/stacked-sparks) flow using
the **NGC** image (`nvcr.io/nvidia/vllm`), **Docker** (`docker run` /
`--restart unless-stopped`), and the interconnect + `vllm_distributed_extra_env` from
`inventory/group_vars/sparks.yml`. No systemd units for Ray or vLLM.

## Topology

- **Leader**: Ray head container (`vllm-ngc-ray-head`) + `vllm serve` exec'd inside it.
- **Follower(s)**: Ray worker container (`vllm-ngc-ray-worker-<host>`) that joins the
  leader over the QSFP interconnect.
- Both containers use `--network host --gpus all` and an `env-file` rendered from
  `vllm_distributed_extra_env` plus per-host `VLLM_HOST_IP` / `MASTER_ADDR`.

## Enable

Container-only is the default in `roles/spark_provision/defaults/main.yml`
(`spark_provision_vllm_stacked_container: true`). To run:

```bash
ansible-playbook playbooks/provision_sparks.yml --tags vllm_ngc_stack
```

## Key variables

See `defaults/main.yml`. Most-edited:

| Var | Default | Notes |
|---|---|---|
| `vllm_stacked_container_image` | `nvcr.io/nvidia/vllm:26.01-py3` (set in `sparks.yml`) | Pin per model / platform. |
| `vllm_stacked_container_api_port` | `8000` | Open in `firewall_allow_tcp_ports`. |
| `vllm_stacked_container_dashboard_host` | `0.0.0.0` | Bind addr for the Ray dashboard on the leader. Open `vllm_stacked_container_dashboard_port` (8265) in `firewall_trusted_lan_tcp_ports` (LAN-only by default). |
| `vllm_stacked_container_dashboard_port` | `8265` | Ray dashboard port — `http://<leader-lan-ip>:8265/`. Recreate the head (`-e vllm_stacked_container_recreate=true`) when changing this — `ray start` flags are baked into the running container's command. |
| `vllm_stacked_container_recreate` | `false` | Set `true` when image, env, or any `ray start` flag changes. |
| `vllm_stacked_container_drop_caches_on_provision` | `true` | On **every** provision: `sync` + host `drop_caches` before `vllm serve`. |
| `vllm_stacked_container_triton_cache_invalidate` | `true` | Before `vllm serve`: wipe `~/.triton/cache` inside each Ray container (GB10 sm_121 stale-cache garble — vllm#41871). |
| `vllm_stacked_container_clear_ray_tmp_before_recreate` | `false` | On recreate only: delete `vllm_stacked_container_ray_tmp_dir` (default `/tmp/ray`) before starting fresh Ray containers — clears stale session/socket dirs on the host; unrelated to HF weights cache under `vllm_hf_home`. |
| `spark_nccl_ib_gid_index` (inventory `host_vars`) | unset → `"3"` | Rendered last into `head.env` / `worker-*.env` as `NCCL_IB_GID_INDEX`. **Must match** `show_gids` RoCE v2+IPv4 row on **both** `rocep1s0f0` and `roceP2p1s0f0` for this host — **indices drift across GX10 units / FW / drivers** (never assume follower ≠ leader without checking). **Important:** vLLM’s Ray executor copies `NCCL_*` from the leader into workers unless excluded — mount `ray_non_carry_over_env_vars.json` via `vllm_stacked_container_ray_nccl_gid_carryover_exclude` (default **true**) so followers keep their env-file value. Wrong effective index surfaces as PyNCCL `ncclCommInitRank` / NCCL “unhandled system error” or verbs **`ibv_modify_qp`** failures (**`remote GID ::`**). |
| `vllm_stacked_container_ray_nccl_gid_carryover_exclude` | `true` | Bind-mount `/etc/vllm-ngc-stacked/ray_non_carry_over_env_vars.json` → `/root/.config/vllm/ray_non_carry_over_env_vars.json` (lists `NCCL_IB_GID_INDEX`) so vLLM does **not** overwrite per-host RoCE GIDs on Ray workers. **Requires container recreate** to pick up the mount if the stack predates this knob. |
| `vllm_stacked_container_stop_bare_metal_systemd` | `true` | Stops legacy `vllm-stacked` / `ray-head` / `ray-worker` / `vllm` units (from old bare-metal deployments). |
| `vllm_default_model`, `vllm_tensor_parallel_size`, `vllm_load_format`, `vllm_api_server_extra_args`, `vllm_enforce_eager` | — | Same names as before the bare-metal removal; they now feed the `vllm serve` command inside the head container. |

## Operating

```bash
# Tail API (leader)
ssh casibbald@nvidia1 'docker logs -f vllm-ngc-ray-head'

# Tail Ray worker (follower)
ssh casibbald@nvidia2 'docker logs -f vllm-ngc-ray-worker-nvidia2'

# Smoke test
curl -s http://nvidia1:8000/v1/models | jq
```

## See also

- `llmwiki/concepts/ngc-stacked-container-stack.md`
- `llmwiki/runs/2026-04-18-rip-out-bare-metal.md`
