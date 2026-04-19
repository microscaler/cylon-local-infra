---
title: 2026-04-19 — ngc-image-sync service (NGC tag auto-pull + QSFP save/load)
kind: run
status: active
outcome: success
tags: [ngc, docker, sync, systemd, service, architecture]
updated: 2026-04-19
related:
  - entities/ngc-image-sync-service.md
  - entities/hf-prefetch-service.md
  - runs/2026-04-19-26.03-py3-upgrade.md
---

# ngc-image-sync service — first deploy

## Motivation

After hand-pulling `nvcr.io/nvidia/vllm:26.03-py3` on nvidia1 then
`docker save | ssh nvidia2 docker load` over the QSFP interconnect to replicate,
the operator asked for that workflow to be a proper service — same shape as
`hf-prefetch` — so new NGC tags land automatically without blocking Ansible
or requiring the operator to be attached to a terminal.

Requirement summary:

1. Python (no shell scripts).
2. Weekly poll of NGC for new `YY.MM-py3` tags.
3. Download on nvidia1, save/sync/load to nvidia2.
4. Self-updating (daemon reloads config on mtime change).
5. Delivered by Ansible, lives in the repo.

## Implementation

New role `roles/ngc_image_service/`:

| File | Purpose |
|---|---|
| `files/ngc_image_service.py` | Python stdlib + pyyaml daemon (~480 lines). Docker Registry v2 token-auth + pagination. Peer-ID fast-path for already-synced tags. Thread-safe JSON state. Signal-safe subprocess handling. |
| `templates/config.yaml.j2` | Renders `/etc/ngc-image-sync/config.yaml` from `ngc_image_sync_images` in inventory; auto-populates peer sync targets using each host's QSFP IP. |
| `templates/ngc-image-sync.service.j2` | systemd unit, `User=nvidia`, `Restart=always`, `HOME=/home/nvidia` so ssh finds `~/.ssh/`. |
| `defaults/main.yml` | Defaults incl. `ngc_image_sync_wait_for_ready` for blocking CI runs. |
| `tasks/main.yml` | Leader-only install + config + systemd; installs `python3-yaml` + `openssh-client`. |
| `handlers/main.yml` | Restart on script/unit changes; config changes are picked up via mtime polling. |

Wired into `roles/spark_provision/tasks/main.yml` as a new phase toggled by
`spark_provision_ngc_image_sync`. Default disabled in the role; inventory
flips it on for `sparks`.

## First deploy (2026-04-19 10:20 EEST)

Config rendered to:

```yaml
poll_interval_sec: 604800        # weekly
images:
  - repo: "nvcr.io/nvidia/vllm"
    tag_pattern: "^\\d{2}\\.\\d{2}-py3$"
    keep_latest: 2
    always_keep:                  # never evict the tag we're serving
      - "26.03-py3"
    prune_stale: false
sync_targets:
  - host: "169.254.37.109"
    user: "nvidia"
    name: "nvidia2"
```

First poll log:

```
INFO nvcr.io/nvidia/vllm — upstream tags=54, keeping ['26.03-py3', '26.02-py3']
INFO exec (sh): set -o pipefail; docker save nvcr.io/nvidia/vllm:26.03-py3 | ssh … nvidia@169.254.37.109 docker load
```

Two things jumped out:

### Caught bug — no-op save/load of 26.03-py3

nvidia2 already had 26.03-py3 from the earlier manual transfer. The service's
first pass fired the full `docker save | ssh | docker load` pipeline anyway —
25 GB of wasted QSFP. Docker's content-addressable layer dedup meant no
wasted storage, but the read/stream/write work was real.

**Fix**: pre-check `docker image inspect --format '{{.Id}}'` on both local and
peer; if the content IDs match, log and skip. Added `_local_image_id()` +
`_peer_image_id()` helpers. Patched and redeployed.

Post-fix first pass:

```
nvcr.io/nvidia/vllm:26.03-py3 → status: ready
  reason: "present on all configured nodes"         # fast-path hit, no transfer
nvcr.io/nvidia/vllm:26.02-py3 → status: pulling     # not previously local; real pull
```

### What discovery found

- **54 published tags** in `nvcr.io/nvidia/vllm` as of the poll.
- Filtered by regex `^\d{2}\.\d{2}-py3$` → just the standard cadence tags.
- Ranked by version (26.03 > 26.02 > 26.01 > 25.11 …), kept top-2 =
  `['26.03-py3', '26.02-py3']`, plus `always_keep: ['26.03-py3']`
  (which is redundant on purpose — it pins the serving image even if the
  version ranking changes).

## Operating contract established

```bash
# what tags do we have / what's the daemon doing right now?
ssh casibbald@nvidia1 'jq .images /var/lib/ngc-image-sync/state.json'

# when does the daemon next poll?
ssh casibbald@nvidia1 'jq .next_poll_at /var/lib/ngc-image-sync/state.json'

# force an early poll (e.g., "I saw an announcement, check NGC now")
ssh casibbald@nvidia1 'sudo systemctl restart ngc-image-sync'
```

Ansible's job is to write config + start the service; the "live" state is
observable without re-running Ansible and without ssh-tailing a journal.

## Follow-ups

- [ ] Once we trust the prune path, flip `prune_stale: true` in inventory for
      `nvcr.io/nvidia/vllm`. Saves ~14 GB per retired tag. Safety net:
      `docker image rm` refuses when the image is in use, and `always_keep`
      pins the serving tag.
- [ ] Add a second image to the config as a smoke test of multi-image
      handling (e.g. `nvcr.io/nvidia/vllm-ray` or similar NVIDIA-side utility
      image) — prove the multi-image loop before someone actually needs it.
- [ ] Teach the service to also record the discovered digest per tag so we can
      detect a re-tag of an existing `YY.MM-py3` name (NGC occasionally
      reissues). Today we compare by image ID on peer-sync which already
      handles this correctly; the `state.json` view would benefit from
      showing the digest too.
- [ ] Unify the two services' `StateStore` + `Runner` into a small shared
      package under `roles/_common/` if/when a 3rd similar service appears.
