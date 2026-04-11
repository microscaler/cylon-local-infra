# PRD: Two-node stacking with nvidia2 (Ray + distributed vLLM)


| Field       | Value                                                                                                                                                                                                            |
| ----------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Status**  | In progress — `provision_sparks.yml` (`spark_provision_vllm_stack`) + systemd units; validate **M3** on hardware, then set **Accepted**                                                                                                        |
| **Owner**   | Platform / infra (Microscaler)                                                                                                                                                                                   |
| **Repo**    | `cylon-local-infra` (Ansible)                                                                                                                                                                                    |
| **Related** | [spark-parity-pre-stack.md](spark-parity-pre-stack.md) (**run first**), [vllm-multi-node.md](vllm-multi-node.md), [PRD-nvidia-user-exo-transition.md](PRD-nvidia-user-exo-transition.md), [README](../README.md) |


---

## 1. Executive summary

**Today:** The interconnect between **nvidia1** and **nvidia2** is up and reachable (link-local `169.254.0.0/16`), and **vLLM** can serve a model from **nvidia1** alone via the OpenAI-compatible API (`provision_sparks.yml` + `vllm.service` when single-node mode is enabled). That is **single-node** inference — **nvidia2 does not participate** in the running job.

**Goal:** Declare **stacking “working”** in the product sense: **both Sparks** participate in **one** Ray cluster, and **one** vLLM serve uses **tensor parallelism (or pipeline parallelism) across both GPUs** with traffic on the **interconnect** where appropriate. Operators can **verify** health (SSH, Ray cluster size, NCCL sanity) without ad-hoc debugging.

**This PRD** captures **what must be true before you start**, **gaps in current automation**, **milestones**, and **success criteria** so implementation can be sequenced without rediscovering prior issues (Ray version skew, stale Ray head, missing `nvidia`↔`nvidia` SSH trust).

**Prerequisite:** **Ansible/Docker/CUDA/vLLM parity** for **nvidia2** with **nvidia1** is a separate gate — follow [spark-parity-pre-stack.md](spark-parity-pre-stack.md) (ordered playbooks, parity matrix, verification) **before** Ray stacking steps.

---

## 2. Background and problem statement

### 2.1 Current state (observed)


| Layer                                    | State                                                                                                                                                                                                           |
| ---------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Physical / L3 interconnect**           | QSFP link **UP**; ping between link-local IPs works (sub-ms typical).                                                                                                                                           |
| **Single-node vLLM API**                 | **Healthy on leader** when engine starts (requires `**python3-dev`** for Triton; see [vllm-multi-node.md](vllm-multi-node.md) prerequisites).                                                                   |
| **Passwordless SSH `nvidia` → `nvidia`** | Often **incomplete**: hostnames may not resolve; **known_hosts** / first-connection trust not established for Spark-to-Spark over LAN IPs.                                                                      |
| **Ray cluster (2 nodes)**                | **Not verified**: worker on **nvidia2** may be absent; **Ray CLI vs running GCS** may **version-mismatch** (e.g. cluster 2.53 vs venv 2.54).                                                                    |
| **Ansible `provision_sparks.yml` (vLLM phase)** | Installs vLLM+Ray on **both** hosts and starts `**vllm.service` only on the first Spark** with **single-GPU-style** wrapper when enabled (no `--tensor-parallel-size 2` / no Ray-orchestrated multi-node in the unit today). |


### 2.2 Problem statement

**Documentation describes** manual Ray head/worker + `vllm serve` with `--tensor-parallel-size 2` ([vllm-multi-node.md](vllm-multi-node.md)), but **automation and operational hygiene** (SSH trust, pinned Ray versions, worker lifecycle, systemd story) are **not converged**. Without that, **nvidia2 cannot be relied on** as part of the inference plane.

---

## 3. Goals and non-goals

### 3.1 Primary goals

