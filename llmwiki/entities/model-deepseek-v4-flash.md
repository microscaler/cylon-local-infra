---
title: deepseek-ai/DeepSeek-V4-Flash-0731
kind: entity
status: stale
tags: [deepseek, model, moe, fp4, dspark]
updated: 2026-08-29
sources:
  - ../../inventory/group_vars/sparks.yml
  - ../../docs/spark_llm_profiles.md
related:
  - ../entities/model-qwen3_8-27b.md
  - ../entities/model-glm53-flash.md
  - ../entities/hf-prefetch-service.md
---

# DeepSeek-V4-Flash-0731

**Parked 2026-08-30.** Weights reached `ready` (156G, snapshot
`7872f01…`) on both Sparks; Anemll image never landed (GHCR EOF).
Operator dropped the hub trees and returned production to Qwen3.8.
Role stays wired; do not prefetch unless asked.

| Field | Value |
|---|---|
| HF | `deepseek-ai/DeepSeek-V4-Flash-0731` |
| Revision pin | `9e165c30e2704aec5d9d593cce3eebd58bbef1cb` |
| Served name | `deepseek-v4-flash-0731` |
| Size | ~155 GiB / ~167 GB (48 shards) |
| Stack | Anemll `ghcr.io/anemll/dspark-vllm-gx10:0.1.1` mp TP=2 |
| API | `:8888` (not `:8000`) |
| Role | `roles/vllm_dspark_stacked` |

Was **not** on either Spark hub when GLM was ditched (nvidia2 had only
168G free with GLM still cached). GLM hub deleted; prefetch started on
nvidia1.

Cutover (avoid NFS chown):

```bash
ansible-playbook playbooks/provision_sparks.yml -l sparks \
  --tags vllm_dspark_stack,spark_assert \
  -e spark_llm_profile=deepseek_v4_flash \
  -e vllm_dspark_recreate=true
```
