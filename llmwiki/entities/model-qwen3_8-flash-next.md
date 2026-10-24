---
title: nvidia/Qwen3.8-Flash-Next-NVFP4
kind: entity
status: active
tags: [qwen, model, moe, nvfp4, modelopt, kubernetes]
updated: 2026-10-24
sources:
  - https://huggingface.co/nvidia/Qwen3.8-Flash-Next-NVFP4
  - https://recipes.vllm.ai/Qwen/Qwen3.8-Flash-Next
  - ../../inventory/group_vars/sparks.yml
related:
  - ../entities/model-qwen3_8-27b.md
  - ../entities/hf-prefetch-service.md
  - ../entities/model-glm53-flash.md
  - ../concepts/gb10-uma-swappiness.md
---

# Qwen3.8-Flash-Next (NVIDIA NVFP4)

**Serving since 24 Oct 2026** on spark1+spark2 (TP=2) from Kubernetes:
shared-gitops LeaderWorkerSet `cylon/qwen38-flash-next`. It runs *instead of*
[Qwen3.8-27B-FP8](./model-qwen3_8-27b.md) — both groups need both GPUs, `:8000` and
most of the unified memory. Switch: `just cylon-model-switch model=flash-next|27b|none`
(a commit to shared-gitops `serving/active-model.yaml` + Flux reconcile).

| Field | Value |
|---|---|
| HF | `nvidia/Qwen3.8-Flash-Next-NVFP4` @ `fc694b54fb0174e0913e6adf86691ef85a4ead47` (123.6 GiB, 25 files) |
| Base | `Qwen/Qwen3.8-Flash-Next` (FP8 sibling `Qwen/Qwen3.8-Flash-Next-FP8`, 172.8 GiB) |
| Arch | `Qwen4ExpForConditionalGeneration` (`qwen4_exp`): 48 layers, hidden 2560, hybrid Gated DeltaNet + sparse (indexer) attention every 4th layer, 512 experts top-10, 1 MTP layer, vision tower |
| Size | 125B total / ~6B active MoE + **51B n-gram ("PLE"/engram) embedding**, FP8 |
| Quant | ModelOpt 0.46: routed experts NVFP4 W4A4 (group 16); attention / shared experts BF16; MTP experts 128×128 FP8; n-gram embedding per-tensor FP8 |
| Served names | `qwen3.8-flash-next`, `qwen3-flash`, `nvidia/Qwen3.8-Flash-Next-NVFP4` + the legacy aliases `gpt-4o-mini`, `qwen3`, `Qwen/Qwen3.8-27B-FP8`, `Qwen/Qwen3.6-35B-A3B-FP8`, `Qwen/Qwen3-Coder-Next-FP8` |
| Endpoints | `http://192.168.1.104:8000/v1` (leader, hostNetwork), Service `cylon/vllm:8000`, gateway `http://10.177.76.236/v1` |

## Engine and image — what worked

- **vLLM ≥ 0.30 is required** (recipe); NVIDIA's card needs ≥ commit `d4d703ca`. NGC tops
  out at 26.09 = vLLM 0.29-dev, and 26.04+ dropped Ray anyway. NGC is out.
- **Image: `vllm/vllm-openai:nightly-aarch64`** pinned by digest
  `sha256:39d9123969ef6c289c24746675e2e207d37527a208077d6fc4da044f8b8865f7`
  (= `nightly-c50aca14416ef29f6ed0ed256302f30646726dcd`, vLLM `0.32.1rc1.dev435+gc50aca144`,
  CUDA 13.0.2, torch 2.14.0+cu130, flashinfer 0.7.2, NCCL 2.30.7, ~10 GiB compressed).
  The `cu134-*` builds need a newer driver than the Sparks' 580.159 (CUDA 13.0) — don't.
  Probed on GB10 (12,1): registry has `Qwen4ExpForConditionalGeneration`; `_C` carries
  `sm_120f` family kernels (run on sm_121); a real NVFP4 CUTLASS GEMM runs.
  Chosen over building `eugr/spark-vllm-docker` from source: an upstream digest is
  reproducible and needs no hours-long build on a Spark; nothing GB10-specific was missing.
- **The upstream image has no Ray.** Instead of a derived image, the group uses vLLM's own
  multi-node executor: leader `vllm serve --distributed-executor-backend mp --nnodes 2
  --node-rank 0 --master-addr <leader fabric IP> --master-port 29521` (API + rank 0),
  worker the same command with `--node-rank 1 --headless` — the pattern the GLM/DeepSeek
  Docker stacks used. Proven first with TinyLlama TP=2 across both pods (65 s to serve).
