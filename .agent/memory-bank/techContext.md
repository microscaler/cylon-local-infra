# Technical Context

**Stack:**

- **Ansible** 2.14+ (repo assumes `ansible.cfg` at repo root).
- **OS target (Sparks):** Ubuntu on aarch64 DGX Spark; CUDA via NVIDIA repo (`cuda` role), extra `cuda-cudart-12-8` when toolkit is 13-only for vLLM libcudart `.12` compatibility (see `sparks.yml`).
- **Python:** `vllm_venv_path` default `/opt/vllm/venv`, often owned by `**vllm_run_user`** (`nvidia`); `python3-dev` for Triton.
- **PyTorch / vLLM:** `vllm_torch_extra_index_url` (e.g. cu130) + `vllm_package` pin; Ray installed via `vllm_ray_package`.

**Key variables (sparks):**

- `vllm_api_port` (8080), `vllm_ray_port` (6379), `vllm_tensor_parallel_size` (2 for stacked), `vllm_stack_vllm_start_delay_sec`, `vllm_stack_stop_single_node_service`.
- `spark_runtime_user` / `vllm_hf_home` for caches.

**Systemd units (stacked):**

- `ray-head.service` — leader, interconnect `node-ip-address`, `--port` = `vllm_ray_port`.
- `ray-worker.service` — followers, `ray start --address={{ spark_ray_head_socket }} --block`.
- `vllm-stacked.service` — leader only, `After=` / `Requires=ray-head.service`, startup delay in `run-api-server-stacked.sh` (not `ExecStartPre`), `VLLM_HOST_IP` = interconnect IP.

**Operational commands (leader):**

- `journalctl -u vllm-stacked -u ray-head -f`
- `RAY_ADDRESS=<leader_ic>:6379 /opt/vllm/venv/bin/ray status`

**Repo path on disk:** `/Users/casibbald/Workspace/microscaler/cylon-local-infra` (adjust if workspace moves).