---
title: zai-org/GLM-5.3-Flash
kind: source
status: active
tags: [glm, vllm, moe, gb10, nvfp4]
updated: 2026-08-29
sources:
  - https://huggingface.co/zai-org/GLM-5.3-Flash
  - https://recipes.vllm.ai/zai-org/GLM-5.3-Flash
  - https://huggingface.co/LibertAIDAI/GLM-5.3-Flash-NVFP4
  - https://huggingface.co/RedHatAI/GLM-5.3-Flash-NVFP4
  - https://github.com/tonyd2wild/GLM-5.3-Flash-NVFP4-DFlash2-2x-DGX-Spark
  - https://forums.developer.nvidia.com/t/glm-5-3-flash-running-on-2x-dgx-spark-sm-121-day-0-24-7-30-3-tok-s-with-mtp-5-two-silent-gb10-gotchas-worth-knowing/381433
related:
  - ../entities/model-glm53-flash.md
  - ../concepts/glm53-flash-on-gb10.md
---

# GLM-5.3-Flash (ingested 2026-08-29)

- Released 2026-08-26 (previewed as OpenRouter "Ox Alpha"). MIT. Arch
  `Glm5NextForConditionalGeneration` (`glm5_next`). Not yet in vLLM `main`
  (vllm#53906) — serve from `vllm/vllm-openai:glm53-flash*` or a patched
  GB10 image.
- **320B total / 18B active** MoE. 45 layers: 34 KDA linear-attn + 11 NoPE
  sparse MLA (`qk_rope_head_dim=0`). 288 routed + 1 shared experts; 8+1
  active. Native **1,048,576** context. Vision + video ViT. In-weight MTP.
- Default HF id `zai-org/GLM-5.3-Flash` is **native FP8** (~306–331 GiB).
  BF16 sibling ~640–772 GiB.   Community NVFP4: **default is now
  `RedHatAI/GLM-5.3-Flash-NVFP4`** (`compressed-tensors`, 11 shards,
  corruption-free). `LibertAIDAI/GLM-5.3-Flash-NVFP4` is ModelOpt
  weight-only (~181 GiB, 120 shards) and emits intermittent bad token
  IDs (vLLM #54150) — do not serve that one.
- Official vLLM: 0.29.0+, FlashInfer ≥0.6.17, image
  `vllm/vllm-openai:glm53-flash`. Recipe hardware picker: FP8 **386 GB**,
  NVFP4 **229 GB**, BF16 **772 GB**. Example: TP=8 H200 or TP=4 GB200 +
  `--tool-call-parser glm47 --reasoning-parser glm45 --enable-auto-tool-choice`.
  On **vLLM** GB10 reports, use `--reasoning-parser deepseek_r1` not `glm45`
  (glm45 silently empties the reply).
- 2× DGX Spark (sm_121) is **proven** with NVFP4 + patched image
  (`ghcr.io/tonyd2wild/vllm-glm53-flash:sm121-v8` / `…v11-dflash2`): TP=2,
  262k, fp8 KV, ~47 tok/s with DFlash2. Sibling recipe: 4× Spark TP=4 at 1M.
- Silent GB10 traps: NoPE MLA (`pe_dim==64` assert), weight-only NVFP4 MoE
  `input_scale=0` unless `--moe-backend marlin`, vision frontend OOM unless
  `--language-model-only`, top-k kernel crash past ~24k without SM121 patch.
