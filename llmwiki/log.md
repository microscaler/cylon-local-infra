# llmwiki — log

Append-only timeline. One line per entry so `grep "^## \[" log.md | tail -N` always works.

## [2026-04-18] bootstrap | llmwiki scaffold created
Seeded `AGENTS.md`, `index.md`, `log.md`, and the initial entity / concept / source
pages from the repo's existing `docs/`, the uncommitted `roles/vllm_stacked_container/`
role, recent git log, and a live diagnosis of `nvidia1` + `nvidia2`. Trigger was an
operator request to stop forgetting what worked and what did not; blueprint is the
Karpathy LLM-wiki gist ([sources/karpathy-llm-wiki.md](./sources/karpathy-llm-wiki.md)).

## [2026-04-18] ingest | karpathy-llm-wiki gist
Filed the gist at `sources/karpathy-llm-wiki.md`. Established the three-layer model
(raw sources / wiki / schema) and the ingest / query / lint / run workflows in
`AGENTS.md`. No entity pages touched.

## [2026-04-18] run | hf-prefetch-tinyllama-proof | success
End-to-end validation of the hf-prefetch daemon with TinyLlama (2.2 GB). Also
fixed the "cache_bytes frozen at 0" gap by adding a progress heartbeat thread
to `hf_download()` that walks the cache subtree every 5 s and updates
`state.json` with live `cache_bytes` + `bytes_per_sec`. `StateStore` is now
thread-safe (`threading.Lock` around set/save). Observed: TinyLlama
downloaded in 2:45, then auto-rsync'd to `nvidia@169.254.37.109` (nvidia2
over QSFP) at **489 MB/s**, state flipped to `ready`, next reconcile was a
clean no-op (`summary: {'ready': 1}`). Both hosts' `hf cache scan` report
`TinyLlama/TinyLlama-1.1B-Chat-v1.0 model 2.2G 10 files`. Full timeline +
state schema:
[runs/2026-04-18-hf-prefetch-tinyllama-proof.md](./runs/2026-04-18-hf-prefetch-tinyllama-proof.md).

## [2026-04-18] run | hf-prefetch-service | in-progress
Replaced ansible-driven blocking HF downloads with a long-running systemd
daemon (`hf-prefetch.service`, leader-only) that reads
`/etc/hf-prefetch/config.yaml`, downloads each model **once** via the NGC
`hf` CLI, then `rsync`s the hub subtree to every peer Spark over the QSFP
interconnect. State in `/var/lib/hf-prefetch/state.json` is operator-pollable
with `jq`. New role: `roles/hf_prefetch_service/` (Python stdlib + pyyaml + a
fresh j2 template); retired `roles/hf_spark/`. Two bugs caught + fixed on
first deploy: (a) Jinja `~` / `| to_json` operator-precedence quirk
corrupted `cache_dir`, (b) `docker run --rm --name X` CLI was killable but
left the daemon-managed container alive, causing name collisions on
service restart — daemon now `docker stop`s the named container on SIGTERM
and `docker rm -f`s defensively at the start of each download. Live state
on 2026-04-18 08:51 EEST: Qwen3-30B-A3B-Instruct-2507 `status=downloading`,
12.1 GB resumed across 8 safetensor shards. Full diagnostic path + follow-ups:
[runs/2026-04-18-hf-prefetch-service.md](./runs/2026-04-18-hf-prefetch-service.md).
New entity page: [entities/hf-prefetch-service.md](./entities/hf-prefetch-service.md).

## [2026-04-19] run | workspace-sync (Mac → ms02) | success
Shipped `playbooks/sync_workspace.yml` — additive rsync of operator's Mac
`~/Workspace` → `ms02:/home/casibbald/Workspace/`, **never** passes
`--delete`, so local disk-reclaim deletions are not propagated and ms02
stays a superset. Three bugs caught + fixed on dry-run:
(1) macOS `/usr/bin/rsync` is openrsync (protocol 29) which rejects
`--info=progress2` / `--chown` / `--human-readable` — playbook now auto-
detects GNU-vs-openrsync at the controller and applies reduced flag set +
post-sync `chown -R` for the openrsync case;
(2) `delegate_to: localhost` leaked `ansible_host=localhost` into the rsync
destination — fixed by capturing `_workspace_sync_remote_{host,user}` on a
non-delegated task before use;
(3) Ansible strict-UTF8 deserializer choked on a filename with a non-UTF8
byte — fixed by redirecting full rsync stdout to
`/tmp/sync_workspace-<host>.rsync.log` and returning only a bounded,
`tr -cd '[:print:][:space:]'`-sanitized tail to Ansible.
Real run: **551,438 files**, **52,799 transferred**, **~3.4 GB on wire**,
21 min, speedup 18.7×. ms02 post-sync is **115 GB** (Mac source was 67.4 GB)
— ~48 GB of remote-only files preserved correctly. New playbook +
[concepts/workspace-sync.md](./concepts/workspace-sync.md) +
[runs/2026-04-19-workspace-sync.md](./runs/2026-04-19-workspace-sync.md).

