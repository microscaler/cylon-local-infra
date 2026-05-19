---
title: Sparks → ms02 observability pipeline (push, OTLP + Loki)
kind: concept
status: active
tags: [observability, prometheus, otlp, loki, dcgm, node_exporter, promtail, ms02, sparks]
updated: 2026-05-01
first_observed: 2026-05-01
related:
  - runs/2026-05-01-nvidia2-abrupt-power-off-vllm-long-context.md
  - concepts/8-spark-fabric-and-orchestrator.md
  - concepts/ngc-stacked-container-stack.md
---

# Sparks → ms02 observability pipeline

Push-based metrics + logs from each DGX Spark to the kind cluster on
**ms02 (192.168.1.189)**. Filed 2026-05-01 from the GX10 abrupt-power-off
investigation — converts our after-the-fact log forensics into live
timeseries with cross-host correlation.

## Topology

```
per Spark (host network, loopback bind):

  node_exporter            127.0.0.1:9100   ─┐
  dcgm-exporter            127.0.0.1:9400   ─┤
  vLLM /metrics            127.0.0.1:8000   ─┤
  rasdaemon textfile       (in node_exporter   │
    .../textfile/...prom)  textfile dir)    ─┘
                                              ▼
                                   otel-collector-contrib
                                   (systemd, scrape every 15s)
                                              │
                                              │ OTLP gRPC
                                              ▼
                              ms02:4317 (kind NodePort 31417)
                                              │
                                              ▼
                                       otel-collector
                                              │
                                              ├─ prometheus exporter ─► Prometheus
                                              └─ otlp/jaeger ─► Jaeger

per Spark (host network):

  journald (kernel + systemd + dockerd + every container,
            because dockerd has --log-driver=journald)
                                              │
                                              ▼
                                          promtail
                                          (systemd)
                                              │
                                              │ HTTP push
                                              ▼
                              ms02:3100 (kind NodePort 31310)
                                              │
                                              ▼
                                            Loki
```

**No LAN exposure of metrics endpoints.** All four local exporters bind
to `127.0.0.1`. Only the agents (otel-agent, promtail) reach the LAN —
outbound to ms02. UFW outbound is open by default.

## Components

### Per-Spark (`roles/spark_observability/`)

| Component | Version | Where | What it does |
|---|---|---|---|
| `node_exporter` | 1.8.2 | systemd, `/usr/local/bin/node_exporter`, runs as `node_exporter` system user | Host CPU/mem/net/disk/`hwmon`/`thermal_zone`/`textfile` collectors. Filtered systemd-unit collector tracks docker, vllm-*, hf-prefetch, ngc-image-sync, vllm-stack-autoupgrade, otel-agent, promtail, node_exporter, dcgm-exporter. |
| `dcgm-exporter` | `nvcr.io/nvidia/k8s/dcgm-exporter:3.3.5-3.4.1-ubuntu22.04` | systemd-managed Docker container, host network, `--gpus all`, `--cap-add SYS_ADMIN` | NVIDIA DCGM metrics: power, temp, util, memory, NVLink, throttle reasons, ECC errors. **`--runtime nvidia` is NOT used on DGX OS** — `--gpus all` alone is the right flag. |
| `otel-collector-contrib` | 0.96.0 | systemd, `/usr/local/bin/otelcol-contrib`, runs as `otel_agent` system user | Scrapes node_exporter + dcgm-exporter + vLLM /metrics every 15s. Attaches resource attributes (`cluster=cylon-sparks`, `host=nvidiaN`, `os_kernel=…`). Pushes OTLP gRPC to ms02. |
| `promtail` | 2.9.4 (matches Loki on ms02) | systemd, `/usr/local/bin/promtail`, runs as `promtail` system user (in `systemd-journal` + `docker` groups) | Reads journald (the only log source we need — see below), promotes `_systemd_unit`, `container_name`, `container_image`, `priority` to Loki labels. Pushes HTTP to ms02. |
| `rasdaemon-textfile.timer` | (systemd timer, 60s) | `/usr/local/sbin/rasdaemon-textfile.sh` writes to `/var/lib/node_exporter/textfile/rasdaemon.prom` | Parses `ras-mc-ctl --summary` into Prometheus counters: `rasdaemon_events_total{category=memory_ce,memory_ue,pcie_aer_correctable,pcie_aer_uncorrectable,pcie_aer_fatal,mce_records,extlog_records,devlink_records}`. node_exporter exposes them via its textfile collector. |

