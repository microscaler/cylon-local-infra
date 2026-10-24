# Runbook — Spark recovery after abrupt power-off

**Since 24 Oct 2026 vLLM on nvidia1/nvidia2 is served from Kubernetes** (ms02 k3s; shared-gitops stacks
`cylon-gpu` / `cylon-serving`: LeaderWorkerSet `cylon/qwen38-27b`, gateway `cylon/llm-gateway` on 10.177.76.236;
design doc §5.6/§17). There is nothing to restart by hand: when a Spark comes back, kubelet rejoins and the
LeaderWorkerSet rebuilds the pair (any pod failure recreates the whole group), serving again in ~2–3 min.

```bash
# 1. Power the Spark back on at the wall / button. Then:
just cylon-vllm-status             # group Running 1/1, /v1/models answering
just cylon-vllm-restart            # only if the group is wedged: recreates leader + worker
just spark-observability-probe     # confirm metrics + logs flowing to ms02
just spark-kernel-status           # confirm pin still durable
```

The Docker recipes (`spark-vllm-*-start/restart`, `spark-vllm-api-restart`, `spark-provision-recreate`) refuse on
cylon nodes. The old containers and the 26.03 image are kept for a few days: `just cylon-vllm-rollback-docker`
(emergency) / `just cylon-vllm-rollforward`.

## Forensics — open before triaging the crash

[GX10 abrupt-power-off hunt dashboard](http://192.168.1.189:3000/d/gx10-power-off-hunt). Default 3h lookback. The 30 min preceding the crash should be visible across GPU / SoC / RAS / vLLM / journald panels.

Crash signature for the GX10 platform bug ([forum #359785](https://forums.developer.nvidia.com/t/title-asus-ascent-gx10-gb10-hard-power-off-unclean-reboot-under-vllm-gpt-oss-120b-long-context/359785)):

- `up{cluster="cylon-sparks", host="<spark>"}` flat at 1, then a gap.
- All `rasdaemon_events_total{*}` zero.
- No matches for `(?i)(mlx5|MCE|panic|aer|oom|xid)` in journald grep.
- Last journal entry is mid-line (no shutdown sequence).

If those hold, it's the platform bug — see [llmwiki/runs/2026-05-02-nvidia1-abrupt-power-off-on-pinned-1008-kernel.md](./llmwiki/runs/2026-05-02-nvidia1-abrupt-power-off-on-pinned-1008-kernel.md) for the canonical postmortem and the falsified hypothesis tree.

## If dockerd fails to start (one-shot legacy state cleanup, both Sparks already done 2026-05-02)

Symptom: `dcgm-exporter.service` shows `dependency failed`; `systemctl status docker` shows `failed to start cluster component: --live-restore daemon configuration is incompatible with swarm mode`.

```bash
ansible <host> -i inventory/hosts.yml -b -m shell -a 'mv /var/lib/docker/swarm /var/lib/docker/swarm.bak.$(date +%F); systemctl reset-failed docker; systemctl start docker'
```

Then resume from step 2. Should not recur — both Sparks left swarm permanently 2026-05-02.

## Related runbooks

- `just --list` — all operator recipes (search `spark-*`).
- [`roles/vllm_stacked_container/README.md`](./roles/vllm_stacked_container/README.md) — head/worker container lifecycle (start, restart, stop, recreate).
- [`roles/spark_kernel/README.md`](./roles/spark_kernel/README.md) — kernel pin / OTA mask / how to switch kernels.
- [`roles/spark_observability/README.md`](./roles/spark_observability/README.md) — metrics + logs pipeline; how to verify each agent.
- [`llmwiki/index.md`](./llmwiki/index.md) — full incident history and concept pages.
