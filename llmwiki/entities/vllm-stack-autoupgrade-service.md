---
title: vllm-stack-autoupgrade.service (systemd, nvidia1)
kind: entity
status: active
tags: [systemd, vllm, autoupgrade, daemon, leader]
updated: 2026-04-19
related:
  - entities/ngc-image-sync-service.md
  - entities/ngc-vllm-image.md
  - entities/vllm-stacked-service.md
  - concepts/ngc-stacked-container-stack.md
  - runs/2026-04-19-vllm-stack-autoupgrade-service.md
---

# vllm-stack-autoupgrade.service

Leader-only systemd daemon that promotes a newer NGC vLLM container image
into the running stacked-Ray + vllm-serve deployment **only** after both
(1) image stabilization and (2) a quiet-window on the live vLLM API.

| Field | Value |
|---|---|
| Unit path | `/etc/systemd/system/vllm-stack-autoupgrade.service` |
| Script | `/opt/vllm-stack-autoupgrade/vllm_stack_autoupgrade.py` |
| Config | `/etc/vllm-stack-autoupgrade/config.yaml` (managed by `roles/vllm_stack_autoupgrade/templates/config.yaml.j2`) |
| State (JSON) | `/var/lib/vllm-stack-autoupgrade/state.json` |
| Logs | `journalctl -u vllm-stack-autoupgrade` |
| User / Group | `nvidia` / `nvidia` |
| Restart | `always`, `RestartSec=30` |

## Preconditions for a promotion

All of these must be true:

1. **`enabled: true`** in the config (operator opt-in per cluster).
2. **Newer tag `ready`** in `ngc-image-sync`'s state:
   - Must match the configured `tag_pattern` (default `^\d{2}\.\d{2}-py3$`).
   - Must rank newer than the image of the currently-running leader container.
3. **Stabilization time elapsed** — the candidate tag has been `status: ready`
   on all nodes in the ngc-image-sync state for ≥ `stabilization_sec` seconds
   (default **1 h**). Absorbs NGC flapping / retract-and-republish.
4. **vLLM API quiescent** — reading `/metrics`, every sample in a
   `quiet_window_sec`-long window (default **5 min**, sampled every 30 s)
   shows:
   - `vllm:num_requests_running == 0`
   - `vllm:num_requests_waiting == 0`
   - `vllm:request_success_total` unchanged between samples
   If the API is unreachable we treat that as quiet (nothing to drain).
5. If quiet isn't achieved within `max_wait_for_quiet_sec` (default 24 h),
   the daemon backs off and tries again on the next outer poll instead of
   force-bouncing a busy API.

## Promotion sequence

Once promotion is authorised:

1. `docker container inspect` captures the **full runtime spec** of the head
   and each peer worker — env, mounts, shm_size, GPU requests, restart
   policy, entrypoint, command — so we replay the exact same container on
   the new image. No config duplication between this daemon and
   `roles/vllm_stacked_container`.
2. `docker exec head pgrep -af 'vllm serve'` captures the current
   `vllm serve` argv — we re-exec with the same flags.
3. Bounce order: peer workers first (stop + rm via ssh) → leader head
   (stop + rm).
4. Start new leader head with captured spec + new image.
5. Wait for Ray GCS (`ss -tln | grep :6379`) inside the head container,
   up to `ray_gcs_wait_sec` (default 120 s).
6. `ssh peer docker run …` to launch each peer worker with its captured spec.
7. Wait for `docker exec head ray status` to show all expected nodes,
   up to `ray_cluster_wait_sec` (default 180 s).
8. `docker exec -d head bash -lc 'exec > /root/vllm-serve.log 2>&1; <argv>'`
   to re-exec `vllm serve`.
9. Wait for `/v1/models` 200 (port extracted from the captured argv) up to
   `vllm_api_wait_sec` (default 60 min — first-boot load for a 30 B model).

No automatic rollback in v1. On any failure we log loudly, set
`state.last_error`, and stop — operator triage. Rationale: silent re-attempts
mask real problems; an alert a human can see is better than a cluster that
keeps bouncing.

## State schema (v1)

```json
{
  "schema": 1,
  "host": "gx10-e1ce",
  "pid": 530708,
  "started_at": 1776584252.8,
  "updated_at": 1776584252.9,
  "status": "idle",           // idle | candidate | waiting_quiet | promoting | ready | error
  "enabled": false,
  "current_image": "nvcr.io/nvidia/vllm:26.01-py3",
  "leader_container": "vllm-ngc-ray-head",
  "candidate_tag": null,
  "candidate_since": null,
  "quiet_consecutive": 0,
  "quiet_needed": 10,
  "reason": "...",
  "last_promotion": {
    "from": "nvcr.io/nvidia/vllm:26.01-py3",
    "to":   "nvcr.io/nvidia/vllm:26.03-py3",
    "at":   1776584250.0
  },
  "last_error": null
}
```

## Operating cheat sheet

```bash
# Daemon state
ssh casibbald@nvidia1 'jq . /var/lib/vllm-stack-autoupgrade/state.json'

# Follow
ssh casibbald@nvidia1 'sudo journalctl -u vllm-stack-autoupgrade -f'

# Enable auto-upgrade
# (edit inventory/group_vars/sparks.yml: vllm_autoupgrade_enabled: true)
ansible-playbook playbooks/provision_sparks.yml --skip-tags apt,vllm_ngc_stack,hf_prefetch

# Pin to a specific tag (rollback a surprise promotion, or freeze)
# (edit inventory: vllm_autoupgrade_pinned_tag: "26.03-py3")
ansible-playbook playbooks/provision_sparks.yml --skip-tags apt,vllm_ngc_stack,hf_prefetch

# Disable
# (edit inventory: vllm_autoupgrade_enabled: false)
ansible-playbook playbooks/provision_sparks.yml --skip-tags apt,vllm_ngc_stack,hf_prefetch
```

Service reloads config on mtime change — no `systemctl restart` needed when
editing the config; Ansible's template render is enough.

## Design rationale

- **Separate service from `ngc-image-sync`** — one daemon manages image
  availability, the other manages stack lifecycle. Operators can enable either
  independently; a bug in one doesn't take out the other.
- **Capture-via-inspect, not duplicate config** — the running container is the
  source of truth for its spec. Avoids drift between this daemon's config and
  `roles/vllm_stacked_container`'s templates.
- **Capture `vllm serve` argv live** — operators who change the serve args at
  runtime (e.g. `--max-model-len`) don't need to update this daemon's config;
  it reads the actual running args.
- **Quiet-window gate on `/metrics`** — uses vLLM's Prometheus endpoint which
  is always available. No separate instrumentation or log parsing.
- **Three knobs for operators** — `enabled`, `pinned_tag`, and
  `stabilization_sec`. Everything else has a sane default.

## Cross-refs

- Sibling: [entities/ngc-image-sync-service.md](./ngc-image-sync-service.md).
- Sibling: [entities/hf-prefetch-service.md](./hf-prefetch-service.md).
- Run: [runs/2026-04-19-vllm-stack-autoupgrade-service.md](../runs/2026-04-19-vllm-stack-autoupgrade-service.md).