- NCCL: `Using network IB` on both Cage A HCAs (`rocep1s0f0`, `roceP2p1s0f0`, 200G), GPU
  Direct RDMA disabled (same as the 27B group); all-reduce = PYNCCL (custom/symm-mem
  all-reduce unavailable on sm121 multi-node).
- NVFP4 MoE backend auto-selected: **`FLASHINFER_CUTLASS`** (FlashInfer autotunes
  `trtllm::fused_moe` tactics at start, skipping 2 unsupported on sm121).
- Image distribution: pulled once over Starlink on nvidia2 (`k3s ctr images pull` by digest,
  ~25 min), `k3s ctr images export` → rsync over QSFP → `import` on nvidia1; pinned
  (`io.cri-containerd.pinned=pinned`) on both. `roles/cylon_vllm_image`
  (`cylon_vllm_upstream_images`) pulls + pins it idempotently.

## Flags (shared-gitops `models/qwen38-flash-next`)

`--quantization modelopt --trust-remote-code --tensor-parallel-size 2 --kv-cache-dtype fp8
--attention-config.indexer_kv_dtype fp8 --enable-prefix-caching --enable-auto-tool-choice
--tool-call-parser qwen3_coder --reasoning-parser qwen3 --mm-encoder-tp-mode data
--engram-config '{"cpu_offload":false}' --max-num-batched-tokens 16384`, tunables in the env
ConfigMap: `--max-model-len 262144 --gpu-memory-utilization 0.75 --max-num-seqs 64`.

- **N-gram embedding offload off.** `EngramConfig.cpu_offload` defaults to true (pinned host
  memory, UVA lookup). The table is TP-sharded (~24 GiB/rank). On GB10 unified memory host
  and "GPU" are the same DRAM, but pinned host memory sits *outside*
  `--gpu-memory-utilization`, so the default over-commits a node by ~24 GiB. With
  `cpu_offload:false` the budget counts it.
- No MTP (needs unmerged vLLM PR #55513 for this checkpoint), no expert parallel (not needed
  for TP=2: expert width 640/2 = 320 is fine for NVFP4 group 16), no pipeline parallel (the
  n-gram embedding does not support PP).
- Mamba cache mode `align` (auto with prefix caching); attention block size 3136 tokens.

## Measured (24 Oct 2026)

| | 0.80 / 131072 / 32 seqs | 0.75 / 262144 / 64 seqs |
|---|---|---|
| Weights per rank | 63.45 GiB | 63.45 GiB |
| KV cache | 26.65 GiB = 3,329,228 tokens (25.4× at 131k) | 20.05 GiB = 2,813,318 tokens (10.7× at 262k) |
| MemAvailable while serving | spark1 8.9 GiB / spark2 11.6 GiB | spark1 15 GiB / spark2 17 GiB |
| Decode, 512 tokens, single stream | **28.9 / 30.2 tok/s**, TTFT 0.1–0.16 s | **30.8 / 30.7 tok/s**, TTFT 0.1 s |

- 27B-FP8 on the same pair: 13.1 tok/s → Flash-Next is **~2.3×** faster at decode.
- 17.7k-token prompt (needle in haystack): correct, 9.6 s cold / 5.0 s on the second start
  (~1.8–3.5k tok/s prefill).
- Non-thinking (`chat_template_kwargs: {enable_thinking: false}`): correct, 0.24 s. Thinking:
  reasoning separated into `reasoning_content`, correct answer. Tool call
  (`qwen3_coder` parser): correct `get_weather({"city":"Sofia","unit":"c"})`.
- No token corruption (cf. the ModelOpt GLM-NVFP4 issue, vLLM #54150): output coherent.
- Cold start: pod start → serving **~10 min** (weights 386 s at ~160 MB/s per rank — mmap
  slices from NVMe; engine init 169 s first time, 98 s with warm compile caches).
  Leader deleted → group recreated and serving in **531 s** (564 s at 262k; 27B: 116 s).

## Didn't work / watch out

- **NGC images** (26.03 has Ray but vLLM 0.17; 26.04–26.09 have no Ray and top out at 0.29).
- `cu134` nightlies: driver too old.
- **Heavy I/O beside a resident model wedges a Spark.** 24 Oct 15:26: a 10 GiB image pull +
  the HF download on nvidia1 while 27B served at ~0 GiB MemAvailable → node NotReady,
  sshd unanswering for ~30 min (it recovered by itself at 16:00, no reboot). Guardrail: no
  pulls/builds/downloads on a node with a model resident unless MemAvailable ≥ 16 GiB; park
  the group first; copy images between Sparks over the fabric, never twice over Starlink.
- Weight load is slow (~160 MB/s). Candidates: fastsafetensors (not in the upstream image →
  derived image), `--safetensors-load-strategy`, or a faster page-cache warm.
