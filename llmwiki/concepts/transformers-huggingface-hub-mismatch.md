---
title: transformers ↔ huggingface_hub mismatch (is_offline_mode) — superseded
kind: concept
status: superseded
superseded_by: concepts/ngc-stacked-container-stack.md
superseded_on: 2026-04-18
tags: [python, pip, transformers, hf, failure-mode, historical]
updated: 2026-04-18
related:
  - concepts/bare-metal-venv-stack.md
  - entities/vllm-stacked-service.md
  - runs/2026-04-18-rip-out-bare-metal.md
first_observed: 2026-04-18
---

> **Can no longer trigger.** The bare-metal venv that hit this import error was
> deleted on 2026-04-18 in favour of the NGC container stack, which ships a
> pre-tested `transformers` + `huggingface_hub` pair per image tag. Kept in the wiki
> so we recognise the pattern if we ever regress to host-side pip.


# `ImportError: cannot import name 'is_offline_mode' from 'huggingface_hub'`

Active failure in the bare-metal venv stack, observed on `nvidia1` 2026-04-18.

## Symptom

`vllm-stacked.service` exits with status 1 every restart:

```
File ".../transformers/utils/hub.py", line 29, in <module>
    from huggingface_hub import (
ImportError: cannot import name 'is_offline_mode' from 'huggingface_hub'
(/opt/vllm/venv/lib/python3.12/site-packages/huggingface_hub/__init__.py)
```

Launch chain: `vllm.entrypoints.openai.api_server` → `vllm.config.model` →
`vllm.transformers_utils.config` → `from transformers import ...` → crash.

## Observed versions (2026-04-18, `/opt/vllm/venv`)

| Package | Version |
|---|---|
| `transformers` | `5.6.0.dev0` (from git main, pulled by `vllm_transformers_from_git: true`) |
| `huggingface_hub` | `0.36.2` |
| `vllm` | `0.19.0` |

## Why

Transformers' git `main` is racing toward v5.x. An in-flight refactor of HF hub helpers
dropped/renamed `is_offline_mode` on one side of the boundary without bumping
`huggingface_hub`'s required version pin. Our `vllm_package: "vllm>=0.19"` resolver
installed a combo that explodes at import.

This is a **recurring risk** whenever we pin `vllm_transformers_from_git: true`. The
knob exists because Gemma 4 needs transformers support that PyPI wheels sometimes lag
— but the cost is that any bad transformers commit breaks our entire stack.

## Fixes (ranked)

1. **Pivot to the NGC container stack**
   ([ngc-stacked-container-stack](./ngc-stacked-container-stack.md)). The NGC image
   carries a known-good matched set. Zero pip resolution on the host. **Recommended
   for 2026-04-18.**
2. **Pin transformers to a release tag** in `sparks.yml`:
   ```yaml
   vllm_transformers_from_git: true
   vllm_transformers_git_url: "git+https://github.com/huggingface/transformers.git@v4.58.0"
   ```
   then re-run `ansible-playbook playbooks/provision_sparks.yml --tags vllm` to
   reinstall. Risk: the pinned tag may lack Gemma-4 support.
3. **Pin `huggingface_hub<0.37`** alongside pinning transformers. Ugly but occasionally
   necessary during a transformers major-version transition.

## Do NOT

- Do not add `HF_HUB_OFFLINE=1` to "fix" this. That env only gates runtime network
  calls; this is a pure Python import error before any runtime logic runs.
- Do not downgrade `huggingface_hub` below what `safetensors` / `tokenizers` require.

## Artifacts

- `journalctl -u vllm-stacked` on `nvidia1` (excerpt in
  [runs/2026-04-18-state-of-cluster.md](../runs/2026-04-18-state-of-cluster.md)).
- Launcher `/opt/vllm/run-api-server-stacked.sh`.
