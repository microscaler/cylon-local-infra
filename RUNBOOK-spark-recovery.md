# Runbook — Spark recovery after abrupt power-off

When a Spark hard-powers-off (no shutdown sequence, journal cuts mid-line):

```bash
# 1. Power the Spark back on at the wall / button. Then:

# 2. Bring the cluster back into service:
just spark-vllm-api-restart        # restart `vllm serve` inside the head container
just spark-vllm-status             # confirm head + worker are Up
just spark-observability-probe     # confirm metrics + logs flowing to ms02
just spark-kernel-status           # confirm pin still durable
```

That's it for the standard recovery — total ~3 min. Containers come back via Docker `--restart unless-stopped`; only `vllm serve` (which runs as `docker exec -d` inside the head) needs the manual restart.

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
