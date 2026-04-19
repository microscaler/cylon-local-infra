---
title: eugr/spark-vllm-docker
kind: source
status: active
tags: [vllm, docker, spark, recipes]
updated: 2026-04-18
url: https://github.com/eugr/spark-vllm-docker
---

# spark-vllm-docker (vendored in `contrib/`)

Third-party Docker-first vLLM stack for the Spark, maintained by Evgeniy Gruzdev. We
vendor a subset under `contrib/spark-vllm-docker/` (see
[`docs/contrib-spark-vllm-docker.md`](../../docs/contrib-spark-vllm-docker.md)).

## What it contributes to our work

- **Validated recipes** for multiple models (see
  `/Users/casibbald/Workspace/microscaler/spark-vllm-docker/recipes/`) including
  MiniMax-M2-AWQ, Gemma 3, Llama 3.x, Qwen. Useful as ground truth when vLLM upstream
  regressions break our stacked path.
- **`launch-cluster.sh`** — a reference implementation of Ray head + worker + `vllm
  serve` that we partly mirror in `roles/vllm_stacked_container/`.
- **Patches** to `fastsafetensors` + `flashinfer` that matter on Spark aarch64 + CUDA 13:
  `fastsafetensors.patch`, `fastsafetensors_mxfp4.patch`, `flashinfer_cache.patch`.
- **`Dockerfile` / `Dockerfile.mxfp4`** — source of truth for what goes into a working
  Spark vLLM image when you don't want NGC.

## When to prefer this over NGC

- You need `mxfp4` / a model whose quant isn't in stock NGC.
- You need a specific `vllm` / `transformers` commit mix.
- NGC image is broken for your target model (has happened).

## When to prefer NGC

- You want minimum moving parts. NGC images are precompiled for Blackwell sm_121 and
  carry matched `torch` / `fastsafetensors` / `flashinfer`.
- You want Ansible-idempotent bring-up with no build step on the Sparks themselves.

## Relationship to our roles

- `roles/vllm_docker_stack/` — our Ansible wrapper around the `spark-vllm-docker`
  workflow (triggered by `spark_provision_docker_vllm: true`).
- `roles/vllm_stacked_container/` — our Ansible wrapper around the **NGC** stacked flow
  (triggered by `spark_provision_vllm_stacked_container: true`).

Both paths can coexist in the repo, but only one should be enabled at a time —
`vllm_stacked_container` asserts `spark_provision_vllm_stack: false` and stops the
bare-metal units when it takes over.
