# Project Brief

**Repository:** `cylon-local-infra` (path: `Workspace/microscaler/cylon-local-infra`)

**Purpose:** Idempotent **Ansible** automation for **DGX Spark** pairs (inventory group `sparks`: typically `nvidia1`, `nvidia2`) and separate **dev hosts** (e.g. `ms02`). Aligns with NVIDIA “stacked Sparks” / NCCL / vLLM documentation.

**Scope:**

- Sparks: Docker alignment with DGX image, **`nvidia`** runtime user + operators (sudoers), firewall (ufw), NCCL build/tests over **QSFP interconnect**, CUDA toolkit, **vLLM + Ray** in `/opt/vllm/venv`, single-node and **two-node stacked** (Ray head/worker + tensor-parallel vLLM).
- Dev hosts: Docker CE, Kind, tooling per `docs/docker-dev-host.md`.

**Primary docs:** `README.md`, `docs/spark-parity-pre-stack.md` (parity gate before stacking), `docs/vllm-multi-node.md`, `docs/PRD-spark-stacking-nvidia2.md`, `docs/PRD-nvidia-user-exo-transition.md`.

**Success criteria for “stacking” (product):** Two Sparks in one Ray cluster; one vLLM OpenAI API on the leader with `--tensor-parallel-size 2` and `--distributed-executor-backend ray`; interconnect used for coordination; repeatable Ansible path (see `playbooks/provision_sparks.yml`, `spark_provision_vllm_stack`).
