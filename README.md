# Ansible playbooks for DGX Spark and dev hosts

Idempotent, repeatable setup for:
- **Sparks** (nvidia1, nvidia2): Docker, **`nvidia`** runtime user + operators (sudo), NCCL, CUDA, vLLM/Ray — aligned with [NVIDIA NCCL for Two Sparks](https://build.nvidia.com/spark/nccl/stacked-sparks).
- **Dev hosts** (ms02): **Docker CE + buildx + compose** (for Kind, **act**, local builds) — separate from Sparks; see [docs/docker-dev-host.md](docs/docker-dev-host.md). Users/sudoers, firewall (ufw), Kind.

GPU workloads on Sparks run as **`nvidia`**, not `root`. See [docs/PRD-nvidia-user-exo-transition.md](docs/PRD-nvidia-user-exo-transition.md) and [docs/MIGRATION-root-to-nvidia.md](docs/MIGRATION-root-to-nvidia.md).

## Prerequisites

- **Ansible** 2.14+ (e.g. `pip install ansible` or system package).
- **SSH (controller → hosts)**: Ansible **always** connects from the machine where you run `ansible-playbook` (e.g. your Mac) to each inventory host. That is separate from **Spark↔Spark** SSH over the interconnect (used by NCCL/Ray), which runs **on the Sparks** when tasks execute there.
  - **Sparks**: `inventory/group_vars/sparks.yml` sets **`ansible_user`** (default **`casibbald`**, an operator with passwordless sudo) and **`ansible_become: true`**. Put your Mac’s SSH public key in that user’s `authorized_keys` on both Sparks. Override with `-e spark_ansible_user=root` if you use root SSH instead, or set `spark_ansible_user` in `sparks.yml` to match your operator username.
  - **Do not** use `nvidia` as `ansible_user` unless you widen sudo (that account is limited to `apt`/`systemctl` in the default sudoers rule).
  - **Interactive use**: `ssh nvidia@nvidia1` for GPU work; `ssh casibbald@nvidia1` (or your operator) for Ansible-style access.
  - Dev hosts: `ssh root@ms02` or your dev user per `inventory/group_vars/dev_hosts.yml`.
- **NCCL** (sparks): [Connect two Sparks](https://build.nvidia.com/spark/connect-two-sparks/stacked-sparks) done (QSFP link up, interface with IPs). The playbook **probes** passwordless SSH (`nvidia` → peer over interconnect, remote `ls`); **only if that fails**, pass `nccl_test_user_password` so each Spark can `ssh-copy-id` to the other over **169.254.0.0/16**. Or run NVIDIA's **discover-sparks** on one Spark **as `nvidia`** (see [scripts/README.md](scripts/README.md)).
- **vLLM** (sparks): **CUDA toolkit** on each Spark so **libcudart** (`.so.12` or `.so.13`) is present; the playbook can install it (role **cuda**). See [docs/vllm-multi-node.md](docs/vllm-multi-node.md). **Official NVIDIA vLLM on DGX Spark:** [Overview](https://build.nvidia.com/spark/vllm/overview) · [Instructions](https://build.nvidia.com/spark/vllm/instructions) · [Stacked Sparks](https://build.nvidia.com/spark/vllm/stacked-sparks) · [Troubleshooting](https://build.nvidia.com/spark/vllm/troubleshooting). **Container image:** [vLLM on NGC](https://catalog.ngc.nvidia.com/orgs/nvidia/containers/vllm).

## Inventory

- **Sparks**: `nvidia1`, `nvidia2` (group `sparks`). Vars in `inventory/group_vars/sparks.yml`.
- **Dev hosts**: `ms02` (group `dev_hosts`). Vars in `inventory/group_vars/dev_hosts.yml`.

Edit `inventory/hosts.yml` if your hostnames differ. Override variables in the relevant `group_vars` or via `-e`:

- **Ansible login (sparks)**: `spark_ansible_user` (default `casibbald`) — must have passwordless `sudo` on the Sparks for playbooks to run.
- **Runtime user (sparks)**: `spark_runtime_user` (default `nvidia`), `spark_runtime_home`.
- **Sudoers**: `sudoers_users` — list of `{ name, rule?, groups? }`. `nvidia` gets targeted NOPASSWD for `apt` / `systemctl`; operators (e.g. `casibbald`) default to full NOPASSWD. `sudoers_ensure_users`, `sudoers_user_groups` (fallback when `groups` omitted per user).
- `docker_group_users`: e.g. `[nvidia, casibbald]` on sparks.
- **Sparks:** `docker_install_packages: false` — use Docker from the **DGX / NVIDIA** image repos, do not install `docker.io` via Ansible ([docs/docker-dgx.md](docs/docker-dgx.md)).
- `docker_install_packages`, `docker_packages`, `docker_daemon_config` (dev hosts / generic Ubuntu may install `docker.io`).
- `nccl_interface`, `nccl_test_user` (defaults to `spark_runtime_user`), NCCL paths under `/home/nvidia/...`.
- **vLLM** (sparks): `vllm_venv_path`, `vllm_run_user`, `vllm_working_directory`, `vllm_hf_home`, `vllm_torch_extra_index_url`, `vllm_default_model`, `vllm_extra_pip_packages`, `vllm_transformers_from_git` / `vllm_transformers_git_url` (Gemma 4 / `gemma4` when PyPI lags), `vllm_api_server_extra_args` (optional list, e.g. `--max-model-len`), `vllm_api_port` (8080), `vllm_ray_port` (6379), `vllm_ray_package` (pin, e.g. `ray==2.54.0`), `vllm_tensor_parallel_size` (stacked), `vllm_stack_vllm_start_delay_sec`, `vllm_engine_ready_timeout_sec` (default **1800** = 30 min) / `vllm_stacked_systemd_stop_timeout_sec` (default **3600** = 60 min graceful stop), `vllm_load_format` / `vllm_distributed_extra_env` (Spark load + NCCL/Ray parity with [spark-vllm-docker](https://github.com/eugr/spark-vllm-docker)). **Timebox / Docker pivot:** `docs/vllm-timebox-and-pivot.md`. **Vendored Docker helpers (upstream scripts):** `contrib/spark-vllm-docker/` — see `docs/contrib-spark-vllm-docker.md`. **Docker stack (optional phase):** set `spark_provision_docker_vllm: true` in `sparks.yml` — role `vllm_docker_stack` (invoked from `playbooks/provision_sparks.yml`). **Firewall**: `firewall_allow_tcp_ports`.
- **Spark provisioning phases:** `spark_provision_*` booleans — see `roles/spark_provision/defaults/main.yml` and [docs/provision_sparks.md](docs/provision_sparks.md).
- **APT upgrades** (sparks): `spark_apt_upgrade_mode` (`safe` / `full` / `dist`), `spark_apt_upgrade_enabled`, `spark_apt_cache_valid_time`, `spark_apt_autoremove`, optional `spark_apt_serial` — role `spark_apt` (same playbook as below).
- **Dev hosts**: `docker_install_packages` (false — CE via `docker_dev_engine.yml`), `docker_group_users`, `firewall_allow_tcp_ports`, `kind_version`, `kind_install_path`.

## Playbooks

Run from the **repository root** (directory containing `ansible.cfg`):

```bash
cd /path/to/cylon-local-infra
```

### Sparks — provision (canonical)

**One playbook** drives sudoers, APT, Docker, CUDA toolkit, vLLM/Ray, optional stacked serving, HF prefetch, optional Docker-based vLLM, and diagnostics — via **`spark_provision_*`** toggles in `inventory/group_vars/sparks.yml`. Details: [docs/provision_sparks.md](docs/provision_sparks.md).

```bash
ansible-playbook playbooks/provision_sparks.yml
ansible-playbook playbooks/provision_sparks.yml --tags apt
ansible-playbook playbooks/provision_sparks.yml --skip-tags vllm_test
```

Override APT mode if many packages are **kept back** (e.g. kernel/NVIDIA stacks): `-e spark_apt_upgrade_mode=full` — read release notes before forcing. Vars: `spark_apt_upgrade_mode` (`safe` \| `full` \| `dist`), etc. — see `inventory/group_vars/sparks.yml`.

### NCCL

Builds NCCL (Blackwell sm_121) and nccl-tests **as `nvidia`** under `/home/nvidia/`, then runs `all_gather_perf` across the two nodes over the interconnect. Includes a play to ensure **`nvidia`** (and operators) exist via **sudoers** role.

**Skip slow recompiles:** if `libnccl.so` exists and the NCCL clone is checked out exactly at `nccl_version` (`git describe --tags --exact-match`), the role skips NCCL git + `make`; if `all_gather_perf` also exists, it skips nccl-tests `make`. Use `-e nccl_force_recompile=true` or `-e nccl_tests_force_rebuild=true` to force rebuilds. Optional `--version` probes on small helper binaries run when skipping (disable with `-e nccl_version_probe=false`).

```bash
ansible-playbook playbooks/nccl_sparks.yml -e nccl_test_user_password=YOUR_PASSWORD   # only if SSH probe to peer fails
ansible-playbook playbooks/nccl_sparks.yml
ansible-playbook playbooks/nccl_sparks.yml --skip-tags nccl_test
ansible-playbook playbooks/nccl_sparks.yml -e nccl_interface=enp1s0f1np1
ansible-playbook playbooks/nccl_sparks.yml --tags discover_sparks
ansible-playbook playbooks/nccl_sparks.yml -e nccl_force_recompile=true   # ignore reuse and rebuild NCCL
```

### vLLM / stacked Ray (Sparks)

All phases run through **`provision_sparks.yml`** (role **`spark_provision`**). Set **`spark_provision_vllm_stack: true`** (and **`vllm_sparks_deploy_single_node_service: false`** for large TP=2 models) in `inventory/group_vars/sparks.yml`. Optional **`spark_provision_hf: true`** pre-downloads **`hf_prefetch_models`** via the **`hf_spark`** role.

```bash
ansible-playbook playbooks/provision_sparks.yml
ansible-playbook playbooks/provision_sparks.yml -e vllm_torch_extra_index_url=https://download.pytorch.org/whl/cu121
ansible-playbook playbooks/provision_sparks.yml --skip-tags vllm_test
ansible-playbook playbooks/provision_sparks.yml --tags vllm_stack
ansible-playbook playbooks/provision_sparks.yml --tags verify   # ray status (set spark_provision_verify_ray: true)
```

The leader task prints **`http://<leader-LAN-IP>:8080/v1`** when stacked provisioning runs. First model load is asynchronous — watch **`journalctl -u vllm-stacked -f`** on the leader until the API answers.

**Single-node (manual):** as `nvidia`:  
`/opt/vllm/venv/bin/python -m vllm.entrypoints.openai.api_server --model <model> --port 8080`

See [docs/vllm-multi-node.md](docs/vllm-multi-node.md). NVIDIA: [vLLM stacked Sparks](https://build.nvidia.com/spark/vllm/stacked-sparks). **PRD:** [docs/PRD-spark-stacking-nvidia2.md](docs/PRD-spark-stacking-nvidia2.md).

### Dev hosts (ms02 — remote IDE, Kind, act / buildx)

**Docker:** one-time **Docker Engine CE** (buildx, compose) — `playbooks/docker_dev_engine.yml` — then routine **`dev_hosts.yml`** (sudoers, docker group / `daemon.json`, firewall, Kind). Do **not** use Ubuntu `docker.io` on `ms02` if you need the same stack as [docker-dev-host.md](docs/docker-dev-host.md).

```bash
ansible-playbook playbooks/docker_dev_engine.yml -l ms02   # once, after cleanup per docker-cleanup.md if needed
ansible-playbook playbooks/dev_hosts.yml -l ms02
```

## Layout

- `inventory/hosts.yml` – groups `sparks`, `dev_hosts`.
- `inventory/group_vars/sparks.yml` – sparks (`nvidia`, NCCL paths, vLLM, **`spark_provision_*`** toggles).
- `playbooks/provision_sparks.yml` – **canonical Spark provisioning** (role **`spark_provision`**); see [docs/provision_sparks.md](docs/provision_sparks.md).
- `playbooks/nccl_sparks.yml` – NCCL build (as `nvidia`), NCCL test (separate from provision).
- `playbooks/dev_hosts.yml` – sudoers + Docker + firewall + Kind on dev_hosts.
- `roles/spark_provision/` – orchestrates Spark phases (sudoers, apt, docker, cuda, vllm, hf, optional docker vLLM, diagnostics).
- `roles/sudoers/` – users; optional per-user `groups:`; sudoers fragments.
- `roles/vllm/` – venv/pip as `vllm_run_user`, systemd **User=** when set, stacked Ray templates.
- `docs/provision_sparks.md` – phase list, variables, tags.
- `docs/vllm-multi-node.md` – multi-node Ray / vLLM.
- `docs/spark-parity-pre-stack.md` – align **nvidia2** with **nvidia1** (Ansible/Docker/vLLM) before stacking.
- `docs/MIGRATION-root-to-nvidia.md` – upgrading from older root-based trees.
- `docs/docker-dgx.md` – why Sparks skip `docker.io` and use the platform Docker stack.
- `docs/docker-cleanup.md` – probe and cleanup mixed Docker installs; enable **`spark_provision_diagnostics`** for read-only probes (Docker + HF cache).
- `docs/docker-dev-host.md` – ms02: Docker CE + buildx + act/Kind vs Sparks.
- `docs/EXO-topology-draft.md` – draft: MacBook → **ms02**; EXO **Spark + Mac Studio** (prefill/decode split per [EXO blog](https://blog.exolabs.net/nvidia-dgx-spark/)); **cylon-local-infra** = Sparks/`ms02` Ansible.
- `docs/hf-cache-sparks.md` – Hugging Face cache layout on Sparks; diagnostics phase when enabled.
- `playbooks/docker_dev_engine.yml` – install Docker CE + plugins on dev hosts.

## References

- [Connect two Sparks](https://build.nvidia.com/spark/connect-two-sparks/stacked-sparks)
- [NCCL for Two Sparks](https://build.nvidia.com/spark/nccl/stacked-sparks)
- [vLLM installation (GPU)](https://docs.vllm.ai/en/latest/getting_started/installation/gpu/)
- [vLLM multi-node serving](https://docs.vllm.ai/en/latest/examples/online_serving/multi-node-serving.html)
