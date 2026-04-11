# spark-vllm-docker practices vs Ansible/systemd

We vendor upstream scripts under **`contrib/spark-vllm-docker/`** (MIT, [eugr/spark-vllm-docker](https://github.com/eugr/spark-vllm-docker)).

## Ansible-driven Docker stack (recommended when using containers)

Enable **`spark_provision_docker_vllm: true`** in **`inventory/group_vars/sparks.yml`**, then run **`playbooks/provision_sparks.yml`** (role **`vllm_docker_stack`**). This:

1. Pushes vendored **`launch-cluster.sh`** / **`autodiscover.sh`** / **`hf-download.sh`** to **`/opt/spark-vllm-docker`** on the **leader**.
2. Templates **`/etc/spark-vllm-docker/cluster.env`** (`CLUSTER_NODES`, `ETH_IF`, `IB_IF`, `MASTER_PORT`, …) from inventory / `nccl_interface`.
3. Installs **`/usr/local/sbin/vllm-docker-launch.sh`** (wraps `launch-cluster.sh exec vllm serve …` with your model flags).
4. Enables **`vllm-docker-cluster.service`** — **systemd** starts/stops the same orchestration you would run by hand.

Bare-metal Ray + **`vllm-stacked`** remains available via **`provision_sparks.yml`** (`spark_provision_vllm_stack`); the Docker phase **stops** those units by default to avoid conflicts.

See **`roles/vllm_docker_stack/README.md`** for variables and firewall notes (default API port **8000**).

---

Use the vendored scripts **manually** only when you want ad-hoc debugging. For normal operation, prefer **Ansible + systemd** as above.

## What they do well (extract as “best practices”)

| Practice | In their scripts | Ansible / systemd equivalent |
|----------|------------------|------------------------------|
| **Model prefetch** | `hf-download.sh` → `uvx hf download`, then **rsync** hub dir to peers (`COPY_HOSTS`, parallel copy) | `spark_provision_hf: true`, `hf_prefetch_models` in `sparks.yml`, `ansible-playbook playbooks/provision_sparks.yml --tags hf` |
| **Cluster layout** | `.env`: `CLUSTER_NODES` (head first), `COPY_HOSTS`, `ETH_IF`, `IB_IF`, `MASTER_PORT` | Inventory `groups['sparks']`, `nccl_interface`, `vllm_ray_port` |
| **Per-node IPs** | `VLLM_HOST_IP`, `RAY_NODE_IP_ADDRESS` = **ConnectX / coordination IP** on **each** node | `spark_interconnect_ip` in stack play, `VLLM_HOST_IP` in units and wrappers |
| **NCCL** | `NCCL_SOCKET_IFNAME`, `NCCL_IB_HCA` (both RoCE twins), `NCCL_IB_DISABLE=0` | `vllm_distributed_extra_env` in `sparks.yml`; add **`NCCL_IB_HCA`** from `ibdev2netdev` if needed |
| **Ray** | Head `--port` = `MASTER_PORT` (default **29501** in their script), object store capped | Our Ray uses **`vllm_ray_port`** (default **6379**) — keep **one** port everywhere (firewall + workers) |
| **Container runtime** | `--network host`, `--ipc=host` or `--privileged`, GPU, cache bind-mounts | No Docker: venv + **same env exports** in `run-ray-*.sh` / `run-api-server-stacked.sh` |
| **Weight load** | `--load-format fastsafetensors` + optional image patches | `vllm_load_format`, `fastsafetensors` pip in `sparks.yml` |
| **Parallelism** | Parse `-tp`/`-pp`/`-dp`, trim excess nodes | `vllm_tensor_parallel_size`, inventory host order (leader first) |
| **Engine patience** | (Set in container env in your workflow) | `VLLM_ENGINE_READY_TIMEOUT_S` / `vllm_engine_ready_timeout_sec` |
| **Graceful stop** | Docker stop vs huge processes | `vllm_stacked_systemd_stop_timeout_sec` |

## MiniMax example → bare-metal flags

Their `vllm serve` example (port **8000**) maps to our **`vllm_api_server_extra_args`** + **`vllm_default_model`** (conceptually):

```text
--gpu-memory-utilization 0.7
--max-model-len 128000
--load-format fastsafetensors
--enable-auto-tool-choice --tool-call-parser minimax_m2
--reasoning-parser minimax_m2_append_think
```

vLLM’s CLI uses **underscore** flags in some versions; mirror **exactly** what `vllm serve --help` prints on your venv.

## systemd-oriented workflow

1. **Prefetch** — Either run vendored `hf-download.sh` from `contrib/spark-vllm-docker/` **or** Ansible prefetch — same HF cache layout under `HF_HOME`.
2. **Align env** — Ensure `vllm_distributed_extra_env` matches what you observe working in Docker (`launch-cluster.sh` → `get_env_flags` in upstream).
3. **One command path** — Prefer a **single ExecStart** wrapper (we already use `run-api-server-stacked.sh`) so restarts are predictable; their equivalent is `docker exec` / `launch-cluster.sh exec`.
4. **Pivot** — If bare-metal stalls, run the vendored **`launch-cluster.sh`** against their **`vllm-node`** image (build from their repo) while keeping firewall and DNS documented in **`docs/vllm-timebox-and-pivot.md`**.
