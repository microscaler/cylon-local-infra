# Spark provisioning (`provision_sparks.yml`) — container-only

Single entry point for **DGX Spark** hosts in inventory group **`sparks`**:

```bash
ansible-playbook playbooks/provision_sparks.yml
```

As of **2026-04** the Spark stack is **container-only** (NGC `nvcr.io/nvidia/vllm`).
The bare-metal venv path (`roles/vllm/`) and the custom-built-image path
(`roles/vllm_docker_stack/` + `contrib/spark-vllm-docker/`) were removed — see
[`llmwiki/runs/2026-04-18-rip-out-bare-metal.md`](../llmwiki/runs/2026-04-18-rip-out-bare-metal.md).

## Phases (role `spark_provision`)

| Phase | Variable (default) | Role |
|---|---|---|
| Sudoers / users | `spark_provision_sudoers` (true) | `sudoers` |
| CUDA apt cleanup | `spark_provision_cuda_apt_cleanup` (true) | `cuda` `apt_cleanup` |
| APT upgrade | `spark_provision_apt` (true) | `spark_apt` |
| Docker Engine (DGX image repos) | `spark_provision_docker` (true) | `docker` |
| Firewall (ufw) | `spark_provision_firewall` (true) | `firewall` |
| LAN IPv6 sysctl (per host_vars) | `spark_provision_lan_ipv6_sysctl` (true) | inline (`tasks/lan_ipv6_sysctl.yml`) |
| CUDA toolkit on host | `spark_provision_cuda_toolkit` (**false**) | `cuda` — enable only for `playbooks/nccl_sparks.yml` |
| Hugging Face prefetch daemon (leader-only) | `spark_provision_hf` (false) | `hf_prefetch_service` — systemd service on the leader, downloads once + rsyncs to peers; poll `/var/lib/hf-prefetch/state.json` |
| NGC image sync daemon (leader-only) | `spark_provision_ngc_image_sync` (false) | `ngc_image_service` — weekly polls NGC for new `nvcr.io/nvidia/vllm:YY.MM-py3` tags, pulls them on the leader, `docker save \| ssh \| docker load` to peers over QSFP; poll `/var/lib/ngc-image-sync/state.json` |
| vLLM stack auto-upgrade (leader-only) | `spark_provision_vllm_autoupgrade` (false) | `vllm_stack_autoupgrade` — promotes newer NGC tag into running stack after `stabilization_sec` (1 h default) + vLLM `/metrics` quiet-window (5 min default). Installs disabled by default; flip `vllm_autoupgrade_enabled: true` to arm. Poll `/var/lib/vllm-stack-autoupgrade/state.json` |
| **NGC stacked vLLM** (Docker Ray + `vllm serve`) | **`spark_provision_vllm_stacked_container` (true)** | `vllm_stacked_container` |
| Diagnostics | `spark_provision_diagnostics` (false) | `spark_diagnostics` |

## Tags

Commonly used:

```bash
ansible-playbook playbooks/provision_sparks.yml --tags vllm_ngc_stack
ansible-playbook playbooks/provision_sparks.yml --tags hf_prefetch
ansible-playbook playbooks/provision_sparks.yml --tags firewall
ansible-playbook playbooks/provision_sparks.yml --skip-tags apt
```

## What the NGC stack produces

| Host | Containers |
|---|---|
| leader (`nvidia1`) | `vllm-ngc-ray-head` — runs `ray start --head` and, via `docker exec -d`, `vllm serve <model> --tp 2 --host 0.0.0.0 --port 8000`. |
| follower(s) (`nvidia2`) | `vllm-ngc-ray-worker-<host>` — runs `ray start --address <leader>:6379`. |

Both containers use `--network host`, `--gpus all`, `--shm-size 10.24g`, and
`--restart unless-stopped`. HF cache on the host is bind-mounted at
`/root/.cache/huggingface`.

## Hugging Face prefetch daemon

When `spark_provision_hf: true`, the `hf_prefetch_service` role installs a
long-running systemd daemon (`hf-prefetch.service`) on the **leader** Spark.
Ansible **does not block** on downloads any more — it writes
`/etc/hf-prefetch/config.yaml` from `hf_prefetch_models` and returns. The
daemon reads the config, downloads each model once via the NGC `hf` CLI in
an ephemeral container, and rsyncs the repo's hub subtree to every peer
Spark over the QSFP interconnect.

```bash
# Poll readiness from anywhere with ssh to the leader
ssh casibbald@nvidia1 'jq .models /var/lib/hf-prefetch/state.json'

# Follow live progress
ssh casibbald@nvidia1 'sudo journalctl -u hf-prefetch -f'

# Add a model: edit inventory/group_vars/sparks.yml → hf_prefetch_models,
# then rerun ansible (daemon picks up config changes automatically)
ansible-playbook playbooks/provision_sparks.yml --skip-tags apt,vllm_ngc_stack
```

Status values in `state.json`:
`unknown` → `downloading` → `syncing` → `ready` (or `error` with backoff).

