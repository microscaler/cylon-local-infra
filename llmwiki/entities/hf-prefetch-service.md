---
title: hf-prefetch.service (systemd, nvidia1)
kind: entity
status: active
tags: [systemd, hf, prefetch, sync, leader]
updated: 2026-04-18
related:
  - entities/hf-cache-sparks.md
  - entities/nvidia1.md
  - entities/nvidia2.md
  - entities/ngc-vllm-image.md
  - concepts/spark-interconnect.md
  - runs/2026-04-18-hf-prefetch-service.md
---

# hf-prefetch.service

Long-running systemd service on the **leader** Spark that owns the HF model
cache: downloads each configured model once via the NGC `hf` CLI, then rsyncs
the repo's hub subtree to every peer Spark over the QSFP interconnect.
Ansible does **not** block on downloads any more — it writes the config, starts
the daemon, and returns.

| Field | Value |
|---|---|
| Unit path | `/etc/systemd/system/hf-prefetch.service` |
| Script | `/opt/hf-prefetch/hf_prefetch_service.py` |
| Config | `/etc/hf-prefetch/config.yaml` (managed by `roles/hf_prefetch_service/templates/config.yaml.j2`) |
| State (JSON, poll this) | `/var/lib/hf-prefetch/state.json` |
| Logs | `journalctl -u hf-prefetch` |
| User / Group | `nvidia` / `nvidia` |
| Restart | `always`, `RestartSec=15` |
| Runs on | leader only (`groups['sparks'] | sort | first`); followers just get the cache dir created |

## How it works

1. Reads `config.yaml` on start. Reloads on mtime change (adding a model is
   `ansible-playbook` + the daemon's next 30 s poll cycle, no restart needed).
2. For each model, checks `cache_dir/models--<org>--<repo>` for:
   - `snapshots/<rev>/config.json` present, AND
   - no `.incomplete` blobs in `blobs/`.
   If cache is complete: skip download and go straight to sync.
3. Otherwise runs `docker run --rm --name hf-prefetch-<repo> nvcr.io/nvidia/vllm:<tag>
   hf download <repo>` — resumes partial downloads via hf-xet. A `docker rm -f`
   pre-clean handles leftover containers from prior crashed runs.
4. On successful download, loops over `sync_targets` and runs
   `rsync -a --delete --partial` to each peer at
   `nvidia@<peer-interconnect-ip>:<peer cache dir>`. Passwordless SSH is
   already wired up by `playbooks/nccl_sparks.yml`.
5. Writes per-model status into `state.json` after every transition so
   operators can `jq` the file without attaching to the journal.

## State schema (v1)

```json
{
  "host": "gx10-e1ce",
  "pid": 488867,
  "started_at": 1776578579.95,
  "updated_at": 1776578762.80,
  "schema": 1,
  "models": {
    "TinyLlama/TinyLlama-1.1B-Chat-v1.0": {
      "status":        "ready",            // unknown | downloading | syncing | ready | error
      "reason":        "cached + synced",  // free-form human-readable
      "cache_bytes":   2202470815,         // apparent size on disk (inc. .incomplete shards)
      "bytes_per_sec": 3569556,            // rolling delta from progress heartbeat; null on first sample
      "failures":      0,                  // consecutive failures (drives exponential backoff)
      "image":         "nvcr.io/nvidia/vllm:26.01-py3",
      "updated_at":    1776578762.80
    }
  }
}
```

During an active download the `cache_bytes` and `bytes_per_sec` fields are
updated every 5 s by a heartbeat thread — operators polling with `jq` see
live progress without attaching to the journal.

## Operating cheat sheet

```bash
# Poll readiness
ssh casibbald@nvidia1 'jq .models /var/lib/hf-prefetch/state.json'

# Follow live progress
ssh casibbald@nvidia1 'sudo journalctl -u hf-prefetch -f'

# Add a model: edit inventory/group_vars/sparks.yml, then
ansible-playbook playbooks/provision_sparks.yml --skip-tags apt,vllm_ngc_stack

# Force one-shot cycle (useful in tests)
ssh casibbald@nvidia1 'sudo -u nvidia python3 /opt/hf-prefetch/hf_prefetch_service.py --once -v'

# Restart daemon (state + partial downloads survive)
ssh casibbald@nvidia1 'sudo systemctl restart hf-prefetch'
```

## Design choices worth keeping

- **No host pip**: stays in the container-only spirit we committed to in
  [runs/2026-04-18-rip-out-bare-metal.md](../runs/2026-04-18-rip-out-bare-metal.md).
  Only stdlib + `python3-yaml` (apt) on the host; the heavy lifting (`hf download`)
  runs in the same NGC image that serves vLLM.
- **Deterministic container name** — `hf-prefetch-<sanitised repo>` — so we can
  `docker stop` the right container on SIGTERM and survive service restarts
  without leaking.
- **Rsync over QSFP, not LAN** — `sync_targets.host` is the peer's interconnect
  IPv4 (`169.254.x`), giving us the 200 Gbps link instead of the 1 Gbps LAN.
- **No HTTP endpoint** — state is a JSON file, pollable with `jq`, greppable
  with stdlib `json.load`. Ansible uses this for the optional readiness gate
  (`hf_prefetch_wait_for_ready: true`) without needing an HTTP client.
- **Config hot-reload** — poll `config.yaml` mtime. Adding a model doesn't
  require `systemctl restart`; the next reconciliation picks it up.

## Failure-mode notes (see the run page for full context)

- Transient HF CDN slowness doesn't break anything — `hf download` resumes
  safely and our wrapper tracks failure counts + exponential backoff.
- `SIGTERM` on the service now stops the in-flight container cleanly via
  `docker stop <name>` **before** SIGTERM'ing the CLI (otherwise the daemon-
  managed container outlives the CLI and the next `--name` collides).
- The Jinja template had an operator-precedence bug where `| to_json` ate
  the wrong part of a string concat — fixed by using `set` variables before
  the YAML key. Worth remembering when writing j2 templates with chained
  filters.
