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

## NCCL (separate playbook)

NCCL host-side build + `all_gather_perf` remain in `playbooks/nccl_sparks.yml`. Enable
`spark_provision_cuda_toolkit: true` first if you want the CUDA toolkit on the host.

## Non-Spark hosts

`dev_hosts` (e.g. `ms02`): `playbooks/dev_hosts.yml`, `playbooks/docker_dev_engine.yml`
— unchanged.