### ms02 (the kind cluster — `shared-kind-cluster`)

The cluster already had the right components — only **two changes** were
needed:

1. **NodePort services** for `loki`, `otel-collector`, and `prometheus`.
   The kind extraPortMappings in `kind-config.yaml`
   (`containerPort: 31310/31417/31418/31090 → hostPort: 3100/4317/4318/9090`)
   were forwarding to NodePorts that didn't exist (services were
   ClusterIP only) — `ms02:4317` reached docker-proxy → kind:31417 →
   `kube-proxy` refused → `connection refused`. Fixed by adding
   `type: NodePort` + explicit `nodePort:` to those Services in
   `k8s/observability/{loki,otel-collector,prometheus}.yaml`. Tilt
   re-applied automatically.

2. **`inventory/host_vars/ms02.yml` firewall**: added `3100`, `4317`,
   `4318` to `firewall_trusted_lan_tcp_ports` (192.168.1.0/24). The
   existing 7000-12000 range didn't cover them.

The OTel Collector pipeline (`otel-collector-config.yml`) was already
configured with `otlp` receiver → `prometheus` exporter (Prometheus
scrapes locally) and `otlp/jaeger`. The `logs` pipeline is **not yet
configured on ms02** — Promtail goes direct to Loki, bypassing
otel-collector. We can add an OTLP `logs` pipeline on ms02 later if we
want one-agent-everywhere uniformity (see "Open follow-ups").

## Why these choices

### Push, not pull

Matches the existing OTel-Collector-as-entry-point pattern. Doesn't
require Prometheus inside kind to reach the Sparks. Lets us add new
Sparks (Phase 5 of the
[8-spark plan](./8-spark-fabric-and-orchestrator.md)) without touching
central scrape config.

### Native systemd binaries (not containers) for the agents

Lower baseline RAM/CPU than Docker. Removes the dockerd dependency for
the observability path — observability survives a Docker restart. The
exception is `dcgm-exporter`, which only ships as a container on aarch64.

### Loopback binds for local exporters

Defence-in-depth — nothing on the LAN can scrape `:9100`/`:9400`/`:8000`
directly, only the local agent. A reduced attack surface for free.

### Single log source (journald), one shipper (Promtail)

`group_vars/sparks.yml` configures `dockerd` with `--log-driver=journald`
+ `tag: "{{.Name}}"` (so `CONTAINER_NAME` lands in the journal entry).
Result: kernel + systemd + dockerd + every container's stdout/stderr all
flow through a single Promtail scrape config. No second
`/var/lib/docker/containers/*-json.log` reader to maintain. Container
name shows up as a Loki label for easy filtering of
`vllm-ngc-ray-{head,worker-*}` streams.

### 15s scrape

Matches Prometheus default. Aggregate ingest is ~6 KB/s for our 4-source
× 2-host fleet — negligible storage on the kind `emptyDir` Loki/Prometheus.
Drop to 5s only during a focused crash-hunt window
(`-e spark_observability_scrape_interval=5s`).

## Operator surface

```bash
# Status (all 5 components on both Sparks, listener probes)
just spark-observability-status

# Apply the role
just spark-observability-apply              # both Sparks
just spark-observability-apply host=nvidia1 # one host

# Dry-run
just spark-observability-check

# End-to-end probe (local exporters HTTP 200, ms02 Prometheus has cluster=cylon-sparks targets, ms02 Loki has streams)
just spark-observability-probe
```

Direct PromQL / LogQL examples for cross-Spark queries:

```bash
# Anything labeled cluster=cylon-sparks, grouped by host:
curl -sG 'http://192.168.1.189:9090/api/v1/query' \
  --data-urlencode 'query=count by (host)({cluster="cylon-sparks"})'

# Last 5 log lines from any Spark:
curl -sG 'http://192.168.1.189:3100/loki/api/v1/query' \
  --data-urlencode 'query={cluster="cylon-sparks"}' \
  --data-urlencode 'limit=5'

# All vllm-ngc-ray-head logs in last 1h:
curl -sG 'http://192.168.1.189:3100/loki/api/v1/query_range' \
  --data-urlencode 'query={cluster="cylon-sparks", container_name="vllm-ngc-ray-head"}' \
  --data-urlencode 'start='$(date -u -v-1H +%s)000000000
```

