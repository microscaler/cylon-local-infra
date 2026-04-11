# PRD: Non-root (`nvidia`) operations, infra completion, EXO readiness, and remote dev (`ms02`)


| Field       | Value                                                            |
| ----------- | ---------------------------------------------------------------- |
| **Status**  | In progress (nvidia runtime + vLLM unit converged on Sparks)    |
| **Owner**   | Platform / infra (Microscaler)                                   |
| **Repo**    | `cylon-local-infra` (Ansible)                                    |
| **Related** | [vllm-multi-node.md](vllm-multi-node.md), [README](../README.md) |


---

## 1. Executive summary

Complete the **GB10 Spark pair** and **Minisforum dev host (`ms02`)** automation so that **day-to-day GPU and cluster operations run as a dedicated `nvidia` user** (not `root`), consistent with **NVIDIA DGX Spark documentation** and sane security practice. **Development** uses a **MacBook** (or similar) connecting to **`ms02`** as the **remote dev host** (Kind, Docker, act — see [docker-dev-host.md](docker-dev-host.md)), optionally via **Tailscale** or another overlay for mobility. A **future Mac Studio** is intended as a **node in the EXO cluster**, **not** as a replacement desktop/IDE for that workflow — see [EXO-topology-draft.md](EXO-topology-draft.md). Keep architecture compatible with a future **EXO**-centric deployment.

---

## 2. Background

### 2.1 Current state

