---
title: 2026-04-18 — state of cluster at llmwiki bootstrap
kind: run
status: active
outcome: partial
tags: [diagnosis, bootstrap]
updated: 2026-04-18
related:
  - entities/nvidia1.md
  - entities/nvidia2.md
  - entities/vllm-stacked-service.md
  - concepts/transformers-huggingface-hub-mismatch.md
  - concepts/ngc-stacked-container-stack.md
---

# State of cluster — 2026-04-18

Snapshot taken while bootstrapping the llmwiki and deciding the path to a live local
LLM endpoint.

## What was checked (from operator Mac)

```
ssh casibbald@nvidia1 'hostname && uptime && nvidia-smi -L | head -4'
ssh casibbald@nvidia2 'hostname && uptime && nvidia-smi -L | head -4'
ssh casibbald@nvidia1 'sudo systemctl status vllm-stacked ray-head; docker ps'
ssh casibbald@nvidia2 'sudo systemctl status ray-worker; docker ps'
ssh casibbald@nvidia1 'sudo journalctl -u vllm-stacked --since "2 hours ago" | tail -200'
ssh casibbald@nvidia1 'cat /opt/vllm/run-api-server-stacked.sh; cat /etc/systemd/system/vllm-stacked.service'
ssh casibbald@nvidia1 '/opt/vllm/venv/bin/pip show huggingface_hub transformers vllm'
ssh casibbald@nvidia1 'sudo ls /home/nvidia/.cache/huggingface/hub'
ssh casibbald@{nvidia1,nvidia2} 'sudo docker images'
```

## What was working

| Thing | Evidence |
|---|---|
| SSH `casibbald@nvidia1,2` | both answered hostname + uptime immediately. |
| GB10 GPU on both Sparks | `nvidia-smi -L` returned one `NVIDIA GB10` per host. |
| Ray head on `nvidia1` | `ray-head.service` active, PID 2205, running 6 days on `169.254.102.149:6379`. |
| Ray worker on `nvidia2` | `ray-worker.service` active, joined `169.254.102.149:6379`, node id `bed24016…`. |
| NGC vLLM image pulled | `nvcr.io/nvidia/vllm:26.01-py3` on **both** Sparks (~14 GB). |
| HF cache populated on nvidia1 | `models--google--gemma-4-31B-it` and `models--TinyLlama--TinyLlama-1.1B-Chat-v1.0` under `/home/nvidia/.cache/huggingface/hub`. |
| Operator IPv4 reachability | `curl https://huggingface.co/api/models/google/gemma-4-31B-it` → 200. |

## What was failing

### `vllm-stacked.service` restart loop (restart counter 967)

Every cycle:

```
ImportError: cannot import name 'is_offline_mode' from 'huggingface_hub'
  (/opt/vllm/venv/lib/python3.12/site-packages/huggingface_hub/__init__.py)
```

Versions in `/opt/vllm/venv`:

- `transformers` = `5.6.0.dev0` (git main; pulled by
  `vllm_transformers_from_git: true`).
- `huggingface_hub` = `0.36.2`.
- `vllm` = `0.19.0`.

Full concept page:
[concepts/transformers-huggingface-hub-mismatch.md](../concepts/transformers-huggingface-hub-mismatch.md).

### Systemd optics lie

`systemctl status` showed `Active: active (running)` at the start because the launcher
`sleep 600`s before exec'ing vLLM. The import error happens **after** the sleep, so the
unit looks healthy for the first 10 minutes of every cycle. Worth remembering when
diagnosing — status alone is misleading for this unit.

### `nvidia2` HF cache not yet audited

`sudo ls` returned success but we didn't capture the output. Follow-up: re-run on
`nvidia2` and note in [entities/hf-cache-sparks.md](../entities/hf-cache-sparks.md).

## Decisions

1. **Pivot to the NGC container stack** for the "get a local LLM running today" goal.
   Rationale: the bare-metal venv fix requires either pinning transformers to a tag
   that might not support Gemma 4, or downgrading `huggingface_hub`. The NGC image
   we've already pulled bypasses all of this.
2. **Keep the bare-metal role in the repo** — useful when NGC image is wrong for a
   target model. Flag it `status: active` but currently unused.
3. **First serve attempt**: TinyLlama-1.1B TP=2 via NGC (fast feedback). Then swap to
   Gemma-4-31B-it.

## Follow-ups (next run)

- [ ] Disable bare-metal `vllm-stacked`, `ray-head`, `ray-worker`, `vllm` systemd units
      so they don't fight the container stack.
- [ ] Render `head.env` / `worker-nvidia2.env` files via the Ansible role.
- [ ] Update `vllm_stacked_container_image` default to `nvcr.io/nvidia/vllm:26.01-py3`
      once validated.
- [ ] Set `spark_provision_vllm_stack: false` and
      `spark_provision_vllm_stacked_container: true` in `sparks.yml`.
- [ ] Add `8000` to `firewall_allow_tcp_ports` and
      `firewall_trusted_lan_tcp_ports`.
- [ ] Run `ansible-playbook playbooks/provision_sparks.yml --tags vllm_ngc_stack`.
- [ ] Smoke test `http://nvidia1:8000/v1/models` with TinyLlama, then Gemma.
