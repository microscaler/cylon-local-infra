# Progress

## Done (interim snapshot)

- **Parity doc** `docs/spark-parity-pre-stack.md` — referenced as gate before stacking; updated for **`provision_sparks.yml`**.
- **Canonical Spark playbook:** `playbooks/provision_sparks.yml` + role **`spark_provision`** (sudoers, apt, docker, firewall, cuda, vllm, optional stack, HF, optional docker vLLM, diagnostics).
- **vLLM single-node path:** `roles/vllm` (`serve.yml`, `vllm.service`, `run-vllm-api.sh`) — toggled via **`spark_provision_vllm_serve`** / **`vllm_sparks_deploy_single_node_service`**.
- **Two-node stack automation:** same playbook, **`spark_provision_vllm_stack`** (interconnect facts → stop single-node `vllm` on leader → Ray head → Ray worker → `vllm-stacked`).
- **Verify Ray:** `spark_provision_verify_ray` + **`provision_sparks.yml --tags verify`** (`ray status` from leader with `RAY_ADDRESS`).
- **Docs:** `README.md`, `docs/provision_sparks.md`, `docs/vllm-multi-node.md`, `docs/PRD-spark-stacking-nvidia2.md` — align with single entry playbook where updated.
- **Ansible UX:** `roles/vllm/tasks/stack_vllm.yml` — `systemd` `no_block: true` for `vllm-stacked` start.
- **Ray pin:** `inventory/group_vars/sparks.yml` — `vllm_ray_package` set (verify version matches cluster policy).

## Not done / verify on cluster

- **PRD M3:** End-to-end validation — OpenAI chat completion through leader with TP=2 on real hardware; document any model-specific limits (memory).
- **PRD top status:** Move to **Accepted** after M3 and any follow-up tickets from §5 backlog.
- Optional: assert two alive nodes in `ray status` output when **`spark_provision_verify_ray`** runs.

## Next actions when resuming

1. **PRD M3 (definition of done):** One successful **OpenAI chat completion** against the **leader** `vllm-stacked` API with **TP=2** (model sized for 2× device memory). Example: `curl` to `http://<leader>:8080/v1/chat/completions` with `vllm_default_model` from `sparks.yml`.
2. After **APT full-upgrade** (especially kernel/NVIDIA): **reboot** Sparks if required, then `ansible-playbook playbooks/provision_sparks.yml` (or `--tags vllm_stack` if only stack units need refresh) to bring **ray-head / ray-worker / vllm-stacked** back cleanly.
3. `ansible-playbook playbooks/provision_sparks.yml --tags verify` with **`spark_provision_verify_ray: true`** — confirm Ray cluster; if **stale nodes** appear in `ray status`, stop units and clear: `sudo systemctl stop vllm-stacked ray-head` (leader), `sudo systemctl stop ray-worker` (follower), then `sudo -u nvidia /opt/vllm/venv/bin/ray stop -f` on both, re-run stack phase.
4. If API missing: `journalctl` on leader/follower per `docs/vllm-multi-node.md`.

## Session notes

- **2026-05-20:** **GPU clock cap 2000 MHz** — `roles/spark_gpu_clock` locks GB10 graphics clocks via `nvidia-smi -lgc 2000,2000` + systemd `cylon-gpu-clock-lock.service` on boot. Inventory: `spark_gpu_lock_clocks_enabled: true`, `spark_gpu_locked_graphics_clock_mhz: 2000`. `just spark-gpu-clock-apply` / `--tags spark_gpu_clock`. nvidia2 rebooted onto `6.17.0-1018-nvidia` without `linux-modules-nvidia-580-open-6.17.0-1018-nvidia` (stale 1008 modules blocked apt). Fixed on host; `spark_kernel` now detects missing module packages, purges stale nvidia module debs, and runs `nvidia-smi` when pin==running kernel. `spark_assert` uses `ray list nodes` (not `ray status` for IP grep); fixed NCCL container-name Jinja ternary; added `nvidia-smi` gate. in `inventory/group_vars/sparks.yml`. `roles/spark_kernel` now apt-installs the pinned image when missing (temp unhold + `allow_change_held_packages`). Apply: `just spark-kernel-apply` then `just spark-reboot`. `apt changelog linux-image-unsigned-6.17.0-1018-nvidia-64k` on gx10-47b5. **Running:** `6.17.0-1008-nvidia` (4K); **installed but not booted:** `1014`, `1018-nvidia`, `1018-nvidia-64k`. The **64k** image is a **64K page-size** build (`CONFIG_ARM64_64K_PAGES=y`), not interchangeable with fleet pin `1008` (4K). **1018-only fixes:** DGX Spark iGPU IOMMU quirk (LP #2150487), SMT asym-capacity scheduler (LP #2150671), Olympus perf PMU skip (LP #2149758), BMC IOMMU identity domain (LP #2150470), MPAM bandwidth hard-limit (LP #2150290), CXL/NPEM/fwctl (LP #2149918), CVE-2026-31431. **Cumulative since 1008 (high-signal):** RoCE stale GIDs on netdev events (1017, LP #2148311), r8169 vs r8127 Spark Ethernet contention (1017/1016, LP #2144345), ucsi/TBT5 power-plug boot hang (1016), NVFS SGL pool overrun (1016), CXL Type-2 + large CXL stack (1017). **1014** was AppArmor-only — no Spark platform fixes; aligns with prior crash bisection. **No explicit GX10 abrupt power-off fix** in 1008→1018 changelog. Hold pin at 1008 until symmetric A/B on `1018-nvidia` (4K, not 64k).
- **2026-05-20:** **Canonical provision:** `just spark-provision` = full `provision_sparks.yml` + `spark_assert` gate. Retired `cutover_roce.yml`, `refresh_hf_prefetch.yml`.
- **2026-04-11:** **nvidia1 Ray + vLLM** — **Stale GCS** fixed by reset. **Ansible `--tags vllm_stack`** skipped inner stack tasks until roles used **tagged blocks**. **`ufw`** only allowed **22/6379/8080** while Ray uses **many TCP ports** on the interconnect (`ss` showed raylets on e.g. **10002–10022**, **42xxx**) — GCS health checks to **nvidia2** failed → “marked as dead”. **Fix:** **`ufw allow from 169.254.0.0/16`** (link-local QSFP) + **`ray start`** **`--min-worker-port 10002`–`--max-worker-port 19999`** (avoids **10001** client_server and avoids huge range colliding with Ray’s other dynamic ports). Post-deploy **`ray status`:** **2** Active nodes, **`0.0/2.0` GPU**, no pending demands. **vLLM:** long **`sleep`** before API; **8080** after engine ready.
- **2026-04-09:** Playbook consolidation — single **`provision_sparks.yml`**; removed legacy `vllm_*_sparks.yml`, `apt_upgrade_sparks.yml`, `docker_hosts.yml`, probe playbooks; docs and memory bank updated.
- **APT:** `provision_sparks.yml` with `spark_apt_upgrade_mode=full` **completed** by operators — treat as parity maintenance; reboot if upgrades demanded it, then restack as above.
