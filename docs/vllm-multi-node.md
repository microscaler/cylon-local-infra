# vLLM across two Sparks (multi-node)

vLLM can run a **single model** across both nodes using **tensor parallelism** (or tensor + pipeline). Only **one node** exposes the OpenAI-compatible API; the other node runs a **Ray worker** that participates in the distributed model. All coordination uses the **Ray** backend.

## Official NVIDIA vLLM documentation

NVIDIA’s DGX Spark vLLM playbooks (container-based and two-node):

- **[vLLM overview](https://build.nvidia.com/spark/vllm/overview)** – Prerequisites (CUDA 13.0, Docker, Python 3.12), model support matrix (NVFP4/FP8 on Spark).
- **[vLLM instructions](https://build.nvidia.com/spark/vllm/instructions)** – Single-node: pull NGC container `nvcr.io/nvidia/vllm` (tags are `<version>-py3`, e.g. `26.01-py3`; see [vLLM on NGC](https://catalog.ngc.nvidia.com/orgs/nvidia/containers/vllm)), run with `docker run --gpus all -p 8000:8000 ... vllm serve <model>`.
- **[vLLM stacked Sparks](https://build.nvidia.com/spark/vllm/stacked-sparks)** – Two-node: `run_cluster.sh`, Ray head/worker in containers, interface (e.g. `enp1s0f1np1`), tensor-parallel serve.
- **[vLLM troubleshooting](https://build.nvidia.com/spark/vllm/troubleshooting)** – CUDA version, Node 2 not visible, gated Hugging Face models, UMA memory, ARM64 image.

This playbook uses a **venv/pip** install on the host (under `/opt/vllm/venv`, owned by `**nvidia`**) and manual Ray head/worker steps instead of the NGC container. The same concepts apply: Ray head on leader, worker on second node, interconnect IPs, tensor-parallel-size 2. On the Sparks, run shells **as `nvidia`** (`ssh nvidia@<host>`); caches use `**HF_HOME=/home/nvidia/.cache/huggingface**` when using the provided systemd unit.

For a **timeboxed checklist** (bare-metal success criteria) and a **pivot** to the community **spark-vllm-docker** stack if needed, see **`docs/vllm-timebox-and-pivot.md`**. It also documents **`vllm_load_format`**, **`vllm_distributed_extra_env`**, and optional **`NCCL_IB_HCA`** tuning aligned with [spark-vllm-docker](https://github.com/eugr/spark-vllm-docker).

## Roles

- **Leader (API node)**: Runs the Ray head and `vllm serve`. This is the only node that serves the **OpenAI API** (e.g. port 8080). Use the **first** Spark in your inventory as leader (e.g. nvidia1).
- **Worker**: Runs `ray start --address=<LEADER_IP>` and blocks. It does **not** run the API; it only joins the Ray cluster so vLLM can use both GPUs.

Ray and the vLLM engine communicate over the **interconnect** (169.254.x.x). Use the **leader’s interconnect IP** as `RAY_ADDRESS` so traffic stays Spark-to-Spark.

## Prerequisites

- **Host packages:** the vLLM role installs `**python3-dev`** so `**Python.h`** is present. Without it, the **Triton** driver fails to JIT-compile (`fatal error: Python.h`) during engine startup — **no process listens on 8080**, so clients see **connection refused** (this is not a ufw/LAN issue until the engine is healthy).
- vLLM and Ray installed on both Sparks, and **firewall opened** for multi-node:  
`ansible-playbook playbooks/provision_sparks.yml`  
This installs vLLM + Ray and configures **ufw** to allow inbound TCP: **22** (SSH), **6379** (Ray), **8080** (vLLM API). Ports are set in `inventory/group_vars/sparks.yml` (`vllm_ray_port`, `vllm_api_port`, `firewall_allow_tcp_ports`).
- Passwordless SSH between the two Sparks as the same user (`**nvidia`** — NCCL key exchange or discover-sparks), so Ray can coordinate.
- Interconnect IPs known (e.g. from `nccl_sparks.yml` or `ip -4 addr show enp1s0f0np0` on each host).

## Automated stacking (Ansible)

**Single playbook (recommended):** from the repo root, run **`provision_sparks.yml`**. With **`spark_provision_vllm_stack: true`** in `inventory/group_vars/sparks.yml`, it installs the venv, deploys Ray head/worker + **`vllm-stacked`**, and prints an endpoint hint for the leader (OpenAI-compatible base URL **`http://<leader-LAN-IP>:8080/v1`** for Cursor and similar clients).

```bash
ansible-playbook playbooks/provision_sparks.yml
```

**Tags** (equivalent slices): after the venv exists on **both** Sparks, re-run only the stack phase:

```bash
ansible-playbook playbooks/provision_sparks.yml --tags vllm_stack
```

For **large models** that need **TP=2** (e.g. **Gemma 4 31B**), set **`vllm_sparks_deploy_single_node_service: false`** in `inventory/group_vars/sparks.yml` so provisioning does **not** start single-GPU **`vllm.service`** on the leader (would OOM or be invalid). Stacked Gemma flows assume this.

This:

- Reads each host’s interconnect IPv4 from `**nccl_interface**` (same as NCCL) and sets `**VLLM_HOST_IP**` in the generated units.
- Stops and disables single-node `**vllm.service**` on the **leader** (first host in sorted `sparks`) so port **8080** is free — override with `-e vllm_stack_stop_single_node_service=false` if you do not need that.
- Installs `**ray-head.service`** (leader), `**ray-worker.service`** (other Spark(s)), then `**vllm-stacked.service`** on the leader (`--tensor-parallel-size 2`, `--distributed-executor-backend ray`).

**Ray version:** pin `**vllm_ray_package`** in `inventory/group_vars/sparks.yml` (e.g. `ray==2.54.0`) so the venv matches running GCS; reinstall the venv if you change the pin.

**Check cluster:**

```bash
# Set spark_provision_verify_ray: true in sparks.yml, then:
ansible-playbook playbooks/provision_sparks.yml --tags verify
# On the leader as nvidia (optional):
# RAY_ADDRESS=<leader_interconnect_ip>:6379 /opt/vllm/venv/bin/ray status
```

The OpenAI API is on the leader at `**http://<leader>:8080**` (same port as single-node; only `**vllm-stacked**` runs after the stack playbook).

### Default model: `google/gemma-4-31B-it` (stacked)

`inventory/group_vars/sparks.yml` sets `**vllm_default_model**` to `**google/gemma-4-31B-it**` with `**vllm_tensor_parallel_size: 2**` (one GB10 GPU per Spark via Ray). `**vllm_api_server_extra_args**` passes `**--max-model-len**` (default **32768** here) so KV memory stays within **2× unified-memory** nodes — **raise or lower** after testing (model config allows very long context; full length usually needs more hardware).

- **vLLM:** Gemma 4 needs a **current** vLLM with **Gemma4** support ([vLLM Gemma 4 recipes](https://docs.vllm.ai/projects/recipes/en/latest/Google/Gemma4.html)). Reinstall the venv after changing `**vllm_package`**: `ansible-playbook playbooks/provision_sparks.yml`.
- **Transformers:** If startup fails with `**model type gemma4 but Transformers does not recognize this architecture`**, PyPI may not ship `**gemma4`** yet for your platform. `inventory/group_vars/sparks.yml` sets `**vllm_transformers_from_git: true**` so **`provision_sparks.yml`** (vLLM phase) pip-installs `**transformers**` from `**vllm_transformers_git_url**` (default Hugging Face **main**) after vLLM — override the URL to pin a tag if needed. The role installs `**git`** when `**vllm_transformers_from_git**` is true. Then restart `**vllm-stacked**`.
- **Weights:** first start **downloads tens of GB** into `**HF_HOME`**; increase `**vllm_stack_vllm_start_delay_sec`** if `**vllm-stacked**` starts before the worker is ready, or pre-download with **`spark_provision_hf: true`** and **`ansible-playbook playbooks/provision_sparks.yml --tags hf`**.
- **Multimodal:** Gemma 4 is **image-text-to-text**; OpenAI **chat** text requests are the common path — see vLLM docs for **image** inputs if you need vision.

## Step 1: Start the worker first

On the **worker** node (e.g. nvidia2), as `**nvidia`**, start Ray and point it at the leader’s interconnect IP and port (default 6379). Set `**VLLM_HOST_IP`** to this node’s interconnect IP if Ray mis-detects the interface ([Ray / vLLM multi-node](https://docs.vllm.ai/en/latest/examples/online_serving/multi-node-serving.html)).

```bash
# On worker (nvidia2). Replace LEADER_INTERCONNECT_IP with the leader’s 169.254.x.x (e.g. 169.254.102.149).
export VLLM_HOST_IP=$(ip -4 -o addr show dev enp1s0f0np0 2>/dev/null | awk '{print $4}' | cut -d/ -f1)   # use your Up CX-7 interface
source /opt/vllm/venv/bin/activate
ray start --address=LEADER_INTERCONNECT_IP:6379 --block
```

Leave this running. If the leader is not up yet, Ray will retry until it can connect (or you can start the leader in another terminal first and then start the worker).

## Step 2: Start the leader (Ray head + vLLM API)

On the **leader** node (e.g. nvidia1), as `**nvidia`**:

1. Start the Ray head and wait until the worker has joined (cluster size = 2):
  ```bash
   export VLLM_HOST_IP=$(ip -4 -o addr show dev enp1s0f0np0 2>/dev/null | awk '{print $4}' | cut -d/ -f1)   # leader interconnect IP; match your Up interface
   source /opt/vllm/venv/bin/activate
   ray start --head --port=6379
   # Optional: wait until both nodes are in the cluster, e.g.:
   # python -c "import ray; ray.init(); import time; [time.sleep(5) for _ in range(60) if sum(n['Alive'] for n in ray.nodes()) < 2]"
  ```
2. Start the vLLM server with tensor parallelism across 2 GPUs (both nodes) and the Ray backend:
  ```bash
   python -m vllm.entrypoints.openai.api_server \
     --model <your-model> \
     --port 8080 \
     --tensor-parallel-size 2 \
     --distributed-executor-backend ray
  ```

The **OpenAI-compatible API** is now available only on the **leader** at `http://<leader-ip>:8080` (use the leader’s management or interconnect IP depending on where clients run).

## Summary


| Question                                         | Answer                                                                                                                                                                                                                                   |
| ------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| How are requests processed across the two nodes? | vLLM uses **Ray** to run the model with `--tensor-parallel-size 2` (and optionally `--pipeline-parallel-size`). The leader runs the API and the scheduler; the worker runs a Ray worker that holds part of the model and runs inference. |
| Does only one node serve the OpenAI API?         | **Yes.** Only the **leader** runs `vllm serve` and serves the OpenAI-compatible endpoint. The worker does not serve HTTP; it only participates in the Ray cluster.                                                                       |


## Ray `status`: `memory` vs `object_store_memory`

`ray status` (with `RAY_ADDRESS=<leader interconnect>:6379`) shows **two** memory-related resource lines, not one total for DRAM:


| Line                      | Meaning (typical)                                                                   |
| ------------------------- | ----------------------------------------------------------------------------------- |
| `**memory`**              | Memory Ray exposes for **general scheduling** (tasks, heap, etc.).                  |
| `**object_store_memory`** | Memory reserved for the **plasma / object store** (shared objects between workers). |


**Do not read `memory` alone as “all RAM on the cluster.”** Add `**memory` + `object_store_memory`** (and compare to **0B/X** usage lines) to approximate how much memory Ray made available for scheduling. Ray also **does not** claim 100% of physical RAM (OS, CUDA, reserves, and Ray’s own defaults reduce what appears here).

Example: two Sparks with **128 GB RAM each** (~119 GiB per node, ~238 GiB combined in binary units). Seeing something like `**162 GiB memory` + `69 GiB object_store_memory` ≈ 231 GiB** total in the Resources section is **consistent** with two nodes minus reserves—not a sign that half your RAM disappeared.

For **per-node** breakdown, use `**ray status --verbose`** (or inspect each machine’s `/proc/meminfo`).

**Many `node_*` lines under “Active”:** Usually **stale Ray GCS state** from old runs (restarts, upgrades, duplicate `ray start`). **Ansible:** each stacked deploy runs **`roles/vllm/tasks/ray_reset_state.yml`** first (when **`vllm_ray_reset_state_before_stack`** is true): stops **`vllm-stacked`**, **`ray-worker`**, **`ray-head`**, runs **`ray stop -f`** as the venv user, removes **`/tmp/ray/session_*`** if **`vllm_ray_remove_session_dirs`** is true — then redeploys units. Re-run **`ansible-playbook playbooks/provision_sparks.yml --tags vllm_stack`** to apply. **Manual:** same stop order, then **`/opt/vllm/venv/bin/ray stop -f`** on both nodes, optional **`rm -rf /tmp/ray/session_*`**, then start **`ray-head`** → **`ray-worker`** → **`vllm-stacked`**.

**No listener on `:8080` but `vllm-stacked` is “active”:** The HTTP server starts only after the engine initializes. `**systemctl` showing `active (running)`** with **low RSS** (e.g. hundreds of MB for a **31B** model) usually means **weights are not loaded** and **nothing is bound to 8080** yet — or the main **Python** process survived while the engine failed. Confirm with `**ss -tlnp | grep 8080`** (or `**sudo lsof -iTCP:8080 -sTCP:LISTEN`**). A **large restart counter** on the unit means repeated failures — read `**journalctl -u vllm-stacked --no-pager`** from the last start for `**ERROR`**, **OOM**, or **placement group** messages.

**`VLLM_ENGINE_READY_TIMEOUT_S` / “waited 600s”:** vLLM’s default **600 s** is often too short for **large models + Ray + slow disk**. The role sets **`VLLM_ENGINE_READY_TIMEOUT_S`** on **`vllm-stacked`** (systemd **`Environment=`** and **`run-api-server-stacked.sh`**) from **`vllm_engine_ready_timeout_sec`** (default **1800** = **30 min** in `sparks.yml`). Re-apply **`ansible-playbook playbooks/provision_sparks.yml --tags vllm_stack`** so both the unit and wrapper update, then **`systemctl daemon-reload && systemctl restart vllm-stacked`**. **`TimeoutStopSec`** on the same unit is **`vllm_stacked_systemd_stop_timeout_sec`** (default **3600** = **60 min**) so **`systemctl stop`** does not SIGKILL mid-shutdown while the engine is still winding down — if you see **`stop-sigterm` timed out** after ~20 min, redeploy so the unit picks up a higher value.

If `**journalctl -u vllm-stacked`** shows `**Cannot provide a placement group requiring 2.0 GPUs`** or `**Engine core initialization failed**`, Ray never satisfied **TP=2** (second GPU not schedulable in time). If logs show `**marked dead because the detector has missed too many heartbeats`**, `**GCS failed to check the health of this node`**, or `**node manager has mistakenly been marked as dead**`, the worker raylet (interconnect IP, e.g. `169.254.x.x`) is unstable — often the same root cause as hundreds of phantom `**node_***` entries. Do a **full Ray reset**: stop `**vllm-stacked`** (leader), `**ray-worker`** (follower), `**ray-head**` (leader); as `**nvidia**` on **both** nodes run `**/opt/vllm/venv/bin/ray stop -f`**; optionally remove stale `**/tmp/ray/session_*`** after all Ray processes exit; then `**systemctl start ray-head**` → `**ray-worker**` → `**vllm-stacked**`, or re-run **`ansible-playbook playbooks/provision_sparks.yml --tags vllm_stack`**. Re-apply the same so **`/opt/vllm/run-api-server-stacked.sh`** matches **`vllm_default_model`** (e.g. Gemma) — a stale wrapper may still show **`TinyLlama`**.

**Weights only on the leader:** Prefetch/download on **nvidia2** does not help vLLM until **Ray can place shards on that node** — fix Ray health first; `**ray status`** should show **2 GPUs** before the API will listen.

`**KeyboardInterrupt: terminated` in `signal_handler` (often during `_sslobj.read`):** The API process received **SIGTERM**. vLLM treats that as a clean interrupt. Common causes: `**systemctl stop`/`restart`**, **Ansible redeploy** while the engine is still starting, or another operator stopping the unit. **First load** of a large model can take **many minutes**; avoid restarts until `**journalctl`** shows the server ready and `**ss -tlnp**` shows **:8080** listening.

**Clients on the LAN:** Use the leader’s **management** IPv4 (e.g. `**ip -4 addr`** on `**enP*`**), not only the inventory hostname — e.g. `**http://192.168.1.104:8080/v1**` if that is the leader’s address.

**`curl http://nvidia1:8080` fails from your laptop:** (1) **`nvidia1` must resolve** to the leader’s **reachable** IP on the client (`**getent hosts nvidia1**` / `**ping**`). Inventory names are for **Ansible**, not always DNS. (2) **Firewall**: the client must be allowed — see **`firewall_trusted_lan_cidr`** and **`firewall_trusted_lan_tcp_ports`** in `sparks.yml` (or the global **`firewall_allow_tcp_ports`** list). (3) **API not up yet**: on the **leader**, run `**curl -sS http://127.0.0.1:8080/v1/models**` and `**ss -tlnp | grep 8080**`. Logs may show **EngineCore** and Ray **connected** while weights still load and **Uvicorn has not bound** — wait for **application startup** / **listening** messages; first large-model start can take **well past** the Ray connection lines.

**Expected Ray warnings (two Sparks, 1 GPU each, `tensor_parallel_size: 2`):** Lines like **`Tensor parallel size (2) exceeds available GPUs (1)`** or **`tensor_parallel_size=2 is bigger than ... 1 GPUs in a node`** are **normal**: each node exposes **one** GPU; vLLM **places one TP rank per node**. Worry only if logs later show **placement group** failures, **Cannot provide a placement group requiring 2.0 GPUs**, or **Engine core initialization failed** — then check **`ray status`** shows **2 GPUs** total and interconnect stability between nodes.

## Optional: pipeline parallelism

If you prefer to split the model by layers (pipeline) instead of (or in addition to) tensor parallelism:

```bash
# Leader
python -m vllm.entrypoints.openai.api_server \
  --model <model> \
  --port 8080 \
  --tensor-parallel-size 1 \
  --pipeline-parallel-size 2 \
  --distributed-executor-backend ray
```

For 2 nodes with 1 GPU each, `--tensor-parallel-size 2` uses both GPUs for tensor parallelism; `--pipeline-parallel-size 2` uses both for pipeline parallelism. See [vLLM parallelism](https://docs.vllm.ai/en/latest/serving/parallelism_scaling.html).

## Troubleshooting: `journalctl` shows `Control process exited, code=killed, status=15/TERM` during `Starting vllm-stacked`

If this repeats every ~10–20s and `**ExecStart` never runs**, the old unit used `**ExecStartPre=/bin/sleep …`**. Some systemd versions or policies terminate long `ExecStartPre` steps (SIGTERM to the sleep) so the service never reaches `**run-api-server-stacked.sh`**. Current playbooks put `**sleep**` inside the **wrapper script** instead (same `**vllm_stack_vllm_start_delay_sec`**), with `**ExecStart`** pointing at that script only. Re-deploy the stacked unit: **`ansible-playbook playbooks/provision_sparks.yml --tags vllm_stack`**.

## Troubleshooting: Ansible “stalls” on `Ensure stacked vLLM service is started`

The role uses `**no_block: true**` so `systemctl start` is queued and the playbook returns immediately. If you still see a long wait, check SSH timeouts (`timeout` in `ansible.cfg`) or run the stack play with `-vv` to see the last task.

**On the leader**, if the API never comes up:

```bash
sudo systemctl status vllm-stacked ray-head --no-pager
sudo journalctl -u vllm-stacked -u ray-head -u ray-worker -n 200 --no-pager
# On the follower:
sudo journalctl -u ray-worker -n 100 --no-pager
```

Typical causes: **second Spark not in the Ray cluster** (`ray status` with `RAY_ADDRESS=<leader_ic>:6379`), **wrong interconnect IP** (`nccl_interface`), **Ray/vLLM version skew**, or **OOM** on load. Confirm `**spark_interconnect_ip`** in the play matches `ip -4 addr show dev <nccl_interface>` on each host.

## Troubleshooting the vLLM systemd service (single-node)

If `vllm.service` fails (e.g. `Active: activating (auto-restart)` and `status=1/FAILURE`):

1. **View logs** (on the Spark where the service runs):
  ```bash
   journalctl -u vllm -n 100 --no-pager
  ```
2. **Run the server manually** as `**nvidia`** to see the error in the terminal:
  ```bash
   sudo systemctl stop vllm
   sudo -u nvidia -H /opt/vllm/venv/bin/python -m vllm.entrypoints.openai.api_server --model TinyLlama/TinyLlama-1.1B-Chat-v1.0 --port 8080
  ```
   Or log in as `nvidia` and omit `sudo -u nvidia -H`.
   Fix any import, CUDA, or model-download errors, then redeploy the unit from your Ansible host:

```bash
ansible-playbook playbooks/provision_sparks.yml -l nvidia1 --tags vllm_serve
```

The unit runs a wrapper script. The playbook **auto-detects** the directory containing `libcudart.so.12` and uses it in the wrapper.

**If `libcudart.so.12` or `libcudart.so.13` is not on the host:** vLLM requires the CUDA runtime. Install the **CUDA toolkit** on each host. The playbook auto-detects either `.so.12` or `.so.13`. On the host run `find /usr /usr/local /opt -name 'libcudart.so*'`; if it returns nothing, install CUDA (see [NVIDIA CUDA download](https://developer.nvidia.com/cuda-downloads)). For Ubuntu 24.04 aarch64 (DGX Spark), use the repo keyring and `cuda-toolkit-13-0` as in `group_vars/sparks.yml`. If the library is in a custom path, set `vllm_cuda_lib_path` and `vllm_skip_cudart_check: true`, then redeploy.

## Multi-node troubleshooting (from NVIDIA)


| Symptom                                 | Cause                    | Fix                                                                                                                                                                                                                                                                                          |
| --------------------------------------- | ------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Node 2 not visible in Ray cluster       | Network / QSFP           | Verify [Connect two Sparks](https://build.nvidia.com/spark/connect-two-sparks/stacked-sparks), QSFP cable, interconnect IPs; use leader’s **interconnect** IP in `ray start --address=...`.                                                                                                  |
| Cannot access gated repo / Hugging Face | Restricted model         | [HuggingFace token](https://huggingface.co/docs/hub/en/security-tokens), request access to the [gated model](https://huggingface.co/docs/hub/en/models-gated#customize-requested-information); on the Spark run `**hf auth login`** (or legacy `**huggingface-cli login`**) as `**nvidia**`. |
| CUDA version mismatch                   | Wrong toolkit            | Official single-Spark guidance: CUDA 12.9; our Sparks use CUDA 13.0 (libcudart.so.13). Ensure `vllm_torch_extra_index_url` matches host CUDA (e.g. cu124).                                                                                                                                   |
| Memory issues (UMA)                     | DGX Spark unified memory | If within capacity but still OOM, try flushing buffer cache: `sudo sh -c 'sync; echo 3 > /proc/sys/vm/drop_caches'` (see [vLLM troubleshooting](https://build.nvidia.com/spark/vllm/troubleshooting)).                                                                                       |


Interface name: NVIDIA’s stacked-sparks doc uses `enp1s0f1np1` for the high-speed link; our NCCL/vLLM vars use `nccl_interface` (e.g. `enp1s0f0np0`). Use the interface that shows “(Up)” from `ibdev2netdev` on your hardware.