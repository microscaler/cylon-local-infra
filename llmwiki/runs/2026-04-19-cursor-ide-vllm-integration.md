# Cursor IDE → vLLM integration (Qwen3.5-35B-A3B-FP8)

**Date:** 2026-04-19 (late Sat UTC) / 2026-04-20 ~01:30 EEST on picolino

**Goal:** Wire the self-hosted vLLM FP8 endpoint (nvidia1:8000) into the user's
local Cursor IDE as a custom OpenAI-compatible model, so Cursor chat/agent can
use our Qwen3.5-35B-A3B-FP8 deployment instead of cloud models.

## Outcome: working end-to-end

- Public entrypoint: `https://subway-omit-quadrant.ngrok-free.dev/v1`
- Advertised model ids (both serve the same weights):
  - `qwen3` (Cursor-safe alias — no `/`, no `.`)
  - `Qwen/Qwen3.5-35B-A3B-FP8` (original HF repo id — kept for existing
    bench scripts, curl probes, the hf-prefetch daemon metadata, etc.)
- Cursor Base URL field points at the ngrok URL; model picker shows `qwen3`.
- Verified traffic path: Cursor (Electron) → Cursor's cloud backend →
  ngrok edge (`212.105.157.74`) → ngrok agent on nvidia1 → vLLM `localhost:8000`.
- First successful `POST /v1/chat/completions 200 OK` landed at 22:35:22 UTC.

## Cursor constraints discovered along the way

Confirmed against live Cursor behaviour (undocumented — treat as current, not
contract):

1. **Custom model name validator rejects `/`.** `Qwen/Qwen3.5-35B-A3B-FP8`
   triggers "AI Model Not Found. Model name is not valid: '…'" before Cursor
   makes any HTTP call. Fix: `vllm --served-model-name qwen3 …` so vLLM
   advertises a Cursor-friendly alias. We kept the HF repo id as a
   second served name so nothing downstream had to change.
2. **`/v1/models` probes go client-direct** from the Electron renderer to the
   configured Base URL. That's why the initial LAN probe (`http://nvidia1:8000/v1`)
   showed up as requests from `192.168.1.130` (picolino) in vllm-serve.log.
3. **`POST /v1/chat/completions` dispatches via Cursor's cloud backend.** A
   LAN / `192.168.*` base URL will never work for chat — the cloud backend
   cannot reach a private IP. Hence the ngrok tunnel.
4. **`message.reasoning` is dropped by Cursor.** vLLM's Qwen3 reasoning parser
   still emits it; Cursor just doesn't render it. Concrete impact: if the
   model spends `max_tokens` on thinking, the user sees an empty bubble.
   Workarounds: append `/no_think` to prompts, or put a
   "Do not reason, respond directly. /no_think" entry into Cursor Rules.
5. **Tab completion is hard-coded to Cursor's own models.** Our custom
   model is only available via Chat / Agent / Inline-Edit / Composer.
6. **API key field must be non-empty.** vLLM has no auth; `EMPTY` works.

## vLLM change (persisted)

File: `inventory/group_vars/sparks.yml`

Added two tokens at the top of `vllm_api_server_extra_args`:

```yaml
vllm_api_server_extra_args:
  # Cursor IDE compatibility: advertise a short alias at /v1/models so Cursor's
  # custom-model validator accepts it …
  - "--served-model-name"
  - "qwen3"
  - "Qwen/Qwen3.5-35B-A3B-FP8"
  - "--max-model-len"
  - "262144"
  …
```

`--served-model-name` is `nargs='+'` so `qwen3 Qwen/Qwen3.5-35B-A3B-FP8` are
both consumed until the next `--flag`. vLLM returns both at `/v1/models`
and accepts either as the `model` field in requests.

## Applied via

`ansible-playbook playbooks/cutover_roce.yml` — full recreate of Ray
head+worker containers; vllm relaunched with the new args. 48 s start-to-ready
(Ray + patches + fastsafetensors load of the 35 B FP8 weights). The playbook
is the recoverable path; an earlier attempt at an in-place
`docker exec pkill -TERM 'vllm serve'` + restart **hung** at
"Asynchronous scheduling is disabled" for 3+ min with zero log progress. The
clean `cutover_roce.yml` path is the only one to use going forward when
vllm args change.

