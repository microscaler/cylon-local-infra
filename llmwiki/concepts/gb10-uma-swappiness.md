---
title: GB10 UMA swappiness
kind: concept
status: active
tags: [gb10, uma, vllm, swap]
updated: 2026-09-07
related:
  - ../entities/nvidia1.md
  - ../entities/nvidia2.md
  - ../concepts/glm53-flash-on-gb10.md
---

# GB10 UMA swappiness

Each Spark reports ~121 GiB of unified LPDDR5X. The OS page cache and GPU
weights share that pool. Default `vm.swappiness=60` will steal vLLM/UVM pages
into `/swap.img` when a write storm (hf-prefetch of a ~200 GiB unused
checkpoint, `drop_caches` storms, long agent sessions) runs **while serving**.

Symptom: tok/s decays over days with **no vLLM deploy or restart**. GPUs sit
at ~96% util and ~15 W (waiting on page-ins). EngineCore CPU is high. KV
usage stays tiny.

Observed 2026-09-07 on the same Qwen3.8 Ray process (up since 2026-08-29):
operator had **>25 tok/s**, Friday **~11**, Monday isolated stream **4.7**.
nvidia1 had **9.2 GiB of 15 GiB swap in use**; nvidia2 had 2.5 GiB.
`RedHatAI/GLM-5.3-Flash-NVFP4` finished prefetch **2026-08-30 21:29** while
Qwen stayed up.

## Fix (applied 2026-09-07)

1. Persist `vm.swappiness=0` — `/etc/sysctl.d/99-spark-uma.conf`.
2. `sync` + `drop_caches` on both hosts.
3. `swapoff -a` then `swapon -a`.
4. Tagged Qwen Ray recreate.

Result: both hosts swappiness 0, swap 0B, `/health` 200. Warm isolated
stream **13.1 tok/s** decode / TTFT 0.18 s (pre-fix isolated was 4.7).
`spark_assert_uma_swappiness: true`. Recover stays off on ordinary
provision. Operator recipes: `just spark-uma-*`. Runbook:
[`docs/provision_sparks.md`](../../docs/provision_sparks.md).

Do not prefetch a large unused hub tree onto the same UMA as a live serve.
Clock lock and YaRN 524288 were already present at 25 tok/s; they are not
the Friday cliff.