- Ansible uses `**ansible_user: root`** on sparks and dev hosts.
- **NCCL** is built under `**/root/nccl`** and `**/root/nccl-tests**`, with permissions relaxed so a separate test user can run `mpirun` — workable but **not** aligned with NVIDIA’s published `**~/nccl` / `$HOME/nccl-tests`** pattern.
- **vLLM** systemd unit and caches assume `**/root`** (`WorkingDirectory`, `HF_HOME`).
- **Official NVIDIA flows** ([NCCL for Two Sparks](https://build.nvidia.com/spark/nccl/stacked-sparks), [Spark Stacking](https://docs.nvidia.com/dgx/dgx-spark/spark-clustering.html)) assume a **non-root user** (commonly `nvidia`) for discovery, SSH, and NCCL tests.

### 2.2 Problem statement

Root-owned build trees and root-run services increase **operational risk**, complicate **auditing**, and **diverge from vendor docs**, making support and future automation (EXO, remote dev) harder.

---

## 3. Goals

### 3.1 Primary goals

1. `**nvidia` user as default runtime identity** on Spark hosts for:
  - NCCL / nccl-tests **build and run** paths under `/home/nvidia/...` (or agreed home root).
  - **SSH key exchange** and **discover-sparks**-style workflows **as `nvidia`** (same UID story on both nodes).
2. **vLLM + Ray** runnable **as `nvidia`**: venv, caches (`HF_HOME`, `XDG_CACHE_HOME`), systemd `User=nvidia` (or documented equivalent).
3. **Ansible** may still use **privileged tasks** where required, but **idempotent ownership** and **no long-term reliance** on world-readable `/root` trees.
4. **Passwordless sudo for `nvidia`** (see §5) scoped to **documented** needs (e.g. `apt`, `systemctl` for approved units, Docker if policy allows) — **not** unbounded `NOPASSWD:ALL` unless explicitly approved as a temporary measure.
5. **Development and access pattern**:
  - **Remote dev:** **`ms02`** runs Kind, Docker CE, and related tooling; developers connect from a **MacBook** (or similar) over **SSH**, often using **Tailscale** (or VPN) so work is reachable from anywhere.
  - **GPU / Sparks:** Same **`nvidia`** user model on Sparks; **Ansible** runs from the **MacBook** (or CI) against inventory — not assumed to require a future **Mac Studio** as controller.
  - **Future Mac Studio:** Treat as an **EXO cluster node** (see [EXO-topology-draft.md](EXO-topology-draft.md)), **not** as the primary IDE or as a substitute for **`ms02`** unless product changes that.
6. **EXO readiness (long-term)**:
  - **Stable service identities** (`nvidia` or future service account), **predictable ports**, **documented APIs** (vLLM OpenAI-compatible endpoint, Ray), and **no hard-coded root paths** in playbooks.
  - PRD assumes **EXO** will integrate with these hosts or successors; exact EXO topology TBD — requirements below are **interfaces and hygiene**, not EXO implementation.

### 3.2 Non-goals (this PRD)

- Full **EXO** product implementation or **Mac Studio** hardware provisioning.
- Replacing **Ansible** with another provisioner (Kubernetes-only, etc.) — may be a later phase.
- **Slurm** or **enterprise SSO** unless later phase explicitly adds them.

---

## 4. Users and personas


| Persona                     | Need                                                                          |
| --------------------------- | ----------------------------------------------------------------------------- |
| **Platform engineer**       | Reproducible Ansible runs, clear sudo boundaries, easy rotation of keys.      |
| **ML / inference engineer** | `nvidia` SSH, vLLM/Ray URLs, NCCL sanity without root.                        |
| **EXO (future)**            | Stable users, ports, TLS/offload story — no root-only assumptions in configs. |


---

## 5. `nvidia` user and sudo policy

### 5.1 Account

- **Username:** `nvidia` (match DGX convention; document if a different name is used).
- **Present on:** both Spark hosts; **same UID/GID optional** but **same username** required for SSH and NVIDIA scripts.
- **Groups:** at minimum `docker` if Docker socket access is required without sudo; `sudo` if using passwordless sudo.

### 5.2 Passwordless sudo

**Requirement:** Support **passwordless sudo** for `nvidia` **where needed for automation and day-2 ops**, with **minimal surface**.

**Recommended approach (choose one in implementation):**

- **Option A (preferred):** **Targeted** `/etc/sudoers.d/` rules, e.g.:
  - `apt-get` / `apt` for package installs (or restrict to Ansible-driven paths).
  - `systemctl` for **named** units only (`vllm`, `docker`, etc.) if unit management stays partially manual.
  - Optional: `/usr/bin/docker` if not using `docker` group.
- **Option B (interim):** `nvidia ALL=(ALL) NOPASSWD:ALL` for **bootstrap window only**, with a **ticketed follow-up** to narrow to Option A.

**Out of scope for sudo:** sharing `nvidia` password with humans; prefer **SSH keys** and **sudo** for escalations.

### 5.3 Ansible connection model

- **Preferred end state:** `ansible_user: nvidia` with `**become: true`** for privileged tasks **OR** a split inventory (`nvidia` + `become` where needed).
- **Migration:** document one-time steps if existing hosts are root-only today (create `nvidia`, sync keys, run playbooks).

---

## 6. Technical requirements

### 6.1 Inventory and variables

- Add `**nvidia_user`** (or reuse `**sudoers_users**` pattern) with `**name: nvidia**` and explicit `**home**` if not default.
- Replace `**nccl_home` / `nccl_tests_home**` defaults from `/root/...` to `**/home/nvidia/nccl**` and `**/home/nvidia/nccl-tests**` (or `~nvidia/...` resolved in playbooks).
- **vLLM:** `vllm_venv_path` under `/home/nvidia/...` or `/opt/vllm` **owned by `nvidia`** (team decision: user home vs `/opt` with `nvidia` ownership).

### 6.2 NCCL / nccl-tests role

- Clone and **build as `nvidia`** (e.g. `become_user: nvidia` with correct `HOME` and CUDA paths).
- **Remove** reliance on `**chmod` of `/root`** for test execution.
- `**nccl_test_user**` default → `**nvidia**` (or alias variable for clarity).
- `**nccl_sparks.yml`:** key exchange and `mpirun` remain **as the same user** that owns the trees.

### 6.3 CUDA / drivers

- System CUDA under `/usr/local/cuda` remains **system-wide**; `**nvidia` must read** libs and include paths (standard world-readable layout).
- Any **NVIDIA repo / apt** steps stay **root** via Ansible `become`; no requirement for `nvidia` to run `apt` manually if Ansible owns installs.

### 6.4 vLLM and Ray

- Systemd unit: `**User=nvidia`**, `**Group=nvidia**`, `**WorkingDirectory=/home/nvidia**` (or chosen dir), `**Environment=HF_HOME=...**` under `nvidia` home.
- **Multi-node:** document `**VLLM_HOST_IP`** / interconnect usage for `nvidia`; consider **Ray `symmetric-run`** in docs or a small wrapper for parity with [vLLM multi-node](https://docs.vllm.ai/en/latest/examples/online_serving/multi-node-serving.html) and [Ray symmetric-run](https://vllm.ai/blog/ray-symmetric-run).
- **First Spark / leader** selection remains deterministic (inventory order or explicit group).

### 6.5 Dev host (`ms02` / Minisforum)

- Decide whether **dev host** uses `**nvidia`** for parity or a separate `**casibbald**` / dev user — **recommend:** keep **human dev user** for IDE SSH, add `**nvidia` only if** GPU workloads run there; otherwise **do not** force `nvidia` on non-Spark hosts.
- **Firewall / Kind / Docker** unchanged in scope except **sudo** and **non-root** consistency per host policy.

### 6.6 Documentation

- Update **README** paths (`cd ansible` vs repo root if still wrong).
- **README:** default SSH examples → `**nvidia@nvidia1`** where appropriate.
- `**docs/vllm-multi-node.md`:** all examples `**source` as `nvidia`**, env vars for interconnect IPs.

---

## 7. Development usage pattern (MacBook → ms02; Mac Studio separate)


| Phase | Client | Workload placement | Notes |
| ----- | ------ | ------------------ | ----- |
| **Standard** | **MacBook** (or laptop) | **SSH to `ms02`** for Kind, Docker, act; **SSH to Sparks** as needed; **Ansible** from MacBook to inventory | **`nvidia`** on Sparks; **`ms02`** per [docker-dev-host.md](docker-dev-host.md); optional **Tailscale**/VPN. |
| **Future** | **MacBook** unchanged for dev access | Same **`ms02`** remote-dev pattern unless revised | **Mac Studio** is an **EXO cluster node**, not the primary IDE — [EXO-topology-draft.md](EXO-topology-draft.md). |


**Requirements:**

- **Document** canonical endpoints: vLLM base URL, Ray port, Kind API server from `ms02`.
- **Avoid** machine-specific paths in playbooks (no Mac paths on servers).

---

## 8. EXO readiness (interfaces)

Until EXO is specified in detail, deliver **stable contracts**:

- **Inference:** OpenAI-compatible HTTP(S) on **leader Spark**; port `**vllm_api_port`** (default 8080).
- **Coordination:** Ray `**vllm_ray_port`** (default 6379) on interconnect/management as designed.
- **Identity:** Services run as `**nvidia`**, not root.
- **Observability hooks (optional phase 2):** document **log locations** (`journalctl -u vllm` as `nvidia`-owned unit) for EXO agents.

---

## 9. Milestones


| Milestone         | Description                                                                   | Exit criteria                                       |
| ----------------- | ----------------------------------------------------------------------------- | --------------------------------------------------- |
| **M1**            | `nvidia` user + SSH + sudo policy documented and applied on **one** Spark     | SSH as `nvidia`, sudo rules in place                |
| **M2**            | NCCL role + playbook use `**/home/nvidia/...`**, builds as `nvidia`           | `all_gather_perf` passes **without** `/root` chmod  |
| **M3**            | vLLM systemd + venv + caches as `**nvidia`**                                  | API test passes; reboot survives                    |
| **M4**            | Second Spark + inventory parity                                               | Both nodes match; multi-node doc validated manually |
| **M5**            | README + migration guide from root-based setup                                | Another engineer can reproduce                      |
| **M6 (optional)** | Ray **symmetric-run** or upstream **multi-node script** wired in docs/scripts | Documented one-command path for 2-node              |

### 9.1 Implementation checklist (operator)

Track these in order when moving hosts to **`nvidia`** + current vLLM layout:

1. **Inventory / vars:** `spark_runtime_user: nvidia`, `vllm_run_user`, `vllm_hf_home`, `vllm_torch_extra_index_url` (Sparks: **`cu130`** to match CUDA 13 runtime), `vllm_package` (e.g. **`vllm>=0.19`** for cu130 stack), `cuda_extra_packages` including **`cuda-cudart-12-8`** (vLLM aarch64 **`_C`** links **`libcudart.so.12`**).
2. **CUDA role:** Converge so **`cuda-cudart-12-8`** is installed (alongside CUDA 13 toolkit) on each Spark that runs vLLM.
3. **vLLM role:** Pip installs **PyTorch from the cu130 index first**, then **vLLM** (avoids CPU-only torch from PyPI).
4. **Serve / systemd:** Deploy **`serve.yml`** so the wrapper sets **`LD_LIBRARY_PATH`** to **`…/site-packages/torch/lib`** first, then the detected **`libcudart.so.12`** directory, then system CUDA libs. Unit has **`User=nvidia`**, **`WorkingDirectory=/home/nvidia`**, **`Environment=HF_HOME=…/nvidia/.cache/huggingface`**.
5. **Smoke test:** `systemctl restart vllm` on the leader; **`journalctl -u vllm -f`** until the API is ready (first start may compile a long time). **`curl http://127.0.0.1:8080/v1/models`** on the host.
6. **GPU generation:** GB10 may log **CUDA capability 12.1** vs PyTorch “max 12.0” — if inference never becomes ready, plan a **newer PyTorch / vLLM** build for Blackwell or vendor guidance (outside pure Ansible).
7. **HF CLI:** **`provision_sparks.yml`** with **`spark_provision_hf: true`** (role **`hf_spark`**) for **`hf`** in **`/opt/vllm/venv`** (prefer **`hf`**; **`huggingface-cli`** is deprecated by Hugging Face) and **`/etc/profile.d/cylon-nvidia-vllm.sh`**.

---

## 10. Success criteria

1. **No production dependency** on root-owned `**/root/nccl`** trees for NCCL tests.
2. **vLLM** runs as `**nvidia`** with persistent cache under `**nvidia**` home (or approved `/opt` ownership).
3. **Ansible** can converge from clean Ubuntu + NVIDIA drivers to **passing NCCL test + vLLM smoke test** with `**nvidia`** as runtime user.
4. **Access doc:** MacBook → **`ms02`** and Sparks endpoints remain stable without playbook changes when overlay IPs shift (**Tailscale**/VPN); **Mac Studio** as EXO node does not replace **`ms02`** dev role unless explicitly changed.

---

## 11. Risks and mitigations


| Risk                          | Mitigation                                           |
| ----------------------------- | ---------------------------------------------------- |
| **Broad NOPASSWD sudo**       | Prefer targeted sudoers; time-box full NOPASSWD      |
| **UID mismatch across nodes** | Same username; document optional UID sync            |
| **Existing root-based state** | Migration playbook or manual cleanup of `/root/nccl` |
| **Docker socket**             | Prefer `docker` group for `nvidia` over sudo docker  |


---

## 12. Open questions

1. **EXO:** Exact services on **Sparks** vs **Mac Studio (EXO node)** vs **`ms02`** — confirm when EXO architecture is fixed ([EXO-topology-draft.md](EXO-topology-draft.md)).
2. **vLLM install location:** `/home/nvidia/vllm-venv` vs `/opt/vllm` owned by `nvidia`.
3. **Minisforum `ms02`:** Single shared dev user vs `nvidia` — confirm GPU use on that box.
4. **TLS:** Termination at vLLM vs reverse proxy — defer unless EXO requires it in M1–M4.

---

## 13. References

- [NVIDIA — NCCL for Two Sparks](https://build.nvidia.com/spark/nccl/stacked-sparks)
- [NVIDIA — Spark Stacking](https://docs.nvidia.com/dgx/dgx-spark/spark-clustering.html)
- [vLLM — Multi-node serving](https://docs.vllm.ai/en/latest/examples/online_serving/multi-node-serving.html)
- [vLLM blog — Ray symmetric-run](https://vllm.ai/blog/ray-symmetric-run)
- [NVIDIA/dgx-spark-playbooks](https://github.com/NVIDIA/dgx-spark-playbooks) (reference assets)

