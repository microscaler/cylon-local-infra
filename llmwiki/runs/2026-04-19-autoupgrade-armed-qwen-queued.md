---
title: 2026-04-19 — autoupgrade armed; Qwen3.6 + Qwen3-Coder queued on hf-prefetch
kind: run
status: active
outcome: success
tags: [autoupgrade, armed, qwen3_6, qwen3-coder, prefetch, milestone]
updated: 2026-04-19
related:
  - runs/2026-04-19-26.03-py3-upgrade.md
  - entities/vllm-stack-autoupgrade-service.md
  - entities/hf-prefetch-service.md
  - entities/ngc-image-sync-service.md
---

# Autoupgrade armed; two real model targets queued

Post-26.03 cutover, follow-through on the three next steps from
[runs/2026-04-19-26.03-py3-upgrade.md](./2026-04-19-26.03-py3-upgrade.md):

1. **Arm the autoupgrade daemon** for future image cutovers.
2. **Queue the real model targets** on hf-prefetch now that 26.03-py3 unlocks them.
3. **Make inventory match reality** — `vllm_default_model` now tracks what's
   actually served (TinyLlama for smoke; Qwen swap is a downstream step).

## Inventory changes (all in `inventory/group_vars/sparks.yml`)

```yaml
# arm the autoupgrade daemon
vllm_autoupgrade_enabled: true

# inventory now matches the running stack (TinyLlama on 26.03-py3)
vllm_default_model: "TinyLlama/TinyLlama-1.1B-Chat-v1.0"
vllm_api_server_extra_args: []   # TinyLlama max_position_embeddings = 2048

# queue the real targets in download-priority order
hf_prefetch_models:
  - TinyLlama/TinyLlama-1.1B-Chat-v1.0            # keep, smoke model
  - Qwen/Qwen3.6-35B-A3B                          # primary target
  - Qwen/Qwen3-Coder-30B-A3B-Instruct             # fallback / A-B
```

One `ansible-playbook playbooks/provision_sparks.yml --skip-tags apt,vllm_ngc_stack`
push — no container bounce, no model swap. The running stack on 26.03-py3
keeps serving TinyLlama; the two other daemons pick up config changes.

## Post-push state across all three daemons

```json
// /var/lib/vllm-stack-autoupgrade/state.json
{
  "enabled": true,
  "status": "ready",
  "current_image": "nvcr.io/nvidia/vllm:26.03-py3",
  "candidate_tag": "26.03-py3"
}
```

`enabled=true` — **armed**. `status=ready` because the newest candidate
(`26.03-py3`) matches what's already running; nothing to promote. When 26.04
or later lands on NGC, the daemon will go
`candidate → waiting_quiet → promoting → ready` autonomously, gated by the
1 h stabilization + 5 min quiet window on `/metrics`.

```json
// /var/lib/hf-prefetch/state.json — models
[
  { "key": "TinyLlama/TinyLlama-1.1B-Chat-v1.0",
    "status": "ready", "bytes": 2202470815, "reason": "cached + synced" },
  { "key": "Qwen/Qwen3.6-35B-A3B",
    "status": "downloading", "bytes": 16657945, "reason": "hf download" }
]
```

Qwen3.6-35B-A3B is **actively downloading** (just started, 17 MB in). Qwen3-Coder-30B-A3B
will start as soon as Qwen3.6 finishes + rsyncs to nvidia2 — the daemon
processes models sequentially within a reconcile pass.

At the observed HF CDN speed, expect roughly:

| Model | Size | WAN DL | QSFP sync | Total |
|---|---|---|---|---|
| Qwen/Qwen3.6-35B-A3B | ~70 GB | ~10 h | ~2.5 min | ~10 h |
| Qwen/Qwen3-Coder-30B-A3B-Instruct | ~61 GB | ~8.5 h | ~2 min | ~8.5 h |

Background work — operator can detach and come back.

```json
// /var/lib/ngc-image-sync/state.json  (unchanged)
{ "keep": ["26.03-py3", "26.02-py3"],
  "next_poll": 1777188947.2962108 }   // ~1 week out
```

## Cutover-to-Qwen path (for later, when a model is `ready`)

When `/var/lib/hf-prefetch/state.json` shows `Qwen/Qwen3.6-35B-A3B` →
`status: "ready"` on nvidia1 (and by extension synced to nvidia2):

```bash
# A) Single-shot cutover via per-run -e overrides (doesn't churn inventory):
ansible-playbook playbooks/provision_sparks.yml \
  --skip-tags apt,hf_prefetch,vllm_autoupgrade,ngc_image_sync \
  -e vllm_stacked_container_recreate=true \
  -e vllm_default_model=Qwen/Qwen3.6-35B-A3B \
  -e '{"vllm_api_server_extra_args":["--max-model-len","32768"]}'

# B) Or update inventory (vllm_default_model + vllm_api_server_extra_args) then:
ansible-playbook playbooks/provision_sparks.yml \
  --skip-tags apt,hf_prefetch,vllm_autoupgrade,ngc_image_sync \
  -e vllm_stacked_container_recreate=true
```

Either triggers the trusted `vllm_stacked_container` bounce path (same as
today's 26.03 cutover) — stop workers, stop head, start new head on the
configured image with the new model, wait for Ray, start workers, exec
`vllm serve` with the new args. Then smoke with `/v1/chat/completions`.

## Why arming now is safe

The 26.01 → 26.03 cutover earlier today used the exact same docker commands
that `vllm-stack-autoupgrade` would emit:

- Same `docker stop` / `docker rm` sequence (worker first, then head)
- Same `docker run` parameters (reconstructed from `docker inspect` in the
  daemon's case, rendered from inventory in Ansible's)
- Same `docker exec -d head 'vllm serve …'` pattern

We've seen this path land green end-to-end. The daemon additionally adds
stabilization (1 h) and quiet-window (5 min of no API traffic) gates, which
Ansible's manual run bypassed. Net: daemon path is **strictly safer** than
what we just did manually, so arming it is a tightening, not a loosening.

## Follow-ups

- [ ] When `Qwen3.6-35B-A3B` reaches `ready`, perform the model cutover per
      the path documented above. File a run page with the result.
- [ ] Consider shipping a second daemon (or a flag on the autoupgrade daemon)
      that handles **model** cutovers symmetrically to image cutovers —
      operator-armed, with the same stabilization + quiet-window safety
      rails.
- [ ] If 26.04-py3 lands on NGC while we're running this config: daemon will
      auto-promote after stabilization. That's the real "this works end-to-end
      without human intervention" moment — worth a dedicated wiki run page
      when it happens.