## [2026-04-19] run | autoupgrade-armed-qwen-queued | success
Follow-through on the three post-26.03 next steps: (1) armed the vLLM
autoupgrade daemon (`vllm_autoupgrade_enabled: true`); (2) made inventory
match the actual running stack (`vllm_default_model: TinyLlama`,
`vllm_api_server_extra_args: []`); (3) queued the real Qwen targets on the
hf-prefetch daemon in priority order (`Qwen/Qwen3.6-35B-A3B` primary,
`Qwen/Qwen3-Coder-30B-A3B-Instruct` fallback). Post-push state: autoupgrade
`enabled=true, status=ready, current=26.03` (no newer candidate, idle until
NGC publishes 26.04+); hf-prefetch shows Qwen3.6 actively downloading with
TinyLlama preserved as `ready`; ngc-image-sync unchanged. The 26.01 → 26.03
cutover earlier today proved the exact docker command sequence the daemon
will emit, so arming it is a tightening (adds 1 h stabilization + 5 min
quiet-window gates) not a loosening. Full write-up:
[runs/2026-04-19-autoupgrade-armed-qwen-queued.md](./runs/2026-04-19-autoupgrade-armed-qwen-queued.md).

## [2026-04-19] run | 26.03-py3-upgrade | success
First live image cutover on the stacked Spark pair: bounced
`nvcr.io/nvidia/vllm:26.01-py3` → `26.03-py3` using the trusted
`roles/vllm_stacked_container` path with `-e vllm_stacked_container_recreate=true`,
plus `-e` overrides for `vllm_default_model=TinyLlama/TinyLlama-1.1B-Chat-v1.0`
and empty `vllm_api_server_extra_args` (inventory still aspirationally points
at Qwen3 which isn't cached yet, and 32768 max-model-len wouldn't fit
TinyLlama's 2048). Ansible run: ~58 s. vLLM engine warmup on 26.03: ~45 s
after. `/v1/chat/completions` returned `{"content":"UPGRADED"}`. `/metrics`
exposes the exact counters the autoupgrade daemon's quiet-window gate reads
(`vllm:num_requests_running`, `num_requests_waiting`, `request_success_total`).
After `systemctl restart vllm-stack-autoupgrade`, daemon re-observed
`current_image=nvcr.io/nvidia/vllm:26.03-py3` and stayed `idle` (no newer
candidate) — exactly the baseline behaviour we want before arming it with
`vllm_autoupgrade_enabled: true`. Full write-up:
[runs/2026-04-19-26.03-py3-upgrade.md](./runs/2026-04-19-26.03-py3-upgrade.md).

## [2026-04-19] run | vllm-stack-autoupgrade-service | success
Operator directive: auto-cut over to a newer image + restart services,
gated on "5 min window of quietness on the LLM API". Shipped as
`roles/vllm_stack_autoupgrade/` — a third leader-only systemd daemon
alongside `hf-prefetch` and `ngc-image-sync`. Preconditions for promotion:
(1) operator opt-in `enabled: true`, (2) candidate tag newer than running,
(3) `ngc-image-sync` marked it ready for ≥ `stabilization_sec` (default
1 h), (4) vLLM `/metrics` shows zero running + zero waiting + unchanged
`request_success_total` across a `quiet_window_sec` window (default 5 min,
sampled every 30 s), (5) quiet achieved within `max_wait_for_quiet_sec`
(default 24 h) else back off. On promotion: capture head + peer specs via
`docker inspect`, capture `vllm serve` argv via `pgrep` inside head, bounce
peer workers → head → new head with captured spec and new image, wait for
Ray GCS, start new peer workers, wait for `ray status` to show all nodes,
re-exec `vllm serve` via `docker exec -d` with captured argv, wait for
`/v1/models` 200. No auto-rollback v1. Safety-default: service installed
but `enabled=false`; first state shows correctly-parsed `current_image:
nvcr.io/nvidia/vllm:26.01-py3` and idle. New entity page:
[entities/vllm-stack-autoupgrade-service.md](./entities/vllm-stack-autoupgrade-service.md).
Full diagnostic:
[runs/2026-04-19-vllm-stack-autoupgrade-service.md](./runs/2026-04-19-vllm-stack-autoupgrade-service.md).

## [2026-04-19] run | ngc-image-sync-service | success
Operator directive: a Python service (same shape as hf-prefetch) that polls
NGC weekly for new `nvcr.io/nvidia/vllm:YY.MM-py3` tags, pulls them on
nvidia1, and replicates via `docker save | ssh | docker load` over QSFP to
peers. Shipped as `roles/ngc_image_service/` — stdlib urllib for Docker
Registry v2 token-auth + pagination, stdlib subprocess for pipelines,
pyyaml config, pollable JSON state at `/var/lib/ngc-image-sync/state.json`.
First poll discovered 54 tags, kept `['26.03-py3', '26.02-py3']` per
`keep_latest: 2 + always_keep: ['26.03-py3']`. Caught + fixed: first pass
naively `docker save`'d 26.03-py3 to nvidia2 even though nvidia2 already had
it (25 GB no-op). Added a peer-ID fast-path (`docker image inspect
--format {{.Id}}` on both sides, skip if equal). Post-fix: 26.03-py3 flagged
`"present on all configured nodes"` with zero transfer; 26.02-py3 is
pulling + syncing now. New entity page:
[entities/ngc-image-sync-service.md](./entities/ngc-image-sync-service.md).
Full diagnostic path + follow-ups:
[runs/2026-04-19-ngc-image-sync-service.md](./runs/2026-04-19-ngc-image-sync-service.md).

## [2026-04-18] run | tp2-nccl-solved | success
Cross-node TP=2 is **live**. Root cause: NGC image ships 4 NCCL net plugins that
auto-load and abort on GB10+ConnectX link-local before the NCCL logger starts —
`NCCL_NET_PLUGIN=none` bypasses the plugin path and forces the built-in socket
transport. Also learned: `docker exec -e` does NOT propagate env to Ray actors —
container env must come from `--env-file` / `-e` at `docker run`, so every NCCL
experiment requires container recreation. Second gotcha: nvidia2 had no TinyLlama
in HF cache, rank-1 hung in `xet_get`; `tar|ssh|tar -x` from nvidia1 fixed it.
Baked `NCCL_NET_PLUGIN=none` (+ `NCCL_P2P_DISABLE=1`, `NCCL_CUMEM_ENABLE=0`,
`NCCL_SHM_DISABLE=1` defensively) into `vllm_distributed_extra_env`, added
`/root/vllm-serve.log` redirection to the role. Smoke: `/v1/chat/completions`
returns `STACKED`. Full diagnostic path + follow-ups:
[runs/2026-04-18-tp2-nccl-solved.md](./runs/2026-04-18-tp2-nccl-solved.md).
Concept [ncclcommInitRank-abort-tp2](./concepts/ncclcommInitRank-abort-tp2.md) marked
`superseded` → pointer to
[nccl-on-spark](./concepts/nccl-on-spark.md) which now carries the authoritative env table.

## [2026-04-18] run | ngc-container-bringup | partial-success
Container stack green end-to-end: `vllm-ngc-ray-head` (leader) + `vllm-ngc-ray-worker-nvidia2`
(follower) on `nvcr.io/nvidia/vllm:26.01-py3`, Ray cluster healthy (2 nodes, 2 GPUs),
TinyLlama **TP=1** serves `http://nvidia1:8000/v1/chat/completions` (returned `PONG`).
Fixed three role bugs along the way (`set -o pipefail` under dash; `fastsafetensors`
not in image; Gemma-4 unknown to NGC 26.01 transformers). Pivoted target model
`gemma-4-31B → gemma-3-27b-it → Qwen2.5-32B` (gated access, image lag, prefetching).
**TP=2 cross-node NCCL aborts in `ncclCommInitRank`** — filed as
[concepts/ncclcommInitRank-abort-tp2.md](./concepts/ncclcommInitRank-abort-tp2.md);
TP=1 unaffected. Full timeline + follow-ups:
[runs/2026-04-18-ngc-container-bringup.md](./runs/2026-04-18-ngc-container-bringup.md).

## [2026-04-18] run | rip-out-bare-metal | success
Operator directive: "the pip/transformers/huggingface_hub dependency hell proved very
painful and I think we should rip it out and focus on the containerised route."
Deleted `roles/vllm/`, `roles/vllm_docker_stack/`, `contrib/spark-vllm-docker/`,
`docs/contrib-spark-vllm-docker.md`, `docs/vllm-multi-node.md`,
`docs/vllm-timebox-and-pivot.md`, `docs/spark-parity-pre-stack.md`. Rewrote
`roles/spark_provision/` to a container-only phase list, `roles/hf_spark/` to prefetch
via the NGC image (no host venv), and aggressively pruned
`inventory/group_vars/sparks.yml`. Updated `README.md`,
`docs/provision_sparks.md`, and `roles/vllm_stacked_container/README.md`. Marked
`entities/{ray-head,ray-worker,vllm-stacked}-service.md`,
`concepts/bare-metal-venv-stack.md`, and
`concepts/transformers-huggingface-hub-mismatch.md` as `superseded`. Full decision
record: [runs/2026-04-18-rip-out-bare-metal.md](./runs/2026-04-18-rip-out-bare-metal.md).

## [2026-04-18] run | state-of-cluster | partial
Observed: Ray head up on `nvidia1` and Ray worker up on `nvidia2` for 6 days; both
Sparks reachable over SSH; both have `nvcr.io/nvidia/vllm:26.01-py3` pulled;
`/home/nvidia/.cache/huggingface/hub` already has `google/gemma-4-31B-it` and
`TinyLlama-1.1B-Chat-v1.0`. **Failure**: `vllm-stacked.service` is in a restart loop
(`restart counter is at 967`) with
`ImportError: cannot import name 'is_offline_mode' from 'huggingface_hub'` —
`transformers 5.6.0.dev0` (git main, pulled by `vllm_transformers_from_git: true`)
dropped the shim that `huggingface_hub 0.36.2` no longer exports. Full trace and
versions in [runs/2026-04-18-state-of-cluster.md](./runs/2026-04-18-state-of-cluster.md);
failure mode cross-referenced from
[concepts/transformers-huggingface-hub-mismatch.md](./concepts/transformers-huggingface-hub-mismatch.md).
Decision: pivot to the NGC stacked container stack
([concepts/ngc-stacked-container-stack.md](./concepts/ngc-stacked-container-stack.md))
using the already-pulled `26.01-py3` image, and stop the bare-metal units.

## [2026-04-19] ops | macos-mdns-local-trap-resolved
Symptom: `curl http://registry.local:5001/v2/` from the Mac timed out at
3 s even though the SSH tunnel forwards 5001 and `dscacheutil` said
`registry.local → 127.0.0.1`. Diagnosis: macOS's `scutil --dns` shows
`resolver #2 domain: local options: mdns` — the Bonjour resolver claims
`.local` before `/etc/hosts`, so `getaddrinfo`-based apps stall on mDNS
multicast. Proved with `curl --resolve registry.local:5001:127.0.0.1 …`
→ instant `200 OK`. Fix: renamed `/etc/hosts` entries from `.local` to
`.lan` — short, universal, never mDNS-advertised. Now:
`registry.lan:5001` → `200 OK` via tunnel. `*.kind.lan` is staged for when
a kind ingress controller is deployed (needs :80/:443 added to the tunnel
at that point). Rename cascaded into
[`msmctl_docs/KIND_HOSTS_FILE.md`](../../msmctl_docs/KIND_HOSTS_FILE.md)
(public-facing docs now default to `.lan`) and documented as
[concepts/macos-mdns-local-tld-trap.md](./concepts/macos-mdns-local-tld-trap.md)
so the next agent reaches for `.lan` straight away.

## [2026-04-19] ops | ssh-alias-convention-shipped
Unified Mac-side SSH aliases for every microscaler host: `~/.ssh/config.d/`
gains `ms02`, `ms02-dev-tunnel` (IPs fixed to `192.168.1.189` — no more
dependency on `/etc/hosts`) and new `sparks` file with `nvidia1`, `nvidia2`,
`nvidia1-runtime`, `nvidia2-runtime` (IPs `192.168.1.104` + `192.168.1.229`,
default user `casibbald`, `-runtime` variants for user `nvidia`).
Convention documented as
[concepts/ssh-alias-convention.md](./concepts/ssh-alias-convention.md) —
future hosts follow the same `<host>` / `<host>-runtime` / `<host>-root`
pattern with IP-backed `HostName` and shared ControlMaster sockets per
`(user, host, port)` tuple (except the tunnel which owns a dedicated socket).
Mac's `/etc/hosts` trimmed to `localhost` + Docker Desktop + `kind.local` —
operator moves networks freely without resolver babysitting. Verified: all 7
aliases authenticate to the correct `user@hostname` (`casibbald@gx10-e1ce`,
`casibbald@gx10-47b5`, `casibbald@casibbald-MS-02-Ultra`) with no `/etc/hosts`
entries in play. Entity pages
[ms02](./entities/ms02.md), [nvidia1](./entities/nvidia1.md),
[nvidia2](./entities/nvidia2.md) updated with alias rows.

## [2026-04-19] ops | cursor-remote-ssh-wired-to-ms02
Mac had `ms02` in `/etc/hosts` only — Cursor Remote-SSH scans `~/.ssh/config`
Host entries, so the host picker didn't list it. Added dedicated aliases in
`~/.ssh/config.d/ms02`: `Host ms02` (user `casibbald`, `ForwardAgent yes`,
ControlMaster) and `Host ms02-root` (user `root`) — both on ControlMaster
sockets *distinct* from the tunnel's `tunnel-casibbald@ms02:22`, so
`dev-tunnel-down` can't kill an active editor session. Also shipped a new
`roles/dev_workstation` Ansible role (inotify watchers 65536→524288, user
instances 128→512, written to `/etc/sysctl.d/99-dev-workstation.conf`) to
stop Cursor/VS Code Remote-SSH hitting `ENOSPC` on large monorepos; wired
into `playbooks/dev_hosts.yml`. Verified: `ssh ms02 whoami → casibbald`,
three distinct ControlMaster sockets, Tilt tunnel still returning
`HTTP/1.1 200 OK` via `http://localhost:10348/`. Operator notes in
[`docs/dev_hosts.md`](../docs/dev_hosts.md) § Cursor / VS Code Remote-SSH.

## [2026-04-19] run | starlink-tunnel-workaround | shipped
Mac (Wi-Fi) → ms02 (wired LAN-out) reachability was broken for every TCP port
except 22. Step-by-step teardown (ufw rule counters incrementing but Mac still
seeing RST; iptables flushed + `ufw disable` → same result; `tcpdump -i eno4
host 192.168.1.130` capturing **zero** packets on a non-working destination
while `Connection refused` returned to the Mac) located the drop **inside the
Starlink Gen3 router** — not on ms02. Ordered a USB 2.5GbE adapter for a
physical fix and in the meantime shipped an SSH ControlMaster + 22-port
`LocalForward` + SOCKS5 workaround: `~/.ssh/config.d/ms02-dev-tunnel` + new
top-level `justfile` (`dev-tunnel-{up,down,status,check,restart,logs,config}`).
Verified: Tilt UI reachable at `http://localhost:10348/`, MinIO console, Docker
registry, and `kubectl` against `ms02`'s kind cluster all work over the tunnel
(kind's kubeconfig URL `https://127.0.0.1:38839` aligns verbatim with our
LocalForward). Full writeup:
[runs/2026-04-19-starlink-tunnel-workaround.md](./runs/2026-04-19-starlink-tunnel-workaround.md);
pattern recorded in
[concepts/starlink-wifi-lan-port-filter.md](./concepts/starlink-wifi-lan-port-filter.md)
and [entities/starlink-router.md](./entities/starlink-router.md); operator
guide in [`docs/dev_hosts.md`](../docs/dev_hosts.md).

## [2026-04-19] run | qwen3_6-35b-a3b-promoted | success
Swapped the stacked TP=2 default model from `TinyLlama/TinyLlama-1.1B-Chat-v1.0`
to `Qwen/Qwen3.6-35B-A3B` (35B total / 3B active hybrid DeltaNet+attn MoE).
Because the Ray cluster + containers + 26.03-py3 image were already up,
only the in-container `vllm serve` process had to be replaced: SIGTERM'd
the TinyLlama serve (actors → DEAD cleanly per `ray list actors`),
`docker exec -d` a new serve with `--max-model-len 32768`. Boot took 178 s
end-to-end (26 safetensors shards + flashinfer autotune dominate); vLLM
reported `Available KV cache memory: 70.66 GiB` and
`Resolved architecture: Qwen3_5MoeForConditionalGeneration` — confirms the
26.03-py3 arch-coverage story from the prior run. Smoke: 200 tokens in
43.7 s ≈ 4.6 tok/s on the first post-boot request (most of it autotune
cold-start tax; also, Qwen3.6 is a reasoning model by default and spent
the 200-token budget on `<think>` — note for future: add
`--reasoning-parser qwen3` when a consumer wants to hide it). Inventory
persisted (`vllm_default_model` + `vllm_api_server_extra_args` in
`inventory/group_vars/sparks.yml`), and `vllm-stack-autoupgrade.service`
captures live argv on bounce, so Qwen + max-model-len will replay
correctly on the next image promotion. Side-finding: Ansible's
`include_role` in `roles/spark_provision/tasks/main.yml` doesn't
propagate outer tags to inner tasks — filed as a follow-up to add
`apply: { tags: [...] }`. Writeup:
[runs/2026-04-19-qwen3_6-35b-a3b-promoted.md](./runs/2026-04-19-qwen3_6-35b-a3b-promoted.md).

## [2026-04-19] ops | lan-tld-pivot | shipped
Renamed Mac-side `/etc/hosts` entries from `*.kind.local` / `registry.local`
to `*.kind.lan` / `registry.lan`. Root cause was macOS's mDNS resolver
claiming all `.local` lookups ahead of `/etc/hosts` (`scutil --dns` shows
`resolver #2 domain: local options: mdns`), which made `getaddrinfo`
calls from curl / docker / browsers stall ~3 s and then return `NXDOMAIN`
even with a correct hosts entry — while `dscacheutil -q host -a name …`
(different API) answered correctly, which is the part that eats an
afternoon the first time. Chose `.lan` over `.test` / `.internal` /
`.home.arpa` on ergonomics: short, universal, never mDNS-advertised.
Verified `dscacheutil -q host -a name registry.lan → 127.0.0.1` and
`curl -sI http://registry.lan:5001/v2/ → HTTP/1.1 200 OK` over the
existing SSH tunnel — no tunnel or server-side changes needed. Wildcard
caveat captured in both the concept page and today's run: `/etc/hosts`
does **not** expand `*.kind.lan`, so per-service entries (or a local
`dnsmasq` + `/etc/resolver/kind.lan`) will be needed when the kind
ingress controller lands. Full rationale:
[concepts/macos-mdns-local-tld-trap.md](./concepts/macos-mdns-local-tld-trap.md);
today's run:
[runs/2026-04-19-lan-tld-pivot.md](./runs/2026-04-19-lan-tld-pivot.md);
operator doc: [`docs/dev_hosts.md`](../docs/dev_hosts.md) § Local names
for kind services; public guide:
[`msmctl_docs/KIND_HOSTS_FILE.md`](../../msmctl_docs/KIND_HOSTS_FILE.md).

## [2026-04-19] run | qwen3-throughput-and-256k | shipped
Follow-up to the Qwen3.6-35B-A3B promotion: raised `--max-model-len`
from 32768 to the model's native **262144**, and tuned three throughput
knobs that were still at defaults. Concrete flags landed in
`vllm_api_server_extra_args`: `--gpu-memory-utilization 0.92` (0.95
crashed `init_device` because GB10 only has ~111.5 / 119.61 GiB free on
boot — ~8 GiB is permanently pinned by host / docker / ray), and
`--max-num-batched-tokens 16384` (8× the 2048 default, which was the
actual bottleneck in concurrent prefill), and `--max-num-seqs 128`,
plus `--reasoning-parser qwen3` so OpenAI consumers get
`reasoning_content` separated from `content`. KV cache doubled on the
bump: per-rank 70.66 → 72.24 GiB, cluster-total **144.48 GiB holding
1,890,240 tokens = 28.42× concurrency at full 256k context**. Measured
throughput with proper batch-size-sweep warmup (not just batch=1 —
which is what made my first post-change bench look like a regression):
single-stream 29 → **35 tok/s (+22%)**, saturated aggregate 86 (batch=4)
→ **164 tok/s (batch=16, +90%)**. Logger also reported steady-state
decode of 63 tok/s when fed a hot single request. Long-context proof:
fed a deliberately oversized 46,023-token prompt (would have been
rejected pre-change), completed end-to-end in 25 s at ~1,833 tok/s
effective rate. At 25k prompt + 4096-token response the model returned
a clean 3-bullet summary with `finish_reason: stop` and no OOM /
eviction. Autoupgrade daemon inherits the new argv automatically
(`docker inspect` captures live command on bounce), no daemon change
needed. Follow-ups still on the table for future sessions: speculative
decoding, FP8 weights, and an EP-vs-TP bake-off for the 256-expert MoE
shape. Writeup:
[runs/2026-04-19-qwen3-throughput-and-256k.md](./runs/2026-04-19-qwen3-throughput-and-256k.md).
