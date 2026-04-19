---
title: NVIDIA/dgx-spark-playbooks
kind: source
status: active
tags: [nvidia, spark, playbooks, reference]
updated: 2026-04-18
url: https://github.com/NVIDIA/dgx-spark-playbooks
local_clone: /Users/casibbald/Workspace/microscaler/dgx-spark-playbooks
---

# NVIDIA dgx-spark-playbooks

NVIDIA's official collection of "playbooks" (really guided walkthroughs, not Ansible) for
running workloads on DGX Spark. Cloned locally at
`/Users/casibbald/Workspace/microscaler/dgx-spark-playbooks`.

## Directories we mine for patterns

| Dir | Why it matters to us |
|---|---|
| `nvidia/connect-two-sparks/` | QSFP cabling, link-local IP, interconnect sanity checks; source for `concepts/spark-interconnect.md`. |
| `nvidia/nccl/` | `all_gather_perf` across two Sparks; mirrored in `roles/nccl_sparks/` + `playbooks/nccl_sparks.yml`. |
| `nvidia/vllm/` | Bare-metal + container walkthroughs; source for both our stacks. |
| `nvidia/ollama/`, `nvidia/llama-cpp/`, `nvidia/lm-studio/`, `nvidia/open-webui/` | Alternative local-LLM runners if vLLM is unviable for a given model / day. |
| `nvidia/nim-llm/` | NVIDIA NIM (NGC microservices). Alternative to `vllm serve` for hosted-style OpenAI endpoints. |
| `nvidia/trt-llm/`, `nvidia/sglang/` | Other serving engines to consider when vLLM regresses. |
| `nvidia/nvfp4-quantization/`, `nvidia/speculative-decoding/` | Performance path — future tuning. |

## Status: reference only

We intentionally do **not** `git submodule` this into `cylon-local-infra`. We mirror the
patterns that matter into our Ansible roles + document the decisions here.

## Cross-refs

- [concepts/ngc-stacked-container-stack.md](../concepts/ngc-stacked-container-stack.md)
- [concepts/bare-metal-venv-stack.md](../concepts/bare-metal-venv-stack.md)
- [concepts/spark-interconnect.md](../concepts/spark-interconnect.md)
