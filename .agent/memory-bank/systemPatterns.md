# System Patterns

**Configuration hierarchy:** `inventory/hosts.yml`, `inventory/group_vars/sparks.yml`, `inventory/group_vars/dev_hosts.yml`; override with `-e`.

**Ansible layout:**

- **Playbooks (Sparks):** **`playbooks/provision_sparks.yml`** (canonical); **`playbooks/nccl_sparks.yml`** (NCCL / interconnect tests — separate multi-play).
- **Playbooks (non-Spark):** **`playbooks/dev_hosts.yml`**, **`playbooks/docker_dev_engine.yml`**.
- **Roles:** `roles/spark_provision` (orchestration), `roles/vllm`, `roles/cuda`, `roles/firewall`, `roles/sudoers`, `roles/nccl_sparks`, `roles/spark_apt`, `roles/spark_diagnostics`, etc.
- **vLLM role:** `tasks/main.yml` (install), `tasks/serve.yml` (single-node service), `tasks/api_runtime_facts.yml` (libcudart / torch lib paths), stacked tasks; templates for wrappers and units.

**Spark conventions:**

- Controller SSH user (`ansible_user`, often operator with sudo) ≠ Spark↔Spark SSH as `nvidia` over interconnect (NCCL/Ray).
- **`nccl_interface`** (e.g. `enp1s0f0np0`) must match the UP QSFP NIC; used for interconnect IP discovery in stack tasks.
- **Firewall:** TCP 22, `vllm_ray_port`, `vllm_api_port` from group vars.

**Idempotency:** Playbooks are written to be re-ran; stack phase optionally stops/disables `vllm.service` on leader when deploying `vllm-stacked`.

**Documentation:** Prefer updating `README.md` and `docs/*.md` when adding operator-visible behavior; PRD checklist references those artifacts.

**APT on Sparks:** Role **`spark_apt`** via **`provision_sparks.yml`** runs `apt` update + upgrade (vars in `group_vars/sparks.yml`: `spark_apt_upgrade_mode` etc.). Use `full` or `dist` when Ubuntu reports **kept back** packages (e.g. kernel/NVIDIA stacks), after reading release notes.
