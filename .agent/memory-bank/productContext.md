# Product Context

**Users:** Platform/infra operators running Ansible from a controller (laptop/CI) over SSH; GPU work on Sparks is done as **`nvidia`**, not root.

**Problems solved:**

- Repeatable cluster bring-up: users, Docker policy, CUDA, NCCL proof over interconnect, vLLM/Ray installs.
- Clear **pre-stack parity** story so `nvidia2` matches `nvidia1` before multi-node Ray/vLLM (`spark-parity-pre-stack.md`).
- **Stacked inference:** leader exposes OpenAI-compatible HTTP API; follower participates via Ray only (no second API port).

**Operator flows:**

1. Baseline: **`provision_sparks.yml`** — sudoers, docker, cuda, venv; optional single-node `vllm.service` on first sorted Spark when enabled.
2. Stacking: after parity checklist, same playbook with **`spark_provision_vllm_stack: true`** deploys `ray-head`, `ray-worker`, `vllm-stacked` and stops single-node `vllm` on the leader by default.
3. Verification: **`spark_provision_verify_ray`** + **`provision_sparks.yml --tags verify`** or manual `ray status` with `RAY_ADDRESS=<leader_ic>:6379`.

**Non-goals (current phase):** NGC `run_cluster.sh` parity, K8s/Slurm, HA Ray head, TLS in front of vLLM (unless a separate PRD adds them).
