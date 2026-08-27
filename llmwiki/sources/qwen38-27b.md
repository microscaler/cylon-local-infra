---
title: Qwen3.8-27B model card + vLLM recipe
kind: source
status: active
tags: [qwen, vllm, hf, vision]
updated: 2026-08-17
sources:
  - https://huggingface.co/Qwen/Qwen3.8-27B
  - https://huggingface.co/Qwen/Qwen3.8-27B-FP8
  - https://recipes.vllm.ai/Qwen/Qwen3.8-27B
---

# Qwen3.8-27B (ingested 2026-08-17)

- Dense **27B** causal LM **with vision encoder** (not MoE). Architecture
  `Qwen3_5ForConditionalGeneration` + `vision_config`.
- Hybrid attention: 64 layers, **16 full attention / 48 Gated DeltaNet** linear.
- Native context **262144**, YaRN to 1M. MTP trained.
- Thinking on by default; `reasoning_effort` xhigh/medium/low; `preserve_thinking`.
- Official weights: BF16 **~52–67 GiB**. Official quant: **`Qwen/Qwen3.8-27B-FP8`**
  (~38 GiB, block-128 fine-grained FP8). NVFP4: `Inferact/Qwen3.8-27B-NVFP4`.
- vLLM recipe: **vLLM 0.17.0+** and **`transformers>=5.8.0`** (Qwen3-VL processor).
  Text path uses vLLM's own `Qwen3_5Config`.
- Tool/reasoning flags match our stack: `--tool-call-parser qwen3_coder`,
  `--reasoning-parser qwen3`. Vision: `--mm-encoder-tp-mode data`.
- Benchmarks beat **Qwen3.6-27B** (dense sibling), not our production
  **Qwen3.6-35B-A3B-FP8** MoE. Coding/agent deltas vs 3.6-27B are large
  (SWE-bench Pro 61.7 vs 53.5; DeepSWE 42.2 vs 13.3).
