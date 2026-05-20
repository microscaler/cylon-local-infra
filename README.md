# Ansible for DGX Spark and dev hosts (container-only)

Idempotent, repeatable setup for:

- **Sparks** (`nvidia1`, `nvidia2`): Docker, NCCL env, firewall, and a **containerised
  stacked vLLM** (tensor-parallel=2 across both Sparks) on the **NGC** image
  (`nvcr.io/nvidia/vllm`). No host-side Python venv. Aligned with
  [NVIDIA stacked Sparks](https://build.nvidia.com/spark/vllm/stacked-sparks).
- **Dev hosts** (`ms02`): Docker CE + buildx + compose for Kind / `act` / local builds —
  separate from Sparks; see [`docs/docker-dev-host.md`](docs/docker-dev-host.md).

GPU workloads on Sparks run as `nvidia`, not `root`. See
[`docs/PRD-nvidia-user-exo-transition.md`](docs/PRD-nvidia-user-exo-transition.md).

> **2026-04 — bare-metal path removed.** The `roles/vllm/` bare-metal venv role and
> the custom-image `roles/vllm_docker_stack/` + `contrib/spark-vllm-docker/` were
> deleted. Rationale and migration notes:
> [`llmwiki/runs/2026-04-18-rip-out-bare-metal.md`](llmwiki/runs/2026-04-18-rip-out-bare-metal.md)
> and [`llmwiki/concepts/ngc-stacked-container-stack.md`](llmwiki/concepts/ngc-stacked-container-stack.md).

## The llmwiki

This repo carries its own [`llmwiki/`](llmwiki/AGENTS.md) — a persistent, LLM-maintained
knowledge base that records **what worked and what did not** as we bring up the Spark
stack. Read [`llmwiki/AGENTS.md`](llmwiki/AGENTS.md) first when picking up work.
Pattern: [Karpathy — LLM Wiki](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f).

## Prerequisites

- **Ansible 2.14+** (`pip install ansible`).
- **SSH** from the controller (your laptop / CI) to each Spark host. Default
  `ansible_user` is `casibbald` (passwordless sudo); override with
  `-e spark_ansible_user=root`. The `nvidia` runtime user is **not** for Ansible — its
  sudo is narrowed to apt / systemctl / journalctl.
- **QSFP interconnect up**: follow
  [NVIDIA — Connect two Sparks](https://build.nvidia.com/spark/connect-two-sparks/stacked-sparks)
  so `enp1s0f0np0` has an IPv4 on `169.254.0.0/16` on both hosts.
- **Docker present** on the Sparks from the DGX / NVIDIA platform image (see
  [`docs/docker-dgx.md`](docs/docker-dgx.md)). The `docker` role will **not**
  `apt install docker.io`.
- **NGC image pulled** on both Sparks — the Ansible role pulls
  `nvcr.io/nvidia/vllm:<tag>` automatically on the first run
  (`vllm_stacked_container_image` in `inventory/group_vars/sparks.yml`).

## Inventory

- **Sparks**: `nvidia1`, `nvidia2` (group `sparks`). Vars in
  [`inventory/group_vars/sparks.yml`](inventory/group_vars/sparks.yml).
- **Dev hosts**: `ms02` (group `dev_hosts`). Vars in `inventory/group_vars/dev_hosts.yml`.

Per-host overrides live in `inventory/host_vars/<host>.yml` — e.g. the LAN IPv6 disable
(`spark_lan_disable_ipv6_on_interface`) mitigating the ASGI-hang failure mode documented
in [`llmwiki/concepts/ipv6-asgi-hang.md`](llmwiki/concepts/ipv6-asgi-hang.md).

## Playbooks

Run from the repository root (directory containing `ansible.cfg`):

### Sparks — provision (canonical)

```bash
just spark-provision              # full reconcile + state assert (daily driver)
just spark-provision-recreate     # full + force vLLM container recreate
just spark-provision-check        # dry-run (--check --diff)
```

Advanced escape hatch only:

```bash
just spark-provision -- --skip-tags apt
just spark-provision -- --tags hf_prefetch,spark_assert
```

Legacy playbooks `cutover_roce.yml` and `refresh_hf_prefetch.yml` **fail with a pointer**
to `just spark-provision*`. Do not call partial `--tags vllm_ngc_stack` without
`spark_assert` — that path caused fleet drift.

**Model weights (HF prefetch) + which model `vllm serve` loads:** edit
`inventory/group_vars/sparks.yml` (`hf_prefetch_models`, `vllm_default_model`, …),
then follow the **Runbook — HF weights sync and vLLM model switchover** in
[`docs/provision_sparks.md`](docs/provision_sparks.md). From the repo root:

```bash
just spark-model-cutover --recreate   # or: just spark-provision-recreate
```

Hermes on ms02: `just spark-hermes-sync` (separate playbook — not Spark provision).

**While Ray or vLLM is starting** (before `/v1/models` responds), use
`python3 scripts/spark_model_status.py observe --ssh-host nvidia1` or
`just spark-stack-observe` — see **Observability** in
[`docs/provision_sparks.md`](docs/provision_sparks.md).

On success: leader serves the OpenAI API at `http://nvidia1:8000/v1`. First model load
is slow (62 GB weights for Gemma-4 31B); follow along with
`ssh casibbald@nvidia1 'docker logs -f vllm-ngc-ray-head'`.

### Sparks — NCCL (separate playbook, optional)

Host-side NCCL build (`sm_121`) and `all_gather_perf` across the pair. Run only if you
need host NCCL tooling outside the NGC image:

```bash
ansible-playbook playbooks/nccl_sparks.yml
ansible-playbook playbooks/nccl_sparks.yml --skip-tags nccl_test
```

### Dev hosts (`ms02`)

```bash
ansible-playbook playbooks/docker_dev_engine.yml -l ms02
ansible-playbook playbooks/dev_hosts.yml -l ms02
```

### Sync `~/Workspace` from the Mac to `ms02` (additive, preserves remote-only files)

```bash
ansible-playbook playbooks/sync_workspace.yml -l ms02 -e workspace_sync_dry_run=true  # preview
ansible-playbook playbooks/sync_workspace.yml -l ms02                                  # apply
```

Never passes `--delete` — local deletions are ignored, keeping `ms02` a
superset of everything we've ever had locally. Details:
[`llmwiki/concepts/workspace-sync.md`](llmwiki/concepts/workspace-sync.md).

## Layout

```
ansible.cfg                     # roles_path, inventory
inventory/
  hosts.yml
  group_vars/sparks.yml         # container stack vars (NGC image, TP, NCCL env, firewall)
  group_vars/dev_hosts.yml
  host_vars/nvidia1.yml, nvidia2.yml
playbooks/
  provision_sparks.yml          # canonical Spark provisioning
  nccl_sparks.yml               # NCCL build + all_gather test (separate)
  dev_hosts.yml                 # ms02 sudoers + docker group + firewall + Kind
  docker_dev_engine.yml         # one-time Docker CE on dev hosts
roles/
  spark_provision/              # orchestrates all Spark phases
  vllm_stacked_container/       # NGC Docker Ray + vllm serve (the vLLM path)
  hf_prefetch_service/          # leader-only systemd daemon — downloads HF models once, rsyncs to peers
  ngc_image_service/            # leader-only systemd daemon — polls NGC weekly, pulls new image tags, save/load to peers
  vllm_stack_autoupgrade/       # leader-only systemd daemon — promotes a newer image into the running stack after quiet window
  sudoers/ docker/ firewall/ cuda/ spark_apt/ spark_diagnostics/ nccl_sparks/
  docker_ce/ kind/              # dev_hosts only
docs/                           # architectural + reference docs
llmwiki/                        # persistent LLM-maintained knowledge base
```

## References

- [NVIDIA — vLLM stacked Sparks](https://build.nvidia.com/spark/vllm/stacked-sparks)
- [NVIDIA — NCCL for two Sparks](https://build.nvidia.com/spark/nccl/stacked-sparks)
- [vLLM multi-node serving](https://docs.vllm.ai/en/latest/examples/online_serving/multi-node-serving.html)
- [Karpathy — LLM Wiki pattern](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f)