## What this gives us for the GX10 abrupt-power-off investigation

This is the single highest-value tool for the
[2026-05-01 postmortem](../runs/2026-05-01-nvidia2-abrupt-power-off-vllm-long-context.md)
bisection. Concrete forensic capability that didn't exist before:

| Question | Source |
|---|---|
| Was the GPU drawing more power than usual just before the crash? | `DCGM_FI_DEV_POWER_USAGE{host="nvidia2"}` |
| GPU + SoC temps in the 30 min before the crash? | `DCGM_FI_DEV_GPU_TEMP`, `node_hwmon_temp_celsius` |
| Did EDAC / PCIe AER / MCE counters tick before the crash? | `rasdaemon_events_total` |
| What was vLLM doing at the moment of the crash? | `vllm:num_requests_running`, `vllm:num_requests_waiting`, `vllm:gpu_cache_usage_perc` |
| Did dockerd / kernel log anything unusual just before the unclean shutdown? | Loki: `{cluster="cylon-sparks"} |~ "(?i)(mlx5|MCE|panic|aer|oom)"` |
| Was the multi-Hermes-session amplification actually present at the time? | `vllm:num_requests_running` (concurrent in-flight count) |
| Does the kernel pin (`6.17.0-1008` vs `6.17.0-1014`) move MTBF? | Compare MTBF-between-incidents windows; `os_kernel` is a resource attribute on every metric. |

All of these now live as panels in the **GX10 abrupt-power-off hunt**
dashboard (next section).

## Grafana dashboards (shipped 2026-05-01)

Three dashboards live in
`shared-kind-cluster/k8s/observability/embedded/`, mounted into Grafana's
`Sparks` folder (provider config:
`grafana-dashboard-providers.yml`). All filter on
`cluster="cylon-sparks"` and provide a `host` template variable for
single-Spark drill-down.

