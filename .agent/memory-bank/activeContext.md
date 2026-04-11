# Active Context

**Last updated:** 2026-04-09 — single Spark entry playbook **`playbooks/provision_sparks.yml`** (role **`spark_provision`**); legacy per-task playbooks removed.

**Recent implementation focus:**

- **PRD `docs/PRD-spark-stacking-nvidia2.md`:** Ansible-driven two-node **Ray + distributed vLLM** via **`provision_sparks.yml`** (`spark_provision_vllm_stack`), templates under `roles/vllm/templates/` (`ray-head`, `ray-worker`, `run-vllm-api-stacked`, `vllm-stacked.service`), task files `stack_ray_head.yml`, `stack_ray_worker.yml`, `stack_vllm.yml`.
- **Inventory:** `inventory/group_vars/sparks.yml` pins **`vllm_ray_package`** (e.g. `ray==2.54.0`); adjust cluster-wide and re-run **`provision_sparks.yml`** if the pin changes.
- **Ansible stall fix:** `stack_vllm.yml` uses **`no_block: true`** on the `systemd` task for `vllm-stacked` so the play does not block on long `systemctl start` / `TimeoutStartSec` windows; see `docs/vllm-multi-node.md` troubleshooting section.

**Conventions:**

- **Leader:** first host in **`groups['sparks'] | sort`** (same as **`provision_sparks.yml`** / vLLM phases).
- **Interconnect facts:** role sets `spark_interconnect_ip` from `nccl_interface`; `spark_ray_head_socket` = leader interconnect + `vllm_ray_port` (default 6379).

**Open / validate on hardware:** PRD **M3** — successful chat completion against stacked API with TP=2; then PRD Status → Accepted.

**Parity maintenance:** **`provision_sparks.yml`** with **`spark_apt_upgrade_mode=full`** has been run by operators — if kernel/NVIDIA stacks changed, **reboot** and **re-run `provision_sparks.yml --tags vllm_stack`** (or full provision) before M3 validation.

**EXO topology (draft):** **`docs/EXO-topology-draft.md`** — aligns with [EXO Labs DGX Spark + Mac Studio post](https://blog.exolabs.net/nvidia-dgx-spark/): **prefill** on Spark, **decode** on Mac Studio, layer-wise KV streaming; **MacBook → ms02** for dev. **`cylon-local-infra`** = Ansible on Sparks/`dev_hosts`, not EXO runtime.

**Sudoers (nvidia):** `inventory/group_vars/sparks.yml` grants NOPASSWD for **`journalctl`** (and existing apt/systemctl), adds supplementary group **`systemd-journal`**. **`provision_sparks.yml`** includes **`sudoers`** in phase order; re-run after pulling changes.
