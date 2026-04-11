# vLLM on Sparks: timeboxed bare-metal runbook and Docker pivot

Use this as a **sprint checklist** for the Ansible-managed stack (`playbooks/provision_sparks.yml`, tags `vllm` / `vllm_stack`). Set an explicit **end date** (for example one week) with the team; if the checklist is not green by then, **pivot** to the community Docker stack without guilt.

## Timebox (fill in)

| Field | Value |
| ----- | ----- |
| Start | |
| End | |
| Owner | |

## Phase A — Bare-metal parity with [spark-vllm-docker](https://github.com/eugr/spark-vllm-docker) (this repo)

1. **Deploy venv + stack** (both Sparks):  
   `ansible-playbook playbooks/provision_sparks.yml`  
   (or `ansible-playbook playbooks/provision_sparks.yml --tags vllm,vllm_stack` if you are re-running phases only)

2. **Learnings already wired in `inventory/group_vars/sparks.yml`**
   - `vllm_load_format: fastsafetensors` + `fastsafetensors` pip — faster weight load on Spark (same idea as their `--load-format fastsafetensors`). If load fails or OOM, set `vllm_load_format: ""` and remove the `fastsafetensors` pip line.
   - `vllm_distributed_extra_env` — `NCCL_SOCKET_IFNAME` aligned with `nccl_interface`, `RAY_memory_monitor_refresh_ms=0`. If collectives misbehave, add **`NCCL_IB_HCA`** with **both RoCE twins** (see their `docs/NETWORKING.md`: e.g. `rocep1s0f1,roceP2p1s0f1` — **verify with `ibdev2netdev` on your hosts**).

3. **Verify Ray before expecting HTTP**
   - Leader: `ray status` → **2 GPUs**, **2 nodes**, no hundreds of phantom `node_*` rows.
   - If unhealthy: full reset (stop `vllm-stacked`, `ray-worker`, `ray-head`; `ray stop -f` on both; restart head → worker → stacked).

4. **Verify API**
   - Leader: `curl -sS http://127.0.0.1:8080/v1/models`
   - Client on LAN: use **leader management IP**, not unresolvable hostnames; match `firewall_trusted_lan_cidr`.

5. **Timeouts** — `vllm_engine_ready_timeout_sec`, `vllm_stacked_systemd_stop_timeout_sec` in `sparks.yml` (large models need long engine-ready and graceful stop).

## Phase B — Pivot: extract / use their implementation

**Vendored scripts (local clone):** `contrib/spark-vllm-docker/` — `hf-download.sh`, `launch-cluster.sh`, `autodiscover.sh`, `LICENSE`, `.env.example`, plus **`contrib/spark-vllm-docker/README.md`**. See **`docs/contrib-spark-vllm-docker.md`** for mapping to systemd/Ansible.

**Ansible + systemd for the Docker path (same flow, fully pushed):** set **`spark_provision_docker_vllm: true`** in `inventory/group_vars/sparks.yml`, then `ansible-playbook playbooks/provision_sparks.yml` (role **`vllm_docker_stack`**) installs **`vllm-docker-cluster.service`** on the leader (wraps `launch-cluster.sh exec …`). Build the **`vllm-node`** image on the Sparks first (upstream `build-and-copy.sh` or equivalent).

You can also use a full upstream checkout under `~/Workspace/microscaler/spark-vllm-docker` for image builds.

If Phase A is not **serving a successful `/v1/models`** by the **timebox end**, standardize on **`spark-vllm-docker`** (prefer the playbook above over ad-hoc shell):

1. **Networking** — Follow their **`docs/NETWORKING.md`** (passwordless SSH, ConnectX IPs, optional mesh). Aligns with your `nccl_interface` / stacked play assumptions.

2. **Build and copy** — On head: `./build-and-copy.sh -c` (or `./build-and-copy.sh` single node).

3. **Weights** — `./hf-download.sh <model> -c --copy-parallel` (or your existing HF prefetch on hosts; point container `HF_HOME` at the same cache if you unify paths).

4. **Run** — Example pattern from their README:  
   `./launch-cluster.sh exec vllm serve <model> --port 8000 --host 0.0.0.0 -tp 2 --distributed-executor-backend ray ...`  
   Note: default **API port is often 8000** in examples vs **8080** in Ansible — adjust clients/firewall.

5. **Gemma 4** — They ship recipes (e.g. `gemma4-26b-a4b`) with **`vllm-node-tf5`**, mods, and optional **`--no-ray`** for dual-Spark in some cases. For **31B-it**, start from their serve command and your resource limits; consider **`run-recipe.sh`** / **`--no-ray`** if Ray remains the bottleneck.

6. **Keep Ansible for** — OS baseline, CUDA, firewall, operators, and **optional** “Docker host prep” (Docker CE, `nvidia-container-toolkit`, user in `docker` group) if you add a small role later — *not* required for this doc to be actionable.

## Decision log (fill in)

| Date | Outcome |
| ---- | ------- |
| | Bare-metal success / failed criterion |
| | Pivot to Docker: yes/no |
