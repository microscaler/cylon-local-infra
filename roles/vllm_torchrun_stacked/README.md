# vllm_torchrun_stacked — NGC multi-node TP via torchrun (no Ray)

Sibling to `roles/vllm_stacked_container/`. Both Sparks run an **identical**
container whose Cmd is `torchrun … -m vllm.entrypoints.openai.api_server …
--distributed-executor-backend external_launcher`. NCCL is the data plane; Gloo
handles control. Only **rank 0** (leader) serves HTTP on `:8000`.

## When to use

- **A/B testing** against the Ray stack before a 26.04+ cutover.
- **26.04-py3+** images where NVIDIA removed the bundled `ray` CLI (see
  `llmwiki/runs/2026-04-29-cluster-recovery-and-26.04-rollback.md`).

Production default remains **`vllm_stack_kind: ray`** on 26.03-py3 until
torchrun is validated on hardware.

## Enable

```yaml
# inventory/group_vars/sparks.yml
vllm_stack_kind: torchrun   # ray | torchrun
```

```bash
just spark-vllm-torchrun-provision
# or recreate:
just spark-vllm-torchrun-provision-recreate
```

## Key variables

| Var | Default | Notes |
|---|---|---|
| `vllm_torchrun_stacked_image` | same as `vllm_stacked_container_image` | Pin in `sparks.yml`. |
| `vllm_torchrun_stacked_rdzv_port` | `29500` | Open on interconnect (ufw trusts `169.254.0.0/16`). |
| `vllm_torchrun_stacked_v1_multiprocessing_disabled` | `true` | Sets `VLLM_ENABLE_V1_MULTIPROCESSING=0` in env. |
| `vllm_torchrun_stacked_stop_ray_containers` | `true` | `docker rm -f` Ray head/worker before torchrun start. |
| `vllm_torchrun_stacked_triton_cache_invalidate` | `true` | Wipes `~/.triton/cache` at container start (GB10 #41871). |

Shared model/TP/NCCL vars: `vllm_default_model`, `vllm_tensor_parallel_size`,
`vllm_api_server_extra_args`, `vllm_distributed_extra_env`, `spark_nccl_ib_gid_index`.

## Operating

```bash
just spark-vllm-torchrun-ps
just spark-vllm-torchrun-status
ssh casibbald@nvidia1 'docker exec vllm-ngc-torchrun-nvidia1 tail -f /root/vllm-torchrun.log'
```

## See also

- `llmwiki/runs/2026-04-29-cluster-recovery-and-26.04-rollback.md`
- `roles/vllm_stacked_container/README.md` (Ray stack)