Flip `hf_prefetch_wait_for_ready: true` in inventory if a particular run
*does* need Ansible to block until all models are ready (CI, pipelines,
etc.). See [`llmwiki/entities/hf-prefetch-service.md`](../llmwiki/entities/hf-prefetch-service.md).

## Runbook — HF weights sync and vLLM model switchover

Use this when you **add a new Hugging Face repo to the cache**, **change which
checkpoint the stack serves**, or **refresh daemon config** after editing
inventory. All knobs live in **`inventory/group_vars/sparks.yml`** unless you
pass one-off `-e` overrides to Ansible.

### 1. Which file to edit

| What you are changing | Where | Key variables |
|---|---|---|
| **Which HF repos are downloaded + rsynced** to every Spark | [`inventory/group_vars/sparks.yml`](../inventory/group_vars/sparks.yml) | `hf_prefetch_models` (list of repo ids). Requires `spark_provision_hf: true` (already set in this repo). Order matters — the daemon processes entries sequentially. |
| **Which weights `vllm serve` loads** | same file | `vllm_default_model` (HF repo id passed to `vllm serve`). |
| **OpenAI-compat model ids** advertised at `/v1/models` | same file | `vllm_api_server_extra_args` — typically `--served-model-name …` entries alongside `vllm_default_model`. |
| **NGC container image** (vLLM / CUDA stack) | same file | `vllm_stacked_container_image` |
| **Force new Ray/vLLM containers** when Ansible alone would skip `vllm serve` | extra var | `vllm_stacked_container_recreate=true` — required when switching `vllm_default_model` so the running server is replaced (see script below). |
| **Hermes agent on ms02** (OpenAI-compat base URL + `model` id) | [`inventory/group_vars/dev_hosts.yml`](../inventory/group_vars/dev_hosts.yml) + optional [`inventory/host_vars/ms02.yml`](../inventory/host_vars/ms02.yml) | `hermes_agent_openai_base_url`, `hermes_agent_openai_model_id` (keep aligned with a primary `--served-model-name` in `vllm_api_server_extra_args`). Set `hermes_agent_dotenv_path` on ms02 to the Hermes `.env` file to patch; optional `hermes_agent_post_sync_command` to restart the service. |

Optional: advanced `vllm serve` flags (`vllm_tensor_parallel_size`, `vllm_load_format`,
`vllm_api_server_extra_args`, …) stay in the same file under the **NGC stacked
container** and **Extra `vllm serve` args** sections.

### 2. Apply changes from your laptop (controller)

**Canonical helper (Python, from repo root):** [`scripts/spark_model_status.py`](../scripts/spark_model_status.py)

```bash
# Show commands that will run, then execute:
python3 scripts/spark_model_status.py help

# Full local workflow after editing sparks.yml:
#   1) ansible-playbook playbooks/refresh_hf_prefetch.yml
#   2) SSH leader: hf_prefetch_service.py --once  (one download + peer sync pass)
#   3) ansible-playbook … --tags vllm_ngc_stack  (optionally with recreate)
python3 scripts/spark_model_status.py cutover --recreate

# Same, then patch Hermes on ms02 (requires hermes_agent_dotenv_path in host_vars/ms02.yml):
python3 scripts/spark_model_status.py cutover --recreate --sync-hermes

# Hermes-only (after inventory edits):
python3 scripts/spark_model_status.py sync-hermes
just spark-hermes-sync

# Or run steps individually:
python3 scripts/spark_model_status.py ansible-prefetch
python3 scripts/spark_model_status.py prefetch-once --ssh-host nvidia1
python3 scripts/spark_model_status.py ansible-vllm --recreate

# One-off model override without committing a file change:
python3 scripts/spark_model_status.py ansible-vllm --recreate \
  -e vllm_default_model=org/model-id

# Same via just (repo root):
just spark-model-cutover --recreate
```

Environment (optional): `SPARK_SSH_HOST` (default `nvidia1`), `SPARK_ANSIBLE_INVENTORY`
or `ANSIBLE_INVENTORY` for a non-default inventory path, `SPARK_VLLM_API` for status probes.

**Raw Ansible equivalents** (same semantics as the script):

```bash
# Deploy `/etc/hf-prefetch/config.yaml` on the cluster from inventory
# (avoids the older “tags don’t propagate into include_role” footgun).
ansible-playbook playbooks/refresh_hf_prefetch.yml

# Apply / refresh Ray + `vllm serve` (idempotent).
ansible-playbook playbooks/provision_sparks.yml -l sparks --tags vllm_ngc_stack

# Hard bounce containers + pick up new vllm_default_model / image env:
ansible-playbook playbooks/provision_sparks.yml -l sparks --tags vllm_ngc_stack \
  -e vllm_stacked_container_recreate=true

# Hermes agent on ms02 — patch OPENAI_BASE_URL / OPENAI_MODEL in a dotenv (optional)
ansible-playbook playbooks/sync_hermes_ms02.yml -l ms02
```

`just spark-hf-prefetch-provision` runs `provision_sparks.yml --tags hf_prefetch`
(role install + config render). Prefer **`refresh_hf_prefetch.yml`** when you
only need to push config from the edited inventory (matches the Python helper).

