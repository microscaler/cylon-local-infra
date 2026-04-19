---
title: ngc-image-sync.service (systemd, nvidia1)
kind: entity
status: active
tags: [systemd, docker, ngc, sync, leader]
updated: 2026-04-19
related:
  - entities/hf-prefetch-service.md
  - entities/ngc-vllm-image.md
  - entities/nvidia1.md
  - entities/nvidia2.md
  - concepts/spark-interconnect.md
  - runs/2026-04-19-ngc-image-sync-service.md
---

# ngc-image-sync.service

Long-running systemd daemon on the **leader** Spark that keeps the configured
OCI container images (currently `nvcr.io/nvidia/vllm`) current, and
replicates new tags to every peer Spark over the QSFP interconnect.

Same architectural pattern as [hf-prefetch-service](./hf-prefetch-service.md) —
config-driven, stdlib + pyyaml only, JSON state pollable with `jq`, Ansible
pushes config and returns; the daemon does the work.

| Field | Value |
|---|---|
| Unit path | `/etc/systemd/system/ngc-image-sync.service` |
| Script | `/opt/ngc-image-sync/ngc_image_service.py` |
| Config | `/etc/ngc-image-sync/config.yaml` (managed by `roles/ngc_image_service/templates/config.yaml.j2`) |
| State (JSON, poll this) | `/var/lib/ngc-image-sync/state.json` |
| Logs | `journalctl -u ngc-image-sync` |
| User / Group | `nvidia` / `nvidia` (so ssh picks up the QSFP key) |
| Restart | `always`, `RestartSec=30` |
| Poll interval (default) | **weekly** (604800 s) — override via `ngc_image_sync_poll_interval_sec` |
| Runs on | leader only; peers just need the interconnect ssh key from `playbooks/nccl_sparks.yml` |

## How it works

1. **Discover**: GET `https://nvcr.io/v2/<repo>/tags/list` using the Docker
   Registry v2 token-auth flow (anonymous bearer token from the `WWW-Authenticate`
   realm). Handles pagination via `Link: <url>; rel="next"`. No auth tokens
   stored; public repos only.
2. **Select**: filter by `tag_pattern` regex (default `^\d{2}\.\d{2}-py3$` for
   NGC's `YY.MM-py3` cadence), sort by extracted integers,
   keep the newest `keep_latest` plus any `always_keep` list.
3. **Pull**: for each kept tag not already on the local Docker daemon,
   run `docker pull <repo>:<tag>` — streams into `journalctl`.
4. **Peer-sync** (fast-path first): compare local `docker image inspect
   --format {{.Id}}` vs `ssh <peer> docker image inspect --format {{.Id}}` for
   the same tag. If identical, skip — we've already got it on the peer (e.g.
   via a manual `docker save | ssh docker load` or a prior sync). If different
   or missing on peer, run the `docker save | ssh … docker load` pipeline over
   the QSFP interconnect (as `nvidia@169.254.x`).
5. **Optional prune** (opt-in): for each local tag not in the keep-set AND not
   used by any container, `docker image rm`. Docker refuses to remove
   in-use images so no serving outage risk. Off by default until operator
   trusts the service.
6. **Sleep** `poll_interval_sec` seconds, then loop.

## State schema (v1)

```json
{
  "host": "gx10-e1ce",
  "pid": 524383,
  "started_at": 1776583256.5,
  "updated_at": 1776583378.8,
  "last_poll_at": 1776583375.2,
  "next_poll_at": 1777188175.1,
  "schema": 1,
  "images": {
    "nvcr.io/nvidia/vllm:@discovery": {
      "status": "ready",
      "upstream_tag_count": 54,
      "keep_tags": ["26.03-py3", "26.02-py3"],
      "last_discovered_at": 1776583378.5,
      "reason": "tags fetched"
    },
    "nvcr.io/nvidia/vllm:26.03-py3": {
      "status": "ready",                  // pulling | syncing | ready | error | pruned
      "reason": "present on all configured nodes",
      "failures": 0,
      "updated_at": 1776583378.8
    }
  }
}
```

The special `<repo>:@discovery` entry records the last tag-list probe so
operators can tell at a glance whether the service successfully contacted the
registry.

## Operating cheat sheet

```bash
# poll readiness
ssh casibbald@nvidia1 'jq .images /var/lib/ngc-image-sync/state.json'

# follow live progress
ssh casibbald@nvidia1 'sudo journalctl -u ngc-image-sync -f'

# force an immediate poll (useful when a new tag just landed)
ssh casibbald@nvidia1 'sudo systemctl restart ngc-image-sync'

# or lower the poll interval temporarily via inventory + ansible:
#   ngc_image_sync_poll_interval_sec: 3600
# then re-run: ansible-playbook playbooks/provision_sparks.yml --skip-tags apt,vllm_ngc_stack,hf_prefetch

# one-shot (debugging)
ssh casibbald@nvidia1 'sudo -u nvidia python3 /opt/ngc-image-sync/ngc_image_service.py --once -v'
```

## Design choices worth keeping

- **stdlib urllib for the registry protocol** — no `requests` dependency on
  the host. WWW-Authenticate parser is ~5 lines; OAuth2 anonymous token flow
  is 3 HTTP calls. Keeps us aligned with the "no host pip" policy.
- **Peer-ID fast-path** (`_peer_image_id` vs `_local_image_id`) — avoids
  25+ GB `docker save | ssh | docker load` no-ops when the peer already has
  the image from a prior manual transfer or earlier sync pass. First
  deploy saw this save a full 25 GB round-trip on the in-flight 26.03-py3.
- **`always_keep: ["{{ vllm_stacked_container_image | regex_replace(...) }}"]`**
  in inventory — whatever we're actually serving is pinned into the keep-set,
  so a newer tag landing on NGC never kicks the in-use image out of the
  keep-latest window.
- **`prune_stale: false` by default** — operator opt-in. We don't want a
  service decision to silently remove an image an operator might be about
  to bump the serving config to.
- **Registry v2 `Link` pagination** handled — future-proofs against NGC
  shipping 100+ tags per repo.

## Failure-mode notes

- Registry unreachable / auth fails: the `@discovery` entry goes `error`, but
  already-present images stay `ready`. No existing state is torn down.
- Peer unreachable mid-sync: backoff kicks in (60s → 300 → 900 → 1800 → 3600),
  keeps retrying forever. `state.images[<tag>].failures` increments so
  operators can triage.
- Service restart during a save/load pipeline: systemd SIGTERM → Runner sends
  SIGTERM to the bash child → shell pipeline tears down cleanly. Container
  images are content-addressable so no half-loaded state on the peer.

## Cross-refs

- [entities/hf-prefetch-service.md](./hf-prefetch-service.md) — sibling
  service for HF model weights. Same design vocabulary.
- [runs/2026-04-19-ngc-image-sync-service.md](../runs/2026-04-19-ngc-image-sync-service.md)
  — first deploy, bugs caught, initial discovery.
