# vllm_glm53_stacked

GLM-5.3-Flash NVFP4 on 2× DGX Spark using
`ghcr.io/tonyd2wild/vllm-glm53-flash:sm121-v8` (mp TP=2). Mutual exclusion
with NGC Qwen and Anemll DeepSeek via `spark_llm_profile: glm53_flash`.

See `docs/spark_llm_profiles.md` and `llmwiki/concepts/glm53-flash-on-gb10.md`.