1. **Trust and naming:** `**nvidia`** can SSH **passwordlessly** to the peer Spark using **stable names** (inventory hostnames via `/etc/hosts` or DNS) and **no interactive host-key prompts** in normal operation.
2. **Ray cluster:** A **single Ray cluster** with **exactly two alive nodes** (nvidia1 head + nvidia2 worker), using **one Ray version** everywhere (venv + any long-lived processes). `**ray status`** / `**ray list nodes`** (or equivalent) succeeds from the **same** venv used for vLLM.
3. **Distributed vLLM:** Leader runs **OpenAI-compatible API** with `**--distributed-executor-backend ray`** and `**--tensor-parallel-size 2`** (or an agreed PP/TP split documented for the target model class).
4. **Observability:** Repeatable **checks** (Ansible ad-hoc, small playbook, or documented one-liners) for: interconnect ping, Ray node count, optional **NCCL** `all_gather` sanity (existing `nccl_sparks.yml` path).
5. **Documentation + vars:** Pin `**ray`** version in `**inventory/group_vars/sparks.yml`** (or role defaults), document **startup order** (worker-first vs head-first) and **recovery** (reboot, stale GCS).

### 3.2 Non-goals (this phase)

- **NGC container** parity with NVIDIA’s `run_cluster.sh` (may be a later option).
- **Slurm**, **Kubernetes** scheduling, or **HA** for Ray head (single head on nvidia1 is acceptable unless requirements change).
- Changing **LAN firewall** beyond what already exists for **22 / 6379 / 8080** (already in [sparks.yml](../inventory/group_vars/sparks.yml)).
- **Production** TLS termination in front of vLLM (out of scope unless a separate PRD adds it).

---

## 4. What is needed so we can start (pre-flight checklist)

Use this before writing new playbooks or changing units. **Complete [spark-parity-pre-stack.md](spark-parity-pre-stack.md) first** so **nvidia2** matches **nvidia1** for Docker, firewall, venv, and (as needed) NCCL.


| #     | Requirement                                                                                                               | How to verify                                                                           | Owner |
| ----- | ------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------- | ----- |
| **A** | Both hosts in Ansible `sparks` with correct **management IPs** (e.g. `192.168.1.104`, `192.168.1.229`)                    | `ansible sparks -m ping`                                                                | Infra |
| **B** | `**nvidia`** exists on both; **sudoers** role applied if needed                                                           | `ansible-playbook playbooks/provision_sparks.yml` (sudoers phase) or existing user play                              | Infra |
| **C** | **Interconnect** interface name matches `**nccl_interface`** (e.g. `enp1s0f0np0`) and **UP** on both                      | `ip -br a`, `ibdev2netdev` per NVIDIA docs                                              | Infra |
| **D** | **Link-local IPs** assigned on interconnect                                                                               | `ip -4 addr show dev <nccl_interface>` on each node                                     | Infra |
| **E** | **Passwordless SSH** between Sparks **as `nvidia`** (keys + **known_hosts** or `StrictHostKeyChecking` policy documented) | `ssh nvidia@<peer> hostname` from each side                                             | Infra |
| **F** | **NCCL key exchange** completed if using `**nccl_sparks.yml`** for MPI tests                                              | `ansible-playbook playbooks/nccl_sparks.yml -e nccl_test_user_password=...` (first run) | Infra |
| **G** | **vLLM venv** present on **both** nodes with **matching** `**ray`** and `**vllm`**                                        | Same playbook revision; pin Ray in vars (see §6)                                        | Infra |
| **H** | **No stale Ray** head binding **6379** on a different Ray version than venv                                               | Inspect processes / restart policy; align versions before joining worker                | Infra |
| **I** | **HF cache** on worker if models are large (optional for tiny models)                                                     | `HF_HOME` under `/home/nvidia/.cache/huggingface` on **nvidia2**                        | ML    |


**Minimum to “start” multi-node manually:** **A–E** and **G** with version alignment (**H**). **F** is strongly recommended to prove the interconnect for collectives before debugging vLLM. **I** matters once you leave TinyLlama-class models.

