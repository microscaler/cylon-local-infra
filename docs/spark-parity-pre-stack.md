# Spark parity: nvidia2 aligned with nvidia1 (before stacking)

Run this **before** Ray multi-node / tensor-parallel work ([PRD-spark-stacking-nvidia2.md](PRD-spark-stacking-nvidia2.md)). The goal is **the same Ansible-managed state on both Sparks** for users, Docker, CUDA cleanup, firewall, NCCL trees, and the **vLLM venv** — not “nvidia1 is special” except where provisioning **intentionally** only touches the leader.

**Controller SSH:** Ansible connects **from your laptop** to each Spark as **`spark_ansible_user`** (default **casibbald**) with **`ansible_become: true`**. That is separate from **Spark↔Spark** SSH over the interconnect (NCCL/Ray). See [§ Permission denied](#permission-denied-when-running-ansible-from-your-mac) if `ansible sparks -m ping` fails.

---

## Runbook (copy-paste)

From the **repo root** (directory with `ansible.cfg`):

```bash
ansible sparks -m ping

# Base parity: sudoers, APT, Docker, CUDA apt cleanup, firewall, CUDA toolkit, vLLM venv (see docs/provision_sparks.md)
ansible-playbook playbooks/provision_sparks.yml --skip-tags vllm_test

# Optional but recommended: NCCL trees + Spark↔Spark SSH (omit password if probe already works)
# ansible-playbook playbooks/nccl_sparks.yml -e nccl_test_user_password='…'

ansible-playbook playbooks/nccl_sparks.yml --skip-tags nccl_test   # add interconnect test: omit --skip-tags

# Hugging Face CLI + model prefetch (when spark_provision_hf: true in sparks.yml)
# ansible-playbook playbooks/provision_sparks.yml --tags hf

# Read-only Docker + HF cache probes (when spark_provision_diagnostics: true)
# ansible-playbook playbooks/provision_sparks.yml --tags diagnostics

ansible sparks -m shell -a '/opt/vllm/venv/bin/python -c "import vllm, ray; print(vllm.__version__, ray.__version__)"' -b
```

**Catch up only nvidia2:** add `-l nvidia2` to each `ansible-playbook` (still run NCCL SSH/key logic if the worker lacks peer access).

---

## 1. Intentional differences (not drift)

| Item | nvidia1 (typical leader) | nvidia2 (typical worker) |
| ---- | ------------------------ | ------------------------- |
| **vllm.service** (OpenAI API) | Deployed when `vllm_sparks_deploy_single_node_service: true` (first host in sorted `sparks`). | Not deployed by default — multi-node uses Ray worker + vLLM on leader ([vllm-multi-node.md](vllm-multi-node.md)). |
| **HF model cache size** | Often larger (first downloads). | Same **HF_HOME** path; pre-download via **`provision_sparks.yml`** with **`spark_provision_hf`** ([hf-cache-sparks.md](hf-cache-sparks.md)). |
| **HTTP :8080** | Serves when `vllm.service` is up (single-node). | Not required for stacking; **6379** (Ray) matters for workers. |

Everything else in the parity matrix should **match** between nodes.

---

## 2. Parity matrix (what “healthy” means)

| Area | What to match | Primary playbook / role |
| ---- | -------------- | ------------------------ |
| **Inventory** | Both hosts in `sparks`; controller SSH as operator (**casibbald** default) + sudo — `inventory/group_vars/sparks.yml`. | `inventory/hosts.yml`; use `ansible_host:` if names don’t resolve. |
| **Users / sudo** | **nvidia** (+ operators), **login shell /bin/bash** for ensured users (sudoers role). | `playbooks/provision_sparks.yml` → `sudoers` |
| **CUDA apt** | No duplicate CUDA apt sources. | `cuda` **apt_cleanup** (provision phase) |
| **Docker** | Engine running; **nvidia** in `docker` group. | `playbooks/provision_sparks.yml` → `docker` |
| **Firewall** | ufw: **22, 6379, 8080** (+ LAN rules in `sparks.yml`). | `playbooks/provision_sparks.yml` → `firewall` |
| **CUDA toolkit** | Toolkit + extra cudart per `sparks.yml`. | `playbooks/provision_sparks.yml` → `cuda` |
| **vLLM + Ray** | `/opt/vllm/venv` owned by **nvidia**, same pins. | `playbooks/provision_sparks.yml` → `vllm` |
| **python3-dev** | Present for Triton. | `roles/vllm` |
| **NCCL** | Trees under `/home/nvidia/nccl` (+ tests) when play has run. | `playbooks/nccl_sparks.yml` |
| **SSH Spark↔Spark** | **nvidia** passwordless to peer (inventory hostnames). | `playbooks/nccl_sparks.yml` (probe + optional password) |
| **HF CLI** (optional) | **`hf`** in venv + profile.d (`huggingface-cli` deprecated upstream). | `playbooks/provision_sparks.yml` → `hf_spark` when **`spark_provision_hf`** |

---

## 3. Recommended convergence order

Prefer **`hosts: sparks`** (no `-l` unless catching up one node).

| Step | Command | Notes |
| ---- | ------- | ----- |
| **A** | `ansible-playbook playbooks/provision_sparks.yml --skip-tags vllm_test` | Sudoers + Docker + CUDA apt cleanup + APT + firewall + CUDA + vLLM (see toggles in `sparks.yml`). |
| **B** | `ansible-playbook playbooks/nccl_sparks.yml -e nccl_test_user_password=…` | **Only if** the playbook’s SSH probe (**nvidia** → peer over interconnect, remote `ls`) **fails**. |
| **C** | `ansible-playbook playbooks/nccl_sparks.yml` | Idempotent NCCL; use `--skip-tags nccl_test` to skip `all_gather_perf`. |
| **D** | `ansible-playbook playbooks/provision_sparks.yml --tags hf` | When **`spark_provision_hf: true`** — HF CLI + downloads in **hf_prefetch_models**. |

---

## 4. Read-only verification

| Check | Command |
| ----- | ------- |
| Ansible | `ansible sparks -m ping` |
| Docker + HF caches | `ansible-playbook playbooks/provision_sparks.yml --tags diagnostics` with **`spark_provision_diagnostics: true`** |

**On each host** (as operator with sudo, or root): `systemctl is-active docker`; `sudo ufw status verbose`; interconnect `ip -4 -o addr show dev enp1s0f0np0` (or your `nccl_interface`).

**Spark-to-Spark as nvidia:** `ssh nvidia@nvidia1 hostname` from nvidia2 and reverse — no password.

**venv versions (both should match):**

```bash
ansible sparks -m shell -a '/opt/vllm/venv/bin/python -c "import vllm, ray; print(vllm.__version__, ray.__version__)"' -b
```

---

## 5. When parity is “good enough” for stacking

- [ ] **A** completed for both hosts (or **-l nvidia2** catch-up) with `failed=0`.
- [ ] **C** run at least once without `--skip-tags` if you want **NCCL interconnect** proof.
- [ ] Docker up; **vllm** + **ray** versions match on both (see §4).
- [ ] Firewall allows **6379** on both.
- [ ] **nvidia@** ↔ peer SSH by inventory hostname; interconnect ping OK.

Then: [vllm-multi-node.md](vllm-multi-node.md), [PRD-spark-stacking-nvidia2.md](PRD-spark-stacking-nvidia2.md).

### Permission denied when running Ansible from your Mac

- **Cause:** No SSH key for **ansible_user** (**spark_ansible_user**, default **casibbald**), or you expected **root**.
- **Fix:** `ssh-copy-id casibbald@nvidia1` (and **nvidia2**), or `-e spark_ansible_user=root` if root has your key.
- **Not the same:** NCCL’s Spark→Spark probe runs **on each Spark**; it does not replace **laptop → Spark** SSH.

---

## 6. References

- [README](../README.md)
- [provision_sparks.md](provision_sparks.md)
- [MIGRATION-root-to-nvidia.md](MIGRATION-root-to-nvidia.md)
- [docker-dgx.md](docker-dgx.md)