## ngrok setup

Installed and configured on nvidia1 (dev box, root daemon):

- `/usr/local/bin/ngrok` — v3.37.6, arm64 static binary.
- `/root/.config/ngrok/ngrok.yml` — stores the agent authtoken.
- Launched ad-hoc with:
  ```
  sudo setsid ngrok http --url https://subway-omit-quadrant.ngrok-free.dev 8000
  ```
  Logs at `/var/log/ngrok.log`. **No systemd unit yet**, so the tunnel dies
  on reboot. Follow-up: `ngrok_tunnel` Ansible role + systemd service unit.

### ngrok gotcha: "already online" after account signup

First launch failed with `ERR_NGROK_334 … endpoint … is already online.` The
free-tier dev domain had an auto-provisioned **Cloud Endpoint** (not an agent
tunnel) on it from ngrok's signup flow, configured with an
`ai-gateway` traffic policy. Cleared via the ngrok REST API
(`ngrok api endpoints list` → then it was auto-removed the moment we queried).
Reserved domain itself cannot be deleted on free tier (`ERR_NGROK_458 "You
are not allowed to delete your dev domain"`), but that's fine — we want
to USE the reserved domain, not replace it.

### Interstitial behaviour

ngrok free tier shows a browser warning HTML on:
- GET requests
- Browser-like `User-Agent`
- No `ngrok-skip-browser-warning` header

Tested (`curl -A Mozilla/…`): GET with browser UA → HTML interstitial; POST
with browser UA → passes through as JSON. Cursor's actual UA passes through
cleanly in both directions (confirmed live). If that ever breaks, add an
ngrok traffic policy rule to short-circuit the warning on `/v1/*`.

## End-to-end verification (via tunnel, not LAN)

```
curl https://subway-omit-quadrant.ngrok-free.dev/v1/models
  → 200 OK, JSON with both served-model-name entries.
curl -d '{"model":"qwen3","messages":[…],"max_tokens":8}' …/v1/chat/completions
  → 200 OK, model echoed back as "qwen3", wall 1.3 s.
```

vllm-serve.log, extracted after Cursor's first real chat message:

```
212.105.157.74:0 - "POST /v1/chat/completions HTTP/1.1" 200 OK
212.105.157.74:0 - "GET  /v1/models       HTTP/1.1" 200 OK
212.105.157.74:0 - "POST /v1/chat/completions HTTP/1.1" 200 OK
212.105.157.74:0 - "GET  /v1/models       HTTP/1.1" 200 OK
```

(`212.105.157.74` is ngrok's edge address — expected for tunnel-relayed
traffic from Cursor's cloud backend.)

## Follow-ups

- [ ] **`ngrok_tunnel` Ansible role + systemd unit on nvidia1** so the tunnel
  survives reboots. Template the reserved URL from sparks.yml.
- [ ] **Decide on paid ngrok** ($10/mo) vs alternatives (Cloudflare Tunnel +
  Access, Tailscale Funnel). Free tier rate-limits (1 concurrent conn,
  4 tunnels/mo uptime cap) are going to bite sustained use.
- [ ] **Cursor Rules entry** for Qwen — something like "respond directly,
  do not reason, `/no_think`" — so users don't have to append it per message.
- [ ] **Investigate a light-weight Cursor feature-compat matrix** in this
  repo: what works (chat, agent tool calls, inline edit) vs what doesn't
  (tab, specific Composer-2 flows, native reasoning display). Useful when
  we pitch self-hosted inference internally.
- [ ] **Does `--served-model-name qwen3` break the existing benchmark
  harness?** `/tmp/bench.py`, `/tmp/bench_longctx.py`, `/tmp/think_suite.py`
  all send `model: "Qwen/Qwen3.5-35B-A3B-FP8"` — still works because that
  name is still advertised, but worth spot-checking on next bench run.
- [ ] **Drop an earlier-created local tail of `/root/vllm-serve.log`** (shell
  IDs 747402 and 718352) when done — they're backgrounded SSH sessions
  keeping `docker exec tail -F` alive on nvidia1.
