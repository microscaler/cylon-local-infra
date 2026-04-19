---
title: 2026-04-19 — vllm-stack-autoupgrade service (quiet-window-guarded auto promotion)
kind: run
status: active
outcome: success
tags: [vllm, autoupgrade, systemd, daemon, safety]
updated: 2026-04-19
related:
  - entities/vllm-stack-autoupgrade-service.md
  - entities/ngc-image-sync-service.md
  - runs/2026-04-19-ngc-image-sync-service.md
---

# vllm-stack-autoupgrade service — first deploy

## Motivation

After `ngc-image-sync` was shipped to keep NGC image tags current on both
Sparks, the operator asked:

> can this be done automatically in the background — "Both Sparks have the new
> image. Operator decides when to cut `vllm_stacked_container_image` over to
> it." and services restarted

With one safety requirement:

> check that there are no LLM requests coming in then restart. 5 min window of
> quietness on the LLM API.

This run ships that as a separate leader-only daemon,
`vllm-stack-autoupgrade.service`.

## Preconditions for promotion (the design)

All of these must be true before a bounce fires:

1. `enabled: true` — **not** set by default. Operator flips via inventory.
2. A newer candidate tag must be `ready` in `ngc-image-sync`'s state, ranking
   higher than the currently-running leader's image.
3. The tag must have been `ready` for at least `stabilization_sec`
   (default **1 h**). Guards against NGC flapping.
4. The vLLM API must be quiescent for `quiet_window_sec`
   (default **5 min**, sampled every 30 s). "Quiescent" =
   `num_requests_running + num_requests_waiting == 0` AND
   `request_success_total` unchanged between samples.
5. If quiet isn't achieved within `max_wait_for_quiet_sec` (default 24 h),
   back off and retry on the next outer poll — do **not** force-bounce a busy
   API.

## Promotion mechanism (the "don't duplicate config" bit)

Rather than re-rendering `docker run` arguments from Ansible variables (which
would drift from what `roles/vllm_stacked_container` actually sets up), the
daemon captures the running container's spec:

```python
info = json.loads(docker("container", "inspect", "vllm-ngc-ray-head"))
# info['Config']['Env']               — env vars
# info['Config']['Entrypoint']        — /bin/bash
# info['Config']['Cmd']               — ['-c', 'ray start --block --head …']
# info['HostConfig']['Binds']         — HF cache mount
# info['HostConfig']['ShmSize']       — 10.24 GiB
# info['HostConfig']['NetworkMode']   — host
# info['HostConfig']['RestartPolicy'] — unless-stopped
# info['HostConfig']['DeviceRequests']— --gpus all
```

Plus the live `vllm serve` argv:

```python
docker("exec", "vllm-ngc-ray-head", "bash", "-lc",
       "ps -ww -eo pid,args | grep -E '[v]llm serve' | head -1 | awk ...")
```

Then `compose_run_cmd(spec, new_image, name, cmd)` builds a `docker run -d`
argv that mirrors the spec exactly, swapping only the image tag and (for the
peer) the container name. Same trick for worker containers, via ssh to the
peer.

## Deploy

```bash
ansible-playbook playbooks/provision_sparks.yml --skip-tags apt,vllm_ngc_stack,hf_prefetch
```

Ansible installed the service with `vllm_autoupgrade_enabled: false`
(safety rail default). First state dump:

```json
{
  "status": "idle",
  "enabled": false,
  "current_image": "nvcr.io/nvidia/vllm:26.01-py3",
  "leader_container": "vllm-ngc-ray-head",
  "candidate_tag": null,
  "last_promotion": null,
  "last_error": null
}
```

Service correctly read the running container's image (`26.01-py3`) despite
`ngc-image-sync` having `26.03-py3` marked `ready`. Because `enabled: false`,
it stays `idle` — exactly the intended behaviour.

## How the operator turns it on

1. Make sure `ngc-image-sync` has been running long enough for the target
   tag to have `updated_at` at least 1 h ago (or temporarily lower
   `vllm_autoupgrade_stabilization_sec` in inventory for the first run).
2. Flip `vllm_autoupgrade_enabled: true` in
   `inventory/group_vars/sparks.yml`.
3. `ansible-playbook playbooks/provision_sparks.yml --skip-tags apt,vllm_ngc_stack,hf_prefetch`
4. Watch `jq . /var/lib/vllm-stack-autoupgrade/state.json` — the status
   should move `idle → candidate → waiting_quiet → promoting → ready`.

## Follow-ups

- [ ] **Validate against a real cutover** — wait for 26.04-py3 to land on
      NGC, let `ngc-image-sync` sync it, then flip `enabled: true` and watch
      the promotion happen end-to-end. Current state has 26.03-py3 not yet
      in use (the stack is still on 26.01-py3 from before the upgrade work),
      so we need either a manual cut to 26.03-py3 first **or** wait for 26.04
      to enter the ready-set.
- [ ] Consider exposing the quiet-window check as a standalone readiness
      probe (`/readyz`) from vLLM's side; simpler than Prometheus parsing.
      Requires an upstream vLLM feature request.
- [ ] Refactor the three daemons' shared `StateStore` + subprocess `Runner`
      into `roles/_common/files/service_lib.py` once the pattern is locked.