---

## 5. Gaps to close (engineering backlog)


| ID     | Gap                                                                                                     | Proposed direction                                                                                                                                                                                                                                                |
| ------ | ------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **G1** | `**vllm.service`** today targets **single-node** `api_server` (no TP=2 / Ray backend in unit).          | Add **either** a **separate** unit (e.g. `vllm-cluster.service`) **or** group vars (`vllm_tensor_parallel_size`, `vllm_distributed_backend`) consumed by `run-vllm-api.sh.j2` **only when** multi-node mode is enabled; document **manual** flow until automated. |
| **G2** | **Ray head** not lifecycle-managed like vLLM (may be leftover `**ray start --head`** from manual runs). | Define **single owner** for Ray on leader: systemd **template** or documented `**ExecStartPre`** cleanup + `**ray start --head --node-ip-address=<interconnect>`** with `**--port={{ vllm_ray_port }}**`.                                                         |
| **G3** | **Ray worker on nvidia2** is manual (`ray start --address=... --block`).                                | `**systemd` user service** on **second** Spark (after head is defined), `**Wants`/ordering** documented; or Ansible **handler** + **serial** startup order.                                                                                                       |
| **G4** | **Ray version mismatch** between CLI and running GCS.                                                   | **Pin `ray==x.y.z`** in pip (same as [vllm role](../roles/vllm/tasks/main.yml) install) and **restart** any old head; optionally `**pip check`** in CI-style ad-hoc.                                                                                              |
| **G5** | **Hostnames**: `ssh nvidia@nvidia2` fails if DNS missing.                                               | `**/etc/hosts`** from Ansible for **management** and/or **interconnect** names (optional) — align with `nccl_sparks` / inventory.                                                                                                                                 |
| **G6** | **Operational validation** absent as code.                                                              | Small playbook or **role**: `ping interconnect`, `ray status` with `**RAY_ADDRESS`**, assert **2 nodes alive**.                                                                                                                                                   |


---

## 6. Technical requirements (architecture constraints)

- **Leader:** First host in sorted `groups['sparks']` (same convention as [provision_sparks.yml](../playbooks/provision_sparks.yml)) — today **nvidia1**.
- **Ray:** `**RAY_ADDRESS`** for clients/operators should use the **leader interconnect IP** and `**vllm_ray_port`** (default **6379**). Worker uses `**ray start --address=<leader_interconnect>:6379`**.
- **vLLM:** `**--distributed-executor-backend ray`**, `**--tensor-parallel-size 2`** for 1 GPU per node; set `**VLLM_HOST_IP**` to each node’s interconnect IP when Ray picks the wrong interface ([vllm-multi-node.md](vllm-multi-node.md)).
- **Firewall:** **6379** and **8080** (and **22**) already allowed; confirm **interconnect** is not accidentally blocked (typically link-local is unaffected by ufw on the same hosts — verify if custom rules exist).
- **Python build deps:** `**python3-dev`** must remain installed on both nodes (Triton / engine startup).

---

## 7. Milestones and success criteria


| Milestone | Deliverable                     | Success criterion                                                                                                               |
| --------- | ------------------------------- | ------------------------------------------------------------------------------------------------------------------------------- |
| **M0**    | Pre-flight §4 complete          | Checklist A–E, G green; **H** explicitly cleared or Ray restarted on pinned version                                             |
| **M1**    | SSH + hosts                     | `nvidia` SSH peer works **by hostname** from both nodes                                                                         |
| **M2**    | Ray 2-node                      | `ray` reports **2 alive nodes**; versions consistent                                                                            |
| **M3**    | vLLM 2-GPU                      | **One** chat completion against leader API succeeds with **TP=2** model load (model sized for 2× GB10 memory — pick per team)   |
| **M4**    | Automation (optional follow-on) | **systemd** (or playbook) brings up **head + worker + vllm** in documented order; **idempotent** redeploy                       |
| **M5**    | Docs                            | [vllm-multi-node.md](vllm-multi-node.md) updated with **pinned Ray**, **troubleshooting** for version skew, pointer to this PRD |