### 3. Verify

```bash
# HTTP + remote prefetch JSON (from the controller):
python3 scripts/spark_model_status.py --json
just spark-model-status --json

# Ray + vLLM progress on the leader (SSH): ports, ray status, vllm-serve.log, /metrics
python3 scripts/spark_model_status.py observe --ssh-host nvidia1

# Leader state file (any SSH client):
ssh casibbald@nvidia1 'sudo jq .models /var/lib/hf-prefetch/state.json'
```

`/v1/models` on the leader: `http://nvidia1:8000/v1/models` (or your LAN IP).

### 4. Observability — Ray startup and model load

`curl http://nvidia1:8000/v1/models` only answers once the OpenAI server is up;
during **Ray bootstrap** and **cold model load** the API may refuse connections
or hang. Use the sources below for progress.

**One-shot bundle (from the Ansible controller over SSH):**

```bash
python3 scripts/spark_model_status.py observe --ssh-host nvidia1
just spark-stack-observe
```

This prints, on the **leader**: `docker ps` for `vllm-*`, listening ports
(**8000** = vLLM, **8265** = Ray dashboard, **6379** = Ray GCS), `ray status`
inside the head container, the **`vllm serve`** process line, the last lines of
**`/root/vllm-serve.log`** (where `docker exec -d` redirects stderr/stdout), a
trimmed **`/v1/models`** response, and the first lines of **`/metrics`**
(Prometheus text — useful while the engine is initializing).

**Which model is “actually” loaded — Ray vs vLLM vs `/v1/models` ids**

- **Ray** is the **distributed backend** (placement groups, workers across Sparks)
  for **vLLM’s tensor-parallel engine**. It does **not** choose among the
  OpenAI-compat **model names** listed in `/v1/models`.
- **One** Hugging Face repo is loaded as weights: whatever **`vllm serve`**
  was started with — in inventory, `vllm_default_model` in
  [`inventory/group_vars/sparks.yml`](../inventory/group_vars/sparks.yml). Ansible
  generates the `vllm serve …` line inside the head container (see
  `roles/vllm_stacked_container`).
- **`/v1/models` can list several `id`s** (`gpt-4o-mini`, `qwen3`, the HF id,
  …) because `vllm_api_server_extra_args` passes multiple **`--served-model-name`**
  aliases for Cursor/OpenAI client compatibility. Those names are **labels**
  for the **same** engine. In the JSON you shared, every entry has the same
  **`"root": "Qwen/Qwen3.6-35B-A3B-FP8"`** — that **`root`** field is the
  checkpoint vLLM/Ray are running.
- **Proof on the leader:** the `observe` output includes **`pgrep -af 'vllm serve'`**
  (full argv, including the HF id) and **`/root/vllm-serve.log`**.

```bash
# Unique HF roots advertised (should be one row while a single engine is up).
# Run on the leader, or use http://nvidia1:8000/… / the leader LAN IP from your laptop.
# (127.0.0.1 on your Mac is NOT nvidia1 unless you have an SSH -L tunnel to :8000.)
ssh casibbald@nvidia1 'curl -sS http://127.0.0.1:8000/v1/models | jq "[.data[].root] | unique"'
```

**Ray dashboard (cluster view):** bound on the leader at **`127.0.0.1:8265`**
(loopback only). From your laptop:

```bash
ssh -N -L 8265:127.0.0.1:8265 casibbald@nvidia1
# then open http://127.0.0.1:8265/ in a browser
```

**Raw Docker / logs (leader):**

```bash
# Ray container bootstrap (not the vLLM server stdout — that is in vllm-serve.log)
ssh casibbald@nvidia1 'docker logs --tail 120 vllm-ngc-ray-head 2>&1'

# vLLM server log (inside head container)
ssh casibbald@nvidia1 'docker exec vllm-ngc-ray-head bash -lc "tail -f /root/vllm-serve.log"'
```

**HF weight download / peer sync** (before `vllm serve` even starts): follow
`hf-prefetch` on the leader — `sudo journalctl -u hf-prefetch -f` and
`/var/lib/hf-prefetch/state.json` (see the Hugging Face prefetch section above).

### 5. Operational notes

- **Weights must be prefetched (or present in the shared HF cache)** before
  pointing `vllm_default_model` at a large repo — otherwise `vllm serve` will
  download at startup and starve the interconnect.
- **`--recreate` / `vllm_stacked_container_recreate=true`** tears down and
  recreates the Ray head/worker containers; expect **API downtime** during the
  bounce.
- **RoCE / NCCL-only changes** (not HF/vLLM model ids) use
  [`playbooks/cutover_roce.yml`](../playbooks/cutover_roce.yml) — different
  playbook; see comments in that file.

## NCCL (separate playbook)

NCCL host-side build + `all_gather_perf` remain in `playbooks/nccl_sparks.yml`. Enable
`spark_provision_cuda_toolkit: true` first if you want the CUDA toolkit on the host.

## Non-Spark hosts

`dev_hosts` (e.g. `ms02`): `playbooks/dev_hosts.yml`, `playbooks/docker_dev_engine.yml`
— unchanged.
