# Spark provisioning (`provision_sparks.yml`)

Single entry point for **DGX Spark** hosts in inventory group **`sparks`**:

```bash
ansible-playbook playbooks/provision_sparks.yml
```

## What it does

The **`spark_provision`** role runs phases in order (each can be skipped with variables in `inventory/group_vars/sparks.yml` — see `roles/spark_provision/defaults/main.yml`):

| Phase | Variable (default) | Role / notes |
|-------|---------------------|--------------|
| Sudoers / users | `spark_provision_sudoers` (true) | `sudoers` |
| CUDA apt cleanup | `spark_provision_cuda_apt_cleanup` (true) | `cuda` `apt_cleanup` |
| APT upgrade | `spark_provision_apt` (true) | `spark_apt` |
| Docker | `spark_provision_docker` (true) | `docker` |
| Firewall | `spark_provision_firewall` (true) | `firewall` |
| CUDA toolkit | `spark_provision_cuda_toolkit` (true) | `cuda` |
| vLLM venv | `spark_provision_vllm` (true) | `vllm` |
| Single-node `vllm.service` | `spark_provision_vllm_serve` (true) | `vllm` `serve.yml` — skipped when `vllm_sparks_deploy_single_node_service: false` |
| API smoke test | `spark_provision_vllm_test` (true) | skipped when single-node service off |
| Stacked Ray + `vllm-stacked` | `spark_provision_vllm_stack` (default **false** in role; often **true** in `sparks.yml`) | `vllm` `provision_stack.yml` — **before** templates/units, runs **`ray_reset_state.yml`** when `vllm_ray_reset_state_before_stack` (**true**): stop `vllm-stacked` / `ray-worker` / `ray-head`, `ray stop -f`, optional `rm -rf /tmp/ray/session_*` (reduces stale **`node_*`** rows in `ray status`) |
| Leader endpoint hint | (with stack) | `vllm` `report_leader_endpoint.yml` |
| HF CLI + prefetch | `spark_provision_hf` (false) | `hf_spark` |
| Docker vLLM (spark-vllm-docker) | `spark_provision_docker_vllm` (false) | `vllm_docker_stack` |
| `ray status` check | `spark_provision_verify_ray` (false) | `vllm` `verify_ray.yml` |
| Docker / HF probes | `spark_provision_diagnostics` (false) | `spark_diagnostics` |

## Tags

Use **`--tags`** / **`--skip-tags`** on phases that expose tags (see `roles/spark_provision/tasks/main.yml`), e.g. `--skip-tags vllm_test`.

## NCCL (separate playbook)

NCCL build, Spark↔Spark SSH, and optional **`all_gather_perf`** remain in **`playbooks/nccl_sparks.yml`** (multi-play). Run after base provisioning if you need NCCL trees or interconnect tests:

```bash
ansible-playbook playbooks/nccl_sparks.yml
ansible-playbook playbooks/nccl_sparks.yml --skip-tags nccl_test
```

## Non-Spark hosts

**`dev_hosts`** (e.g. ms02): **`playbooks/dev_hosts.yml`**, **`playbooks/docker_dev_engine.yml`** — unchanged.
