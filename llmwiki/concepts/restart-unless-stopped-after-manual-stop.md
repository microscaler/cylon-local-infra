---
title: --restart unless-stopped + manual stop = stays Exited across reboot
kind: concept
status: active
tags: [docker, restart-policy, lifecycle, failure-mode]
updated: 2026-04-27
first_observed: 2026-04-27
related:
  - concepts/ngc-stacked-container-stack.md
  - runs/2026-04-27-ray-head-exited-postmortem.md
---

# `--restart unless-stopped` + manual stop = stays Exited across reboot

## What

Docker's `--restart unless-stopped` policy **deliberately does not** relaunch
a container that was manually stopped (`docker stop`, `docker kill`, or any
client-issued `POST /containers/{id}/stop`). The dockerd state for the
container carries `hasBeenManuallyStopped=true` after such a stop; that
flag survives daemon restarts, including a host reboot. Until something
explicitly **starts** the container again (`docker start`, `docker
restart`, fresh `docker run`), the container stays in `Exited` state forever.

This is the documented difference between `unless-stopped` and `always`:
`always` relaunches everything regardless; `unless-stopped` respects the
operator's "I stopped it on purpose" intent across reboots.

## Why this is sharp for our stack

`roles/vllm_stacked_container/` runs the head and worker containers with
`--restart unless-stopped`, which is the right policy for unattended
recovery from process crashes / OOMs / kernel hiccups. But our operator
surface (`just spark-vllm-stop`) issues a real `docker stop` for ergonomic
reasons — operators want a "stop the cluster" lever that survives reboots
without auto-resurrection.

The trap is the **role's start path**: when the role re-runs after a
manual stop, it sees the head as not-running and tries `docker run -d
--name vllm-ngc-ray-head ...`. That collides with the existing Exited
container (name already in use), the `docker run` fails, and the head
stays Exited. The user-visible symptom is "Ray head doesn't start."

Worker had a `docker start` reuse-existing-name fallback since
2026-04-19; head did not until 2026-04-27.

## Diagnostic signature

`docker container inspect` shows:

```
"State": { "Status": "exited", "ExitCode": 1, ... }
"HostConfig": { "RestartPolicy": { "Name": "unless-stopped" } }
```

`dockerd` journal from the previous boot has the smoking gun:

```
docker stop vllm-ngc-ray-head 2>/dev/null || true
... level=warning msg="ShouldRestart failed, container will not be restarted"
    container=... daemonShuttingDown=false error="restart canceled"
    hasBeenManuallyStopped=true ...
```

Inside the captured Ray session logs (lift via `docker cp` from the
stopped container — works on Exited containers):

```
gcs_node_manager.cc:639: ... death reason = EXPECTED_TERMINATION,
                          death message = received SIGTERM
raylet/main.cc:1109: received SIGTERM. Existing local drain request = None
```

That's the signature: **clean SIGTERM-driven shutdown, no crash, no error,
no progress logs after**.

## Fixes

1. **Make the role idempotent against an Exited container with the
   reserved name**: prefer `docker start` over `docker run` when the
   container exists but isn't running. This is what the worker already
   did and what the head now does. See
   [runs/2026-04-27-ray-head-exited-postmortem.md](../runs/2026-04-27-ray-head-exited-postmortem.md).

2. **Use the operator surface that matches your intent**:
   - `just spark-vllm-stop` — for "stop the cluster, intentional, survive
     reboots". Stays stopped until you start it.
   - `just spark-vllm-start` / `spark-vllm-head-start` — explicit revive.
     Idempotent; no-op if already running.
   - `just spark-vllm-restart` / `spark-vllm-head-restart` — graceful
     bounce. Doesn't re-launch the detached `vllm serve` inside the head
     container; follow with `just spark-vllm-provision`.

## Do NOT

- Do **not** flip the policy to `--restart always` to "fix" this. That
  re-introduces the surprise-resurrection failure mode (operator stops
  the head to triage, host reboots, head is back up in the middle of the
  triage). `unless-stopped` is the right policy; the role just had to be
  idempotent.
- Do **not** rely on `docker run` retries when a same-named container
  already exists. Either `docker rm -f` first (recreate path,
  `vllm_stacked_container_recreate=true`) or `docker start` it (reuse
  path, what the role does by default).

## Cross-refs

- [runs/2026-04-27-ray-head-exited-postmortem.md](../runs/2026-04-27-ray-head-exited-postmortem.md) — the incident this concept is filed from.
- Docker docs — *“Container restart policies”*: see the wording on
  `unless-stopped` vs `always`.
