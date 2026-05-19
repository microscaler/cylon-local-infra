# `spark_observability`

Push-based metrics + logs from each DGX Spark to the kind cluster
observability stack on **ms02 (192.168.1.189)**. Designed for the GX10
abrupt-power-off investigation
([llmwiki/runs/2026-05-01-nvidia2-abrupt-power-off-vllm-long-context.md](../../llmwiki/runs/2026-05-01-nvidia2-abrupt-power-off-vllm-long-context.md))
and ongoing vLLM observability.

## Architecture

```
per-Spark (host network, loopback bind):
  node_exporter           127.0.0.1:9100   ─┐
  dcgm-exporter           127.0.0.1:9400   ─┼── otel-agent (systemd, scrape every 15s)
  vLLM /metrics           127.0.0.1:8000   ─┘                 │
                                                              │ OTLP gRPC
                                                              ▼
                                                       ms02:4317

  rasdaemon → /var/lib/node_exporter/textfile/rasdaemon.prom
              (read by node_exporter --collector.textfile, ships via above)

  journald (kernel + systemd + dockerd + every container) ── promtail (systemd)
                                                              │
                                                              │ HTTP push
                                                              ▼
                                                       ms02:3100/loki/api/v1/push
```

**No LAN exposure** — all local exporters bind to `127.0.0.1`. Only the
agents (otel-agent, promtail) talk outbound to ms02. UFW outbound is open
by default.

## Inventory flags

| Flag | Default | Effect |
|---|---|---|
| `spark_observability_enabled` | `true` | Master toggle. `false` skips everything. |
| `spark_observability_node_exporter_enabled` | `true` | Install node_exporter systemd unit. |
| `spark_observability_dcgm_exporter_enabled` | `true` | Pull NVIDIA's DCGM Docker image and run as a systemd-managed container. |
| `spark_observability_otel_agent_enabled` | `true` | Install otelcol-contrib agent (the OTLP push). |
| `spark_observability_promtail_enabled` | `true` | Install promtail (the Loki push). |
| `spark_observability_rasdaemon_textfile_enabled` | `true` | Install the rasdaemon → node_exporter textfile shipper (60s timer). |
| `spark_observability_scrape_interval` | `15s` | Single setting controls all three Prometheus scrapes the agent does. Drop to `5s` for a focused crash-hunt window. |
| `spark_observability_ms02_host` | `192.168.1.189` | ms02 LAN IP. |

Versions are pinned in `defaults/main.yml` (with SHA256 verification on
download). Bump when needed.

## What gets installed where

| Component | Install path | Run as | Listen |
|---|---|---|---|
| node_exporter (1.8.2) | `/usr/local/bin/node_exporter` + `node_exporter.service` | `node_exporter` system user | `127.0.0.1:9100` |
| dcgm-exporter | NGC Docker image, host-network container | root (Docker host) | `127.0.0.1:9400` |
| otel-collector-contrib (0.96.0) | `/usr/local/bin/otelcol-contrib` + `otel-agent.service` + config in `/etc/spark-observability/otel-agent.config.yaml` | `otel_agent` system user | (no listener — outbound only) |
| promtail (2.9.4) | `/usr/local/bin/promtail` + `promtail.service` + config in `/etc/spark-observability/promtail.config.yaml` | `promtail` system user (in `systemd-journal` + `docker` groups) | `127.0.0.1:9080` (HTTP debug; outbound is the real path) |
| rasdaemon textfile shipper | `/usr/local/sbin/rasdaemon-textfile.sh` + systemd timer (60s) | root (oneshot) | (writes `.prom` file) |

## Apply

```bash
# Full role on both Sparks:
ansible-playbook playbooks/provision_sparks.yml --tags spark_obs

# One component (e.g. just promtail):
ansible-playbook playbooks/provision_sparks.yml --tags spark_obs_promtail

# Operator surface:
just spark-observability-status     # status of all 5 components on both Sparks
just spark-observability-apply      # apply role to both Sparks
just spark-observability-probe      # confirm metrics + logs are landing on ms02
```

## How to verify it's working

```bash
# 1. Local exporters answer (on the Spark itself, via SSH):
curl -s http://127.0.0.1:9100/metrics | head           # node_exporter
curl -s http://127.0.0.1:9400/metrics | head           # dcgm-exporter
curl -s http://127.0.0.1:8000/metrics | head           # vLLM (when API is up)

# 2. otel-agent is pushing — check journal for OTLP errors:
journalctl -u otel-agent.service --since '5 min ago' | grep -iE 'error|refus'

# 3. promtail is pushing — check journal for Loki push errors:
journalctl -u promtail.service --since '5 min ago' | grep -iE 'error|refus'

# 4. ms02 Prometheus has Spark metrics (run from anywhere on LAN):
curl -s 'http://192.168.1.189:9090/api/v1/query?query=up{cluster="cylon-sparks"}' | jq

# 5. ms02 Loki has Spark logs (LogQL):
curl -s 'http://192.168.1.189:3100/loki/api/v1/labels'
curl -sG 'http://192.168.1.189:3100/loki/api/v1/query' \
  --data-urlencode 'query={cluster="cylon-sparks"}' --data-urlencode 'limit=5' | jq
```

## Disable / uninstall

Per-component:

```yaml
spark_observability_promtail_enabled: false
```

then re-run with `--tags spark_obs` — the role does not auto-uninstall;
you'd `systemctl disable --now <unit>` and `apt-mark` cleanup manually.

Master kill:

```yaml
spark_observability_enabled: false
```

stops the role doing anything new but leaves existing units running.
For a true uninstall, use the operator path (manual; this is rarely needed
during development).

## Why these choices

- **Push, not pull**: matches the existing OTel-Collector-as-entry-point
  pattern in the cluster; doesn't require Prometheus inside kind to reach
  the Sparks; lets us add new Sparks without changing central config.
- **Native systemd binaries (not containers) for the agents**: lower
  baseline RAM/CPU than running otel-collector and promtail in Docker;
  removes the dockerd dependency for the observability path itself
  (so observability survives a Docker restart).
- **DCGM in a container**: NVIDIA's official aarch64 distribution path;
  building from source is brittle on DGX OS.
- **Loopback binds**: defence-in-depth — nothing from the LAN can scrape
  these endpoints, only the local agents.
- **Single log path (journald → Promtail → Loki)**: dockerd is
  configured (in `group_vars/sparks.yml`) with `--log-driver=journald`,
  so kernel + systemd + dockerd + every container's stdout/stderr all
  flow through one Promtail scrape config. No second `/var/lib/docker/
  containers/*-json.log` reader to maintain.
- **15s scrape**: matches Prometheus default; ~6 KB/s aggregate ingest.
  The next-crash forensic window is 30 minutes wide, so 15s resolution
  is more than enough; drop to 5s only during a focused hunt.

## Related

- [llmwiki/concepts/sparks-observability-pipeline.md](../../llmwiki/concepts/sparks-observability-pipeline.md) —
  end-to-end design + Grafana dashboard inventory.
- [llmwiki/runs/2026-05-01-nvidia2-abrupt-power-off-vllm-long-context.md](../../llmwiki/runs/2026-05-01-nvidia2-abrupt-power-off-vllm-long-context.md) —
  the GX10 abrupt-power-off postmortem this pipeline is filed from.
- `roles/spark_kernel` — kernel pin / OTA mask (related but
  independent).
- ms02-side: `inventory/host_vars/ms02.yml` LAN firewall now allows
  `4317`, `4318`, `3100` from the home subnet.
- shared-kind-cluster: `k8s/observability/` — ms02-side cluster manifests.
