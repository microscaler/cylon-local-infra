# Migration: root-based NCCL/vLLM → `nvidia` user

If you previously ran this repo when NCCL lived under `/root/nccl` and vLLM used `/root` for `HF_HOME` and systemd:

## On each Spark

1. **Stop vLLM** (if running): `sudo systemctl disable --now vllm`
2. **Optional reclaim disk** after backing up anything you need:
   - `sudo rm -rf /root/nccl /root/nccl-tests`
   - Remove old venv if you are recreating under the same path: `sudo rm -rf /opt/vllm/venv` (Ansible will recreate as `nvidia`)
3. **Apply current playbooks** (from repo root):
   ```bash
   ansible-playbook playbooks/provision_sparks.yml
   ansible-playbook playbooks/nccl_sparks.yml -e nccl_test_user_password=YOUR_PASSWORD   # first interconnect key exchange
   ```
4. **SSH for daily use**: `ssh nvidia@<spark-host>` (Ansible may still use `root` unless you reconfigure `inventory/hosts.yml`).

## What changed

- NCCL / nccl-tests: **`/home/nvidia/nccl`** and **`/home/nvidia/nccl-tests`**
- vLLM: venv remains **`/opt/vllm/venv`** (owned by **`nvidia`**), caches **`/home/nvidia/.cache/huggingface`**
- `nvidia` has **passwordless sudo** for `apt` / `systemctl` only (see `inventory/group_vars/sparks.yml`)
