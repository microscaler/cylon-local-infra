---
title: 2026-04-18 — rip out bare-metal + custom-image paths
kind: run
status: active
outcome: success
tags: [decision, cleanup, containers-only]
updated: 2026-04-18
related:
  - runs/2026-04-18-state-of-cluster.md
  - concepts/ngc-stacked-container-stack.md
  - concepts/bare-metal-venv-stack.md
  - concepts/transformers-huggingface-hub-mismatch.md
---

# Decision — container-only Spark stack

## Context

After yet another bare-metal failure
([concepts/transformers-huggingface-hub-mismatch.md](../concepts/transformers-huggingface-hub-mismatch.md)
with a restart counter of 967 on `vllm-stacked.service`), the operator decided to
**delete** the bare-metal venv path and focus the repo on the NGC container stack.

The third path — `roles/vllm_docker_stack/` backed by the vendored
`contrib/spark-vllm-docker/` — was also removed: the explicit direction was one
blessed container route, not two.

## Deletions

| Path | Why |
|---|---|
| `roles/vllm/` | bare-metal venv + systemd units; source of every pip dependency regression we've hit. |
| `roles/vllm_docker_stack/` | second (custom-image) container path; kept surface area we don't need. |
| `contrib/spark-vllm-docker/` | vendored upstream scripts that fed `vllm_docker_stack`. |
| `docs/contrib-spark-vllm-docker.md` | obsolete once vendor tree removed. |
| `docs/vllm-multi-node.md` | described the bare-metal stacked path; superseded by `llmwiki/concepts/ngc-stacked-container-stack.md`. |
| `docs/vllm-timebox-and-pivot.md` | the pivot is now complete. |
| `docs/spark-parity-pre-stack.md` | parity steps were for the venv path; container path is parity-by-construction (one image). |

## Rewrites

- `roles/spark_provision/defaults/main.yml` — phase toggles reduced to the
  container-only flow (NGC vllm + optional HF prefetch inside the same image).
- `roles/spark_provision/tasks/main.yml` — dropped all `include_role: name: vllm` and
  `name: vllm_docker_stack`. Order: sudoers → cuda_apt_cleanup → apt → docker →
  firewall → lan_ipv6_sysctl → cuda_toolkit (off by default) → hf_spark →
  vllm_stacked_container → diagnostics.
- `roles/hf_spark/` — rewritten to prefetch models via
  `docker run --rm nvcr.io/nvidia/vllm:<tag> hf download <repo>`, mounting the host HF
  cache at `/root/.cache/huggingface`. **No host venv, no host pip.**
- `inventory/group_vars/sparks.yml` — stripped all venv / pip / transformers
  variables. Remaining vars are either container inputs (model, TP, env file), ops
  (firewall, interconnect, sudoers), or NCCL (used by the optional
  `playbooks/nccl_sparks.yml`). New defaults:
  `spark_provision_vllm_stacked_container: true`, `spark_provision_hf: true`,
  `spark_provision_cuda_toolkit: false`.
- `roles/vllm_stacked_container/` — removed the `assert` that demanded the now-defunct
  `spark_provision_vllm_stack: false`. Still stops legacy bare-metal systemd units when
  `vllm_stacked_container_stop_bare_metal_systemd: true` (for hosts migrating from the
  old layout).
- `roles/vllm_stacked_container/defaults/main.yml` — defines its own `vllm_ray_port`
  fallback (was a bare-metal-only variable in the old `roles/vllm/defaults/main.yml`).
- `README.md`, `docs/provision_sparks.md`, `roles/vllm_stacked_container/README.md` —
  updated to reflect container-only semantics, link into the wiki.

## Outcome

- Repo surface area: **smaller** (one `vllm_*` role instead of three; two-screen
  `sparks.yml` instead of five-screen).
- Feedback loop: **shorter** (no more `pip install` → `restart counter ++` cycles).
- Supported failure modes the wiki already has a page for:
  [ipv6-asgi-hang](../concepts/ipv6-asgi-hang.md),
  [ray-cgraph-timeout](../concepts/ray-cgraph-timeout.md),
  [nccl-on-spark](../concepts/nccl-on-spark.md),
  [spark-interconnect](../concepts/spark-interconnect.md).
- The
  [transformers-huggingface-hub-mismatch](../concepts/transformers-huggingface-hub-mismatch.md)
  concept is marked `status: superseded` — it can no longer bite us through the
  container path. Kept in the wiki as institutional memory.

## Follow-ups

- [ ] Stop + disable + mask `vllm-stacked.service`, `vllm.service`, `ray-head.service`,
      `ray-worker.service` on both Sparks; `rm -rf /opt/vllm`.
- [ ] Re-run `ansible-playbook playbooks/provision_sparks.yml --tags vllm_ngc_stack`.
- [ ] Smoke-test `curl http://nvidia1:8000/v1/models`; file the follow-up run page.
- [ ] After first successful Gemma-4 serve, mark
      [concepts/bare-metal-venv-stack.md](../concepts/bare-metal-venv-stack.md)
      `status: superseded`.

## Rollback

Everything deleted is recoverable via `git` — the commit preceding this one still has
`roles/vllm/`, `roles/vllm_docker_stack/`, and `contrib/spark-vllm-docker/`. If a
future model can't be served via NGC, the escape hatch is to resurrect
`vllm_docker_stack` from history — don't resurrect the bare-metal path.
