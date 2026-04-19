---
title: 2026-04-18 — hf-prefetch service proof-of-life (TinyLlama, end-to-end)
kind: run
status: active
outcome: success
tags: [hf, prefetch, daemon, tinyllama, proof-of-life]
updated: 2026-04-18
related:
  - runs/2026-04-18-hf-prefetch-service.md
  - entities/hf-prefetch-service.md
  - entities/model-tinyllama-1_1b.md
---

# hf-prefetch service — proof-of-life run

Following the first deploy of `hf-prefetch.service`, point it at the smallest
model we have (`TinyLlama/TinyLlama-1.1B-Chat-v1.0`, 2.2 GB) to validate the
full lifecycle before trusting it with a 60 GB download.

## Changes from the first deploy

### Progress heartbeat in `state.json`

The first deploy set `cache_bytes: 0` at download start and never updated it
until the download ended — operators couldn't tell from `state.json` whether a
long download was making progress. Patched the daemon:

* `StateStore` is now thread-safe (a `threading.Lock` protects `set()`/`save()`).
* `hf_download()` spawns a daemon thread that walks the repo's cache subtree
  every `PROGRESS_INTERVAL_SEC` seconds (5 s) and writes:
  * `cache_bytes` — apparent size on disk (includes `.incomplete` blobs
    as hf-xet extends them).
  * `bytes_per_sec` — delta between the last two samples.
* Thread is joined on subprocess exit, and a final `cache_bytes` reading is
  stamped after the process exits so the last value reflects the terminal
  state.

No changes to external interfaces — operators still just `jq .models state.json`.

## Sequence

```
ssh casibbald@nvidia1 'sudo systemctl stop hf-prefetch'
# proper hf-API wipe of hub, rm -rf xet + .locks, drop state.json
ansible-playbook playbooks/provision_sparks.yml --skip-tags apt,vllm_ngc_stack
# handler restarts the unit with the new script + config (TinyLlama only)
```

## Observed state transitions (polled every 10 s)

| t | `status` | `cache_bytes` | `bytes_per_sec` |
|---|---|---|---|
| +10 s | downloading | 1.06 GB | null |
| +20 s | downloading | 1.06 GB | null |
| +30 s | downloading | 1.21 GB | 31 MB/s |
| +40 s | downloading | 2.01 GB | **85 MB/s** |
| +50 s | downloading | 2.18 GB | 35 MB/s |
| +60 s | downloading | 2.20 GB | 3.6 MB/s |
| +2:45 | (download complete, rsync starts) | — | — |
| +2:49 | **ready** | 2.20 GB | — |

`hf-xet` pulls multiple shards in parallel; the spikes to 85 MB/s happen while
the CDN is feeding several of them at once.

## rsync step (auto-triggered)

```
rsync -a --delete --partial --info=stats2,progress0 \
  -e 'ssh -o StrictHostKeyChecking=no -o BatchMode=yes -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR' \
  /home/nvidia/.cache/huggingface/hub/models--TinyLlama--TinyLlama-1.1B-Chat-v1.0/ \
  nvidia@169.254.37.109:/home/nvidia/.cache/huggingface/hub/models--TinyLlama--TinyLlama-1.1B-Chat-v1.0/
```

Result from the journal:

```
Number of files: 26 (reg: 11, dir: 5, link: 10)
Total file size: 2,202,470,815 bytes
sent 2,203,010,186 bytes  received 285 bytes  489,557,882.44 bytes/sec
total size is 2,202,470,815  speedup is 1.00
2026-04-19 09:06:02,803 INFO TinyLlama/TinyLlama-1.1B-Chat-v1.0 READY (2.1GiB on disk)
```

**489 MB/s** over the QSFP interconnect — link-local `169.254.x/16`, no LAN
contention. The `-a` flag preserved the HF cache's symlink structure
(`snapshots/<rev>/*.safetensors` → `../../blobs/<sha256>`), 10 symlinks and
11 regular files transferred correctly.

## Idempotency

Next 30 s reconcile pass logged only:

```
2026-04-19 09:07:02 INFO summary: {'ready': 1}
```

No re-download, no re-rsync. Cache is validated as complete via
`cache_status()`: snapshot has `config.json`, no `.incomplete` blobs, status
remains `ready`.

## What this proves

- **End-to-end flow**: config → download → auto-sync → ready.
- **Live observability**: `state.json` tracks real bytes, not a flag set at
  start time.
- **Operator contract**: Ansible writes config and returns in seconds; the
  daemon does the work; operator polls `jq .models state.json` to check.
- **Peer-sync via QSFP works**: 489 MB/s sustained. For a 60 GB model,
  that's ~2 minutes of rsync after the download completes on the leader.

## Follow-up

- [ ] Swap `hf_prefetch_models` back to `Qwen/Qwen3-30B-A3B-Instruct-2507`
      (add it alongside TinyLlama — the daemon handles multiple) and let it
      run overnight.
- [ ] Document the state-file schema (including `bytes_per_sec`, `failures`)
      in [`entities/hf-prefetch-service.md`](../entities/hf-prefetch-service.md).
      (done in this run — see the entity page for the updated schema block.)
