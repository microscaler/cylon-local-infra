---
title: IPv6 black hole → vLLM ASGI hang
kind: concept
status: active
tags: [ipv6, networking, hf, asgi, failure-mode]
updated: 2026-04-18
related: [entities/nvidia1.md, entities/nvidia2.md]
first_observed: 2026-04
sources:
  - commit: f256450  # fix(vllm): circumvent ASGI hang caused by IPv6 blackhole on Hugging Face downloads
---

# IPv6 black hole → vLLM ASGI hang

## Symptom

vLLM starts, Ray is healthy, the engine appears to initialize, but the OpenAI API
**never binds `:8080` (or `:8000`)**. Tailing the journal shows no obvious error;
`httpx` / `urllib3` is stalled on a connection to `huggingface.co` (typically fetching
generation config during ASGI `startup`).

## Root cause

The LAN interface on the Spark (`enP7s7`) had an IPv6 default route that black-holed —
packets left, nothing came back, no ICMP. `httpx` / `requests` tried IPv6 first, waited
for its TCP connect to time out (~tens of seconds, compounded per outbound call),
during which the ASGI `startup` coroutine blocked. Uvicorn never moved to `startup
complete` and the TCP listener never opened.

## Affected hosts

- `nvidia1` — confirmed (host_vars sets `spark_lan_disable_ipv6_on_interface: enP7s7`).
- `nvidia2` — same LAN interface, same symptom, disabled for ops consistency.

## Fix

Disable IPv6 on the LAN interface. Ansible persists this via
`roles/spark_provision/tasks/lan_ipv6_sysctl.yml` and the per-host var
`spark_lan_disable_ipv6_on_interface: enP7s7`.

One-shot manual:

```
sudo sysctl -w net.ipv6.conf.enP7s7.disable_ipv6=1
```

QSFP interconnect (`enp1s0f0np0`) still has its `fe80::/10` link-local — only the LAN
interface's IPv6 is disabled.

## Additional belt-and-braces

`vllm_distributed_extra_env` previously carried `HF_HUB_OFFLINE: "1"` as a workaround
(commit `f256450`). It was dropped again once `spark_provision_hf: true` reliably
prefetches the weights + tokenizer + generation config; the env no longer helps and
can actively hurt when vLLM wants to resolve a symlink in the hub cache the first
time. See the diff on `sparks.yml` from 2026-04-18.

## Cross-refs

- Commit: `f256450 fix(vllm): circumvent ASGI hang caused by IPv6 blackhole on Hugging
  Face downloads` — added `HF_HUB_OFFLINE=1`.
- Follow-up: 2026-04-18 diff removes `HF_HUB_OFFLINE` in favor of the sysctl fix +
  prefetch.
