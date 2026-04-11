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

- **2026-04-11:** **nvidia1 Ray + vLLM** — **Stale GCS** fixed by reset. **Ansible `--tags vllm_stack`** skipped inner stack tasks until roles used **tagged blocks**. **`ufw`** only allowed **22/6379/8080** while Ray uses **many TCP ports** on the interconnect (`ss` showed raylets on e.g. **10002–10022**, **42xxx**) — GCS health checks to **nvidia2** failed → “marked as dead”. **Fix:** **`ufw allow from 169.254.0.0/16`** (link-local QSFP) + **`ray start`** **`--min-worker-port 10002`–`--max-worker-port 19999`** (avoids **10001** client_server and avoids huge range colliding with Ray’s other dynamic ports). Post-deploy **`ray status`:** **2** Active nodes, **`0.0/2.0` GPU**, no pending demands. **vLLM:** long **`sleep`** before API; **8080** after engine ready.
- **2026-04-09:** Playbook consolidation — single **`provision_sparks.yml`**; removed legacy `vllm_*_sparks.yml`, `apt_upgrade_sparks.yml`, `docker_hosts.yml`, probe playbooks; docs and memory bank updated.
- **APT:** `provision_sparks.yml` with `spark_apt_upgrade_mode=full` **completed** by operators — treat as parity maintenance; reboot if upgrades demanded it, then restack as above.