**Definition of Done (stacking “working”):** **M3** satisfied in a **repeatable** way (scripted or documented steps with no one-off manual pip fixes), and **M2** verified immediately before each demo / release.

---

## 8. Risks and dependencies


| Risk                                                      | Mitigation                                                                         |
| --------------------------------------------------------- | ---------------------------------------------------------------------------------- |
| **Stale Ray head** after upgrades                         | Stop old `ray` / `gcs_server` before joining worker; pin versions                  |
| **Model too large** for 2× device memory                  | Choose validation model explicitly; document OOM behavior                          |
| **Interface name drift** (`enp1s0f0np0` vs `enp1s0f1np1`) | Confirm with `ibdev2netdev`; override `nccl_interface`                             |
| **Operator error** (wrong order of start)                 | Document **worker-first** or **head-first** with retry behavior; add health script |


**Dependency:** Aligns with [PRD-nvidia-user-exo-transition.md](PRD-nvidia-user-exo-transition.md) — runtime user `**nvidia`**, paths under `**/home/nvidia`**, no reliance on root-only trees.

---

## 9. References

- [NVIDIA — Connect two Sparks](https://build.nvidia.com/spark/connect-two-sparks/stacked-sparks)
- [NVIDIA — vLLM stacked Sparks](https://build.nvidia.com/spark/vllm/stacked-sparks)
- [vLLM — Multi-node serving](https://docs.vllm.ai/en/latest/examples/online_serving/multi-node-serving.html)
- Internal: [README.md](../README.md) — `nccl_sparks.yml`, `provision_sparks.yml`

---

## 10. Implementation checklist (for the next sprint)

- **[spark-parity-pre-stack.md](spark-parity-pre-stack.md):** Ansible/Docker/CUDA/vLLM (and optional NCCL) parity for **nvidia2** vs **nvidia1**; verification passes.
- **Host packages / NVIDIA repos:** run `ansible-playbook playbooks/provision_sparks.yml --tags apt` (e.g. `-e spark_apt_upgrade_mode=full`); **reboot if kernel or NVIDIA driver stacks changed**, then re-apply stack (`--tags vllm_stack` or full provision) as needed.
- Complete pre-flight table (§4); record **leader/worker interconnect IPs** in inventory or vault (not secrets — link-local).
- Run `**nccl_sparks.yml`** (with password once if needed) and confirm **all_gather** (or agreed test) passes over interconnect.
- Pin `**ray`** version in `**sparks.yml`**; reinstall venv on both nodes; **eliminate** old Ray head processes.
- Establish `**/etc/hosts`** or DNS for `**nvidia1` / `nvidia2`** on both Sparks.
- Manual dry-run: worker `**ray start**` → head `**ray start --head**` → `**vllm ... --tensor-parallel-size 2 --distributed-executor-backend ray**`; fix env (`VLLM_HOST_IP`) until stable.
- **G1–G3:** Implemented as `**ray-head.service`**, `**ray-worker.service**`, `**vllm-stacked.service**` via **`provision_sparks.yml`** (`spark_provision_vllm_stack`) (see [vllm-multi-node.md](vllm-multi-node.md)).
- **G6:** **`spark_provision_verify_ray`** + **`provision_sparks.yml --tags verify`** runs `**ray status**` from the leader venv with `**RAY_ADDRESS**` set to the leader interconnect.
- Update [vllm-multi-node.md](vllm-multi-node.md) with **version pin** and **troubleshooting** for Ray skew — **done** for pin + Ansible path; expand if new issues appear.

When **M3** is green on real hardware, move **Status** in the table at top of this doc to **Accepted** and link any follow-up tickets from the engineering backlog (§5).