| Dashboard | UID | Purpose |
|---|---|---|
| **DGX Spark Cluster — Overview** | `spark-cluster-overview` | Fleet status: up/down, kernel, uptime, RAS event counter; **GPU** power, temp (core + memory, with 75/85°C thresholds), util (SM + memcopy), SM clock; **host** CPU busy, UMA usage (Grace shared mem), load avg, hwmon temps; **network** rx/tx (excluding lo/veth/docker), filesystem usage at `/`, `/home`, `/var/lib/docker`; tracked **systemd** units (vllm-*, hf-prefetch, ngc-image-sync, vllm-stack-autoupgrade, otel-agent, promtail, dcgm-exporter) as a status table. |
| **vLLM — Performance** | `vllm-performance` | Throughput + concurrency (running, waiting, KV cache occupancy, success rate), token throughput (prompt + generation tok/s), TTFT/ITL/e2e latency p50/p95/p99 from histogram, queue + prefill p95, prefix cache hit rate, preemptions/sec. `host` + `model` template variables. |
| **DGX Spark — GX10 abrupt-power-off hunt** | `gx10-power-off-hunt` | **The forensic dashboard.** Status row (up, uptime, kernel, RAS counters by category, XID errors, PCIe replay) → GPU power (with 150/200/240W thresholds matching forum #359785's "sustained high power" trigger) and temps → rasdaemon stacked counters → vLLM concurrent in-flight (the multi-Hermes-session amplifier) + KV cache → **Loki logs panel** (`{cluster=cylon-sparks} \|~ "(?i)(mlx5\|MCE\|panic\|aer\|oom\|hardlockup\|softlockup\|nvidia.*xid\|vbios\|gpu fallen\|uncorrected\|hard.*power.*off\|abrupt\|reset)"`) → vLLM container logs (`container_name=~"vllm-ngc-ray-.*"`) → NVLink + QSFP/RoCE throughput. Default 3h lookback. Page links to the postmortem and NVIDIA forum #359785. |

### Critical OTel collector config gotcha (fixed at apply time)

Without `resource_to_telemetry_conversion: enabled: true` on the central
otel-collector's `prometheus` exporter, OTLP **resource attributes**
(set by the Spark otel-agents — `cluster`, `host`, `os_kernel`,
`os_distribution`) land in a separate `target_info` series instead of as
labels on every metric. This makes any `host=~"$host"` panel selector
return zero series. Fixed in
`shared-kind-cluster/k8s/observability/embedded/otel-collector-config.yml`:

```yaml
exporters:
  prometheus:
    endpoint: "0.0.0.0:9464"
    resource_to_telemetry_conversion:
      enabled: true
```

After this change every metric carries `cluster`, `host`, `os_kernel`,
`os_distribution` directly. The dashboards are written assuming these
labels exist.

### Critical Service-type gotcha (resolved + automated detection)

The kind extraPortMappings (`kind-config.yaml`:
`containerPort: 31300/31310/31417/31418/31090/31166 → hostPort:
3000/3100/4317/4318/9090/16686`) were forwarding to **NodePorts that
didn't exist**. The `grafana`, `loki`, `otel-collector`, `prometheus`,
`jaeger` Services were all `type: ClusterIP`. Result: every LAN-side
request to `ms02:<port>` reached `docker-proxy` → `kind:<NodePort>` →
kube-proxy refused → "connection refused". Tilt's existing
`port_forward` aliases (`9230:3000`, etc.) were the only LAN access
path before this and bound to 127.0.0.1 only — invisible to any
non-Tilt LAN client.

**Fixed** by setting `type: NodePort` + explicit `nodePort:` matching
the kind portmap on all five observability Services in
`shared-kind-cluster/k8s/observability/{grafana,loki,otel-collector,prometheus,jaeger}.yaml`.

**Systematic detection** going forward: `just ms02-cluster-portmap-check`
cross-references the kind extraPortMappings with the actual NodePort
allocations and reports any gap. Sample output:

```
kind:port     host:port   backing Service                     status
----------    ---------   ----------------------------------  ------
31300         3000        observability/grafana               OK
31310         3100        observability/loki                  OK
31417         4317        observability/otel-collector        OK
31418         4318        observability/otel-collector        OK
31090         9090        observability/prometheus            OK
31166         16686       observability/jaeger                OK
30080         8000        (none)                              MISSING — ms02:8000 will refuse
...
Summary: 8 OK  11 MISSING
```

**Remaining 8 MISSING entries** fall into two categories that don't need
fixing right now:

1. **App-team-reserved (5)** — `kind-config.yaml` reserves these
   ports for apps that aren't yet deployed in the cluster. The MISSING
   signal is the *correct* signal; it'll flip to OK as soon as the
   owning team deploys their service with `type: NodePort` + matching
   `nodePort:`:

   | kind:port | host:port | reserved for |
   |---|---|---|
   | `30080` | `8000` | PriceWhisperer API |
   | `31080` | `8080` | BRRTRouter Pet Store API |
   | `31404` | `4040` | Pyroscope (continuous profiling) |
   | `30497` | `7497` | PriceWhisperer mocks / Pact mocks |
   | `30999` | `9999` | PriceWhisperer mocks |

2. **Alt-port duplicates (3)** — historical second mappings for
   services that one app team likes on a non-standard host port
   (BRRTRouter prefers `:9090` for Prometheus, Hauliage prefers
   `:3002` for Grafana). The cluster only runs one of each, so these
   second mappings have no backing Service. Removing them from
   `kind-config.yaml` requires `kind delete cluster && kind create
   cluster` (kind doesn't support changing `extraPortMappings` on a
   running cluster) — too disruptive for now; they cause no harm
   beyond appearing in the probe output:

   | kind:port | host:port | duplicate of |
   |---|---|---|
   | `30002` | `3002` | `grafana` (already on `31300` → `3000`) |
   | `30091` | `9091` | `prometheus` (already on `31090` → `9090`) |
   | `31889` | `8889` | `otel-collector` Prometheus exposition (already on `9464` ClusterIP-internal; no LAN consumer needs it) |

Both categories are documented context, not action items. Every
OBSERVABILITY service is OK; every DATA service that exists is OK
(`postgres`, `redis`, `pact-broker`, `minio`). Run
`just ms02-cluster-portmap-check` after any change to
`kind-config.yaml` `extraPortMappings` or to a Service in
`k8s/observability/` / `k8s/platform-data/` to catch this
regression class before it bites.

### `--runtime nvidia` gotcha (resolved by registering the runtime)

Earlier discovered: DGX OS Docker doesn't register `nvidia` as a runtime
by default — only `runc` and `io.containerd.runc.v2`. Containers using
`--runtime nvidia` (the canonical NVIDIA NGC pattern) fail with
`unknown or invalid runtime name: nvidia`. The workaround was
`--gpus all` alone, which transparently invokes nvidia-container-toolkit.

**Fixed** by registering `/usr/bin/nvidia-container-runtime` as a Docker
runtime via `inventory/group_vars/sparks.yml` `docker_daemon_config`:

```yaml
docker_daemon_config:
  log-driver: journald
  log-opts:
    tag: "{{ '{{' }}.Name{{ '}}' }}"
  live-restore: true
  runtimes:
    nvidia:
      path: /usr/bin/nvidia-container-runtime
      runtimeArgs: []
```

Apply with `systemctl reload docker` (SIGHUP) — re-reads daemon.json
without dropping containers; new runtimes appear immediately. **Default
runtime stays `runc`** — `nvidia` is registered as an option for
explicit `--runtime nvidia` invocations. Verified on both Sparks:

```
Runtimes: io.containerd.runc.v2 nvidia runc
Default Runtime: runc
```

`roles/spark_observability/templates/dcgm-exporter.service.j2` now
uses the canonical `--runtime nvidia --gpus all` pattern matching
NVIDIA's official documentation. Both flags are necessary together:
`--runtime nvidia` selects the runtime; `--gpus all` requests GPU
device exposure.

## Open follow-ups
- [ ] **OTel logs pipeline on ms02**: add a `logs` pipeline to the
  central `otel-collector-config.yml` (OTLP receiver → Loki exporter)
  so we can also push logs via the otel-agent's `journald` receiver
  if we ever want one-agent-everywhere uniformity. Today Promtail
  goes direct to Loki, which is fine.
- [ ] **Alert rules** in Prometheus: `rasdaemon_events_total > 0`,
  `DCGM_FI_DEV_POWER_USAGE > 200W` (sustained), `up{cluster="cylon-sparks"} == 0`
  (Spark down). Wire to a webhook or Grafana Alerting.
- [ ] **Bumping versions**: defaults pin node_exporter 1.8.2,
  promtail 2.9.4, otelcol-contrib 0.96.0, dcgm-exporter
  3.3.5-3.4.1. Bump in `defaults/main.yml` when needed; SHA256s are
  also pinned so re-applying after a bump fails fast if the upstream
  artifact is wrong.
- [ ] **Phase 6 (8-Spark plan) integration**: when the
  [orchestrator1 (MS-A2)](./8-spark-fabric-and-orchestrator.md)
  arrives, the kind cluster relocates there. Inventory variable
  `spark_observability_ms02_host` becomes
  `spark_observability_orchestrator1_host` (or just retargets to the
  new IP); no role change needed.
- [ ] **DCGM profiling metrics**: requires `--cap-add SYS_ADMIN`
  (already set). Some additional `DCGM_FI_PROF_*` metrics may be
  available on GB10; not yet validated. Verify with `dcgmi profile -l`
  inside the container.

## Cross-refs

- `roles/spark_observability/README.md` — the role itself.
- `roles/spark_observability/{defaults,tasks,templates}/main.yml` —
  per-component installs.
- `inventory/group_vars/sparks.yml` — `docker_daemon_config` switches
  dockerd to the journald log driver (so Promtail's single scrape
  catches container logs).
- `inventory/host_vars/ms02.yml` — `firewall_trusted_lan_tcp_ports`
  exposes `3100`, `4317`, `4318` to the LAN.
- `shared-kind-cluster/k8s/observability/{loki,otel-collector,prometheus}.yaml` —
  Service `type: NodePort` (the gap that was blocking LAN access).
- [llmwiki/runs/2026-05-01-nvidia2-abrupt-power-off-vllm-long-context.md](../runs/2026-05-01-nvidia2-abrupt-power-off-vllm-long-context.md) —
  the postmortem this pipeline is filed from.
