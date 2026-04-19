---
title: 2026-04-18 — hf-prefetch service (download-once, sync-to-peers daemon)
kind: run
status: active
outcome: in-progress
tags: [hf, prefetch, systemd, service, architecture]
updated: 2026-04-18
related:
  - entities/hf-prefetch-service.md
  - runs/2026-04-18-tp2-nccl-solved.md
  - concepts/ngc-stacked-container-stack.md
---

# hf-prefetch service — download-once, sync-to-peers daemon

## Motivation

Driving HF downloads synchronously from Ansible was the wrong architecture:

1. **Ansible blocks**: a 60 GB model at ~400 KB/s is a 40 h run. The operator's
   laptop has to stay connected or the whole provisioning aborts.
2. **Parallel downloads to both Sparks contend for our upstream** — measured
   ~380 KB/s per host when two were active, ~2 MB/s when only one was active
   (6× faster). Saturating the WAN pipe from one host then mirroring over the
   200 Gbps QSFP interconnect dominates two parallel WAN pulls.
3. **No observability** during long downloads. Ansible just shows "running".
   If you disconnect, you can't peek at progress without re-attaching.
4. **No resume contract**: a killed Ansible run left `.incomplete` blobs behind
   with no clear ownership of continuing them.

Operator directive (2026-04-18):

> Connecting and trying to download does not work. We need a python script that
> can run in the background with a subprocess. We give it a model in a config
> file and it starts to download. We add another model and it starts to
> download, and on completion it syncs to nvidia2. If we add a model we already
> have, it will know that it already has it. This can be added to our systemd
> and it can sit on nvidia1 as a downloadservice. This way your ansible does
> not have to sit and connect and download, and you the model harness have to
> be connected. You can or I just connect and poll readyness. This script must
> be in our repo and delivered by ansible.

## Implementation

New role: `roles/hf_prefetch_service/`.

| File | Purpose |
|---|---|
| `files/hf_prefetch_service.py` | stdlib + pyyaml daemon (~380 lines). Config-driven, idempotent, signal-safe. |
| `templates/config.yaml.j2` | renders `/etc/hf-prefetch/config.yaml` from inventory (`hf_prefetch_models` + auto-populated peer targets via `nccl_interface`). |
| `templates/hf-prefetch.service.j2` | systemd unit, `User=nvidia`, `Restart=always`, `KillMode=mixed`, tight `HOME` env so rsync's ssh finds `~/.ssh/`. |
| `defaults/main.yml` | sane defaults; `hf_prefetch_wait_for_ready` lets Ansible optionally block. |
| `tasks/main.yml` | installs script, config, unit; creates cache dir on every Spark; leader-only service install. |
| `handlers/main.yml` | restart on script / unit changes; config changes are picked up via mtime polling. |

Wired into `roles/spark_provision/tasks/main.yml` in place of the old
`hf_spark` role; `hf_spark` retired (no shell scripts, no tooling left behind).

## First deploy (2026-04-18 08:48 EEST)

`ansible-playbook playbooks/provision_sparks.yml --skip-tags apt,vllm_ngc_stack`

Two bugs surfaced, both fixed in the second deploy:

### Bug 1 — Jinja operator precedence

```jinja
cache_dir: {{ hostvars[peer].vllm_hf_home | default(...) ~ '/hub' | to_json }}
```

`|` binds tighter than `~`, so `'/hub' | to_json` became `"/hub"` first and
then got concatenated onto an unquoted path: `.../huggingface"/hub"`. Fixed
by assigning `set` variables and applying `| to_json` to the composed final
string.

### Bug 2 — Docker container outlived the CLI on service restart

`docker run --rm --name <X>` is a CLI wrapper; killing the CLI does **not**
stop the daemon-managed container. When systemd restarted the service, the
new `docker run --name <X>` hit `Conflict. The container name "<X>" is
already in use`. Fix:

1. Track `runner.current_container = "<name>"` while a download is in flight.
2. On `SIGTERM`, call `docker stop -t 10 <name>` **before** SIGTERM'ing the
   CLI. The `--rm` flag now does the right thing because the container exits
   normally.
3. At the start of every `hf_download`, run `docker rm -f <name>` first as
   belt-and-braces (and for cases where the service was `kill -9`'d).

## Post-fix deploy (2026-04-18 08:51 EEST)

```
$ sudo systemctl is-active hf-prefetch
active

$ jq .models /var/lib/hf-prefetch/state.json
{
  "Qwen/Qwen3-30B-A3B-Instruct-2507": {
    "status": "downloading",
    "reason": "hf download (resume)",
    "cache_bytes": 12146636968,
    "image": "nvcr.io/nvidia/vllm:26.01-py3",
    ...
  }
}

$ journalctl -u hf-prefetch | grep "resume from"
... (resume from 2147483648/3998893112) ...
... (resume from 2481181463/3999974192) ...
... (resume from 1073741824/3999975056) ...
    # etc. — 8 safetensor shards, all 20-60% done, resuming from disk
```

Download is running; daemon will rsync to `nvidia2` over the QSFP interconnect
(`169.254.37.109`) on completion, then flip `status` to `ready`. Operator can
detach and come back later.

## How we're using it now

```yaml
# inventory/group_vars/sparks.yml
hf_prefetch_models:
  - Qwen/Qwen3-30B-A3B-Instruct-2507
hf_prefetch_wait_for_ready: false   # do not block Ansible
```

```bash
# poll from anywhere with ssh access
ssh casibbald@nvidia1 'jq .models /var/lib/hf-prefetch/state.json'
```

## Follow-ups

- [ ] When the first model lands `ready`, confirm the automatic rsync to
      nvidia2 succeeds (verify with `hf cache scan` on nvidia2 matching
      on-disk `cache_bytes`).
- [ ] Launch `vllm serve` against Qwen3-30B at TP=2 via the existing
      `vllm_stacked_container` stack (NCCL env already pinned to working
      values; see
      [concepts/nccl-on-spark.md](../concepts/nccl-on-spark.md)).
- [ ] Add the service's state file to `docs/provision_sparks.md`'s
      "how to verify" block.
- [ ] Future enhancement: optional pre-download disk-space check that refuses
      to start if `df -B1 /home/nvidia | tail -1 | awk '{print $4}'` is less
      than `expected model size × 1.5`.
- [ ] Future enhancement: HTTP `/readyz` endpoint for Kubernetes-style
      readiness probes when we integrate with the larger Cylon orchestrator.
