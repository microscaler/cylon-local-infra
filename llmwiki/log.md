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

## [2026-04-19] run | qwen3-thinking-validation | shipped
Validated reasoning output on the 262k-context stack across four prompt
categories (hard math, code bug fix, 12-ball logic puzzle, creative
haiku) in both thinking-ON and thinking-OFF modes with Qwen's
recommended sampling profiles (ON: temp=0.6, top_p=0.95, top_k=20;
OFF: temp=0.7, top_p=0.8, top_k=20). **Field-naming gotcha found**:
the `qwen3` reasoning parser on vLLM 0.17.1 / 26.03-py3 populates
`message.reasoning` (OpenAI o1 style), not `message.reasoning_content`
(DeepSeek-R1 style) — earlier smoke tests were reading the wrong field
and concluded the parser was broken; it was not. Reasoning quality is
genuinely high: traces include numbered analysis, self-correction,
verification steps, alternative solution methods, and edge-case
identification (e.g. "density irrelevant because fully submerged",
"no-water-underneath case is outside standard displacement
assumption"). Decode rate is a flat 30-35 tok/s per stream regardless
of mode or prompt. Thinking adds a 7-96× wall-time multiplier: bug-fix
+7.4× (clearer answer), math +9.4× (ran out of budget at 8192), haiku
+96× for the same 21-token output (overthink pathology, 88.7% of
tokens were reasoning on the creative task). Key operational finding:
**the sweet spot is not a single config — it's per-request
`enable_thinking` with task-matched `max_tokens`**. Cluster settings
stay at today's throughput-tuned values. Task-budget recipe:
hard-math→ON+16384, logic/planning→ON+8192, code-debug→ON+4096,
code-gen→OFF+4096, summary→OFF+2048, creative→OFF+1024. Non-thinking
mode also needs task-aware budgets: the 12-ball puzzle hit the 2048
length cap mid-procedure in OFF mode. No inventory changes, pure
validation. Writeup, raw reasoning samples, and per-task budget table:
[runs/2026-04-19-qwen3-thinking-validation.md](./runs/2026-04-19-qwen3-thinking-validation.md).

## [2026-04-19] run | roce-cutover | shipped
Flipped the NCCL data plane off TCP sockets and onto RoCE v2 + GPUDirect
RDMA over the ConnectX-7 QSFP link. The 2026-04-18 workaround conflated
two orthogonal knobs: `NCCL_NET_PLUGIN=none` (disables *external* plugins
— the one that crashed on 26.01) and `NCCL_IB_DISABLE=1` (disables
NCCL's *internal* verbs path — which was never the crasher). Kept the
former off as belt-and-braces, flipped the latter on. Standalone
two-rank `all_reduce_perf` proved the internal path: **sockets 2.02 GB/s
→ RoCE+GDR 13.93 GB/s (6.9×)** at 1 GiB messages, **12× on 1 MiB**
(latency-sensitive) — log line `NCCL INFO NET/IB : Using
[0]rocep1s0f0:1/RoCE [RO]`. Container role updated to inject `--device
/dev/infiniband --cap-add IPC_LOCK --ulimit memlock=-1:-1` (gated on a
new `vllm_stacked_container_rdma_enabled` default=true). Shipped
one-shot `playbooks/cutover_roce.yml` that invokes the role directly to
sidestep the known `spark_provision` tag-propagation issue. Cutover
recap: nvidia1 ok=18 changed=4, nvidia2 ok=12 changed=3, 30 s. Workload
post-cutover (Qwen3.6-35B-A3B, TP=2, 262144 ctx): single-stream decode
35→47.6 tok/s (+36%); batch=16 aggregate 164→265.9 tok/s (+62%); new
ceiling batch=64 at **638 tok/s**; 3k prefill 0.85s→0.44s (1.9×); 40k
prefill ~22s→5.78s (**3.8×**). Verified on RoCE HCA port counters:
3000-token prefill moved 1008 MiB xmit + 1008 MiB rcv through
`rocep1s0f0`, 0 MiB through the socket interface — 100% RDMA data
plane. Rollback is two env vars + one flag (documented). Concept page
[concepts/nccl-on-spark.md](./concepts/nccl-on-spark.md) rewritten as
the RoCE+GDR canonical. Deferred follow-ups: MTU 1024→4096 (native HCA
max), second-ASIC QSFP bond (roceP2p1s0f0 is ACTIVE but uncabled),
re-qualify external OFI plugin on 26.03-py3, fix include_role tag
propagation in `spark_provision`. Writeup:
[runs/2026-04-19-roce-cutover.md](./runs/2026-04-19-roce-cutover.md).

## [2026-04-21] config | hf-prefetch | Qwen3.6 FP8 queued
Added `Qwen/Qwen3.6-35B-A3B-FP8` to `inventory/group_vars/sparks.yml`
`hf_prefetch_models` (after TinyLlama, before 3.5 FP8). Fixed `include_role
hf_prefetch_service` to use `apply.tags` so `--tags hf_prefetch` runs the
full role. Ran `ansible-playbook playbooks/provision_sparks.yml -l sparks
--tags hf_prefetch` to render `/etc/hf-prefetch/config.yaml` on the leader;
daemon picks up on next poll. Serving default remains 3.5 FP8 until cutover.

## [2026-05-01] run | nvidia2-abrupt-power-off-vllm-long-context | shipped
Second nvidia2 abrupt-power-off in 4 days — this one a **double-crash**:
host crashed at end of 34 h uptime (`00:13:45 EEST`), auto-recovered,
then **crashed again 3 minutes into the next boot** (`00:22:03`), then
recovered for a third time (`~00:34`). Same kernel-undetectable signature
(rasdaemon clean across MCE/AER/memory; two journal files moved to
`.journal~` recovery state; one boot has no journal at all). Cross-
referenced against [NVIDIA Developer Forums thread
#359785](https://forums.developer.nvidia.com/t/title-asus-ascent-gx10-gb10-hard-power-off-unclean-reboot-under-vllm-gpt-oss-120b-long-context/359785)
("ASUS Ascent GX10 GB10 hard power-off / unclean reboot under vLLM
gpt-oss-120b long context", opened 2026-02-05, 31 posts) — **confirmed
known platform issue affecting many GX10 owners across multiple FW
revisions**, not unique to our cluster. **Three hypotheses probed**:
(1) **PCIe downgrade to 2.5 GT/s** (forum post #5) — RULED OUT,
`lspci -vv` shows `Speed 32GT/s, Width x4` on GPU + all 4 ConnectX-7
NICs on both Sparks. (2) **Recent firmware update introduced the crash**
(operator instinct) — RULED OUT, we crashed on FW `.0100` AND `.0103`
with the same signature; the FW update is not the cause but also not the
fix. (3) **Long context triggers it** (forum post #2 — degradation past
30k tokens; we run `--max-model-len 262144` = 262k) — STRONGLY
CORRELATED, only operationally-testable lever. Cluster recovery: Ray
auto-restarted via `--restart unless-stopped`; `vllm serve` died with
the head container (it's a `docker exec -d` payload, same downstream
as 2026-04-29 11:17); `just spark-vllm-api-restart` brings the API
back. **Did not change inventory or firmware** — preserved today's
stable config (`262144` + `0.80` + `26.03-py3` pin) so the production
reference is unchanged. Open follow-ups: decide whether to lower
`--max-model-len` to `65536` or `32768` as a deliberate config change in
a maintenance window (not a panic edit at 00:45 EEST); track forum
thread for new NVIDIA-side updates past post #5; consider posting our
own observations to the thread (clean rasdaemon, two-crash sequence,
FW-revision invariance, ruled-out PCIe downgrade) for community
visibility. Doesn't change the [8-spark-fabric concept page](./concepts/8-spark-fabric-and-orchestrator.md)
status (still pinned on hardware) — confirms the platform fragility
that already motivates several of its design choices. Full timeline +
hypothesis table + recovery sequence:
[runs/2026-05-01-nvidia2-abrupt-power-off-vllm-long-context.md](./runs/2026-05-01-nvidia2-abrupt-power-off-vllm-long-context.md).

## [2026-05-02] run | nvidia1-abrupt-power-off-on-pinned-1008-kernel | shipped (kernel hypothesis FALSIFIED)
**12 minutes after locking the kernel pin** at 09:39 EEST, `nvidia1`
had an abrupt power-off at 09:51:05 EEST — ~22h 15min after boot, on
the **pinned `6.17.0-1008-nvidia` kernel**. The forensic record from
the GX10-hunt dashboard is decisive: the host crashed **at idle**.
**At-crash metrics** (30 min window preceding 09:51:05 EEST,
captured by spark_observability):
- `up{host="nvidia1"}` = 1 for all 125 samples (15 s × 31 m); cluster
  healthy until the moment of the cut
- GPU power: peak **66.95 W** (~28% of GB10's ~240 W TGP — idle)
- GPU temp: peak **78°C** (below 85°C concern threshold)
- `vllm_num_requests_running` peak = **1**, waiting = 0, KV cache
  = **0.87 %**
- `rasdaemon_events_total` ALL zero across 8 categories (memory CE/UE,
  PCIe AER correctable/uncorrectable/fatal, MCE, extlog, devlink)
- Loki grep `(?i)(mlx5|MCE|panic|aer|oom|hardlockup|softlockup|nvidia.*xid|vbios|gpu fallen|uncorrected|reset)` over the entire 22h boot: **zero matches**
- Last journal entry: dcgm-exporter container restart at 09:51:05
  (small unit-script race on port 9400, **not causal**); journal
  cuts mid-line on the next dockerd shim cleanup message; no
  shutdown sequence

**Kernel jump `1008 → 1014` as sole cause is FALSIFIED**. The crash
reproduced on the older HWE kernel that was supposed to be the
safer one. The signature exactly matches NVIDIA forum thread #359785
(spontaneous GB10 SoC abrupt power-off with no software-visible
precursor). **GX10 platform-level hardware bug is now the leading
hypothesis** — primary remediation path is **vendor-side**, not in
our stack. nvidia2 did NOT crash (uptime 22h 31min and still up on
the same pinned kernel) — same MTBF improvement signal, but with
N=2 cluster and small sample, NOT statistically significant.

**Did the pin help?** Maybe marginally. Pre-pin: ~5 boots in 1h 50min
+ ~10h between crash chains. Post-pin: 22h 15min before crash on
nvidia1, nvidia2 still up. ~22× MTBF improvement, but could be (1)
real effect, (2) coincidence with end of 2026-05-01 firmware-update
turbulence, or (3) small-sample variance. Honest position: **keep
the pin (no downside, marginal upside) but stop treating "no
crashes" as evidence the issue is fixed**.

**Operator's `dpkg -l 'linux-image-*' | grep '^ii'` confusion**:
`apt-mark hold` doesn't change the "installed" actual state, it
changes the "desired" state from `install` to `hold`. So held
packages show as `hi` (hold/installed) not `ii` (install/installed).
The operator's `^ii` filter hid the held `linux-image-6.17.0-1008-nvidia`
package, making it look like the pin had been undone. Documented
as a "reasoning trace" section in the run page so this cognitive
trap doesn't recur.

**Next-step changes**:
- ~~3-day / 7-day / 14-day "kernel-fix-confirmed" check-ins~~ —
  CANCELLED. We won't get there because the hypothesis is
  falsified.
- ARM kdump on both Sparks (long overdue; even a half-useful kernel
  dump on the next crash would be more than we have).
- File a NVIDIA Developer Forum reply on thread #359785 with the
  forensic record (GPU 67 W, 78°C, vLLM idle, all RAS clean,
  reproduces across kernel revisions and ASUS BIOS revisions).
  This is unique, valuable evidence the community doesn't have.
- Don't unpin the kernel (no benefit, harmless to keep).
- Don't lower `--max-model-len` or `gpu_memory_utilization` yet —
  this crash had nothing to do with workload. Keep these in reserve
  for IF we see a future crash that DOES correlate with load (the
  "vLLM concurrent in-flight" panel preceding the crash would
  show it).
- Bring nvidia1's vLLM API back online via `just spark-vllm-api-restart`.

**The dashboard validated itself**: the absence of any signal in
the GPU/SoC/RAS/vLLM panels IS the critical signal — confirms the
crash is platform-level and the OS is blind to it. This is the
forensic capability we built spark_observability for.

**Two recovery surprises** (both fixed, both unrelated to the crash
itself but would have bitten any future Spark recovery):
1. **dockerd refused to start**: `live-restore: true` (added 2026-05-01
   for nvidia runtime + journald work) is incompatible with active
   Docker Swarm mode. Both Sparks had **stale swarm state from
   2026-02-21** (node_id `ud52cjv9wztc3ih0ojunhh0cp`, manager
   `192.168.1.104:2377`) — leftover from earlier provisioning, never
   used by us (kind on ms02 = k8s; Sparks = plain Docker via NGC
   containers). nvidia2's running dockerd had picked up `live-restore`
   via SIGHUP without conflict; **next clean restart would have
   failed too**. Fix: `docker swarm leave --force` on nvidia2 (clean,
   no container disruption); `mv /var/lib/docker/swarm{,.preswarm-leave-2026-05-02}`
   on nvidia1 (preserves state for forensics, removes from dockerd
   path), then `systemctl reset-failed docker && systemctl start
   docker`. Both Sparks now have NO swarm state; `live-restore` works.
   Open follow-up: pre-flight check in `roles/docker` that fails-loud
   if swarm state present + `live-restore: true` requested.
2. **`roles/spark_kernel` discovery awk had the same `install ok
   installed` filter bug** that bit `just spark-kernel-status` earlier
   today. After `apt-mark hold` had moved 1008 to dpkg state `hi`,
   the role's discovery couldn't see it as "installed" and the
   assertion `spark_kernel_pin in spark_kernel_installed.stdout_lines`
   failed: "spark_kernel_pin='6.17.0-1008-nvidia' is NOT an installed
   kernel". Fix: changed awk pattern from `$1=="install ok installed"`
   to `$1 ~ /ok installed$/` so both `install ok installed` (ii) AND
   `hold ok installed` (hi) match. After the fix, `just
   spark-kernel-check` returns `ok=18, changed=0` on both Sparks —
   confirming the pin is fully idempotent and the role can no longer
   be tricked by its own hold.

**Recovery sequence** documented end-to-end in the run page (one-time
swarm cleanup + standard dockerd-recover dance + `spark-vllm-api-restart`
+ probes). ~3 minutes from "host just came back" to "cluster fully
serving" on the next recovery, because future recoveries skip the
swarm step.

Full timeline + at-crash data + hypothesis status + revised next-steps + recovery surprises:
[runs/2026-05-02-nvidia1-abrupt-power-off-on-pinned-1008-kernel.md](./runs/2026-05-02-nvidia1-abrupt-power-off-on-pinned-1008-kernel.md).
The earlier same-day [bisection-evidence run page](./runs/2026-05-02-kernel-pin-locked-bisection-evidence.md)
is now superseded (front matter updated with a clear callout).

## [2026-05-02] run | kernel-pin-locked-bisection-evidence | shipped (22h uptime, pin durably applied)
After **22h 4min continuous uptime on both Sparks** with **zero abrupt
power-offs** under operator's normal Hermes workload (vs. multiple
crashes/day on `6.17.0-1014-nvidia`), operator declared the bisection
signal strong enough to lock the pin. Important context: the 22h
stability was **coincidental** — `inventory/group_vars/sparks.yml`
had the pin set since 2026-05-01 but `just spark-kernel-apply` was
never actually run (we got distracted by the observability rollout).
`just spark-kernel-status` showed both hosts running 1008 (lucky:
they'd been manually `grub-reboot`-ed during the 2026-05-01
recovery), but `GRUB_DEFAULT=0`, no apt holds, OTA service
unmasked. Any of: a routine `apt upgrade`, a NVIDIA dpkg trigger
re-arming `nvidia-spark-run-apt-upgrade-once.service`, or the next
unscheduled reboot, would have rolled the cluster forward to 1014
again and undone the bisection sample.

**Locked down 2026-05-02 09:39 EEST**: ran `just spark-kernel-apply`
end-to-end; per-host PLAY RECAP `ok=20 changed=4` (GRUB_DEFAULT
lineinfile + apt-mark hold + OTA mask + update-grub handler).
**Verified state on both Sparks**:
- `GRUB_DEFAULT="gnulinux-advanced-<UUID>>gnulinux-6.17.0-1008-nvidia-advanced-<UUID>"`
- 9 packages held cluster-wide (kernel + HWE meta + nvidia-580-open + nvidia-fs)
- `nvidia-spark-run-apt-upgrade-once.service` `enabled=masked, active=inactive`

Hypothesis status updated: **HWE kernel jump `1008 → 1014` is now the
leading suspect** for the GX10 abrupt-power-off pattern (strongly
correlated, not yet confirmed). Honest about alternative explanations
in the new run page (workload may have been lighter; platform
"warm-up"; apt upgrades silently fixing something) — these are
checkable against the 22h dashboard timeseries we now have.

**Monitoring criteria** filed in the run page: 3-day check-in (moderate
confidence), 7-day (strong, update postmortem hypotheses table),
14-day (high — promote pin from "diagnostic" to "production stable",
consider posting bisection evidence to NVIDIA forum #359785).
**Falsification criterion**: if a crash returns on 1008, kernel was not
the only cause and we move to load-axis bisection (lower
`--max-model-len`, lower `gpu_memory_utilization`, Hermes config
tightening, auxiliary-model-isolation). **No other variables changed**
during the monitoring window — kernel, `--max-model-len 262144`,
`gpu_memory_utilization 0.80`, firmware, ASUS BIOS, NCCL/RoCE config,
Hermes settings all held constant. Only thing varying day-to-day is
workload intensity.

**Small bug fix in `just spark-kernel-status`**: the awk filter
`/install ok installed/` only matched packages in the `install`
desired-state, hiding held packages (which dpkg shows as `hold ok
installed`). Changed to `/ok installed/` so held kernels stay visible
in the "installed:" section. The pin lockdown made the earlier symptom
(running kernel `6.17.0-1008-nvidia` not appearing in the installed
list) immediately visible.

Full evidence + monitoring plan + open follow-ups:
[runs/2026-05-02-kernel-pin-locked-bisection-evidence.md](./runs/2026-05-02-kernel-pin-locked-bisection-evidence.md).

## [2026-05-01] cleanup | resolve two operator gotchas (nvidia runtime + NodePort gap)
Promoted both 2026-05-01 gotchas from "documented learnings" to "fixed
in code so they can't happen again".

**Gotcha 1 — `--runtime nvidia` doesn't exist on DGX OS** (RESOLVED).
Was a workaround in the dcgm-exporter unit (`--gpus all` alone, no
`--runtime nvidia`). Now registered `/usr/bin/nvidia-container-runtime`
as a Docker runtime via `inventory/group_vars/sparks.yml`
`docker_daemon_config.runtimes.nvidia.path`. Applied with
`systemctl reload docker` (SIGHUP, no container disruption — vllm-ngc-ray-*
containers stayed Up across the change). `docker info` on both Sparks
now shows `Runtimes: io.containerd.runc.v2 nvidia runc`. Default
runtime stays `runc`; `nvidia` is registered as an option. Bumped
`roles/spark_observability/templates/dcgm-exporter.service.j2` to use the
canonical `--runtime nvidia --gpus all` pattern matching NVIDIA's
official NGC documentation; re-applied via `--tags spark_obs`,
verified `127.0.0.1:9400/metrics` HTTP 200 on both Sparks after
restart.

**Gotcha 2 — kind extraPortMappings → non-existent NodePorts** (RESOLVED
+ AUTOMATED). Was: `grafana`, `loki`, `otel-collector`, `prometheus`,
`jaeger` were all `type: ClusterIP`. Result: `ms02:<port>` from the LAN
got `connection refused` (docker-proxy → kind:NodePort → kube-proxy
refused). Tilt's `port_forward` aliases were 127.0.0.1-only and were
silently working "from inside Tilt" — invisible to the LAN. Today, all
five Services have `type: NodePort` + explicit `nodePort:` matching
the kind portmap (last gap closed by adding NodePort 31166 to
`shared-kind-cluster/k8s/observability/jaeger.yaml`); LAN-side
`ms02:16686` Jaeger UI is now HTTP 200. **Systematic detection** added
as `just ms02-cluster-portmap-check` — pulls kind-config.yaml +
`kubectl get svc -A -o json` from ms02, diffs locally, prints a table
showing which extraPortMappings have backing NodePorts and which
don't. Catches this regression class before it bites. All 6
observability portmaps green; followed up by **fixing 3 deployed
data-namespace services** that were ClusterIP despite being listed in
the kind extraPortMappings: `data/postgres` got `nodePort: 30432`
(host:5433), `data/redis` got `nodePort: 30379` (host:6379),
`data/pact-broker` got `nodePort: 30929` (host:9292), in
`shared-kind-cluster/k8s/platform-data/data/{postgres/postgres-primary-service,
cache/redis-service,postgres/pact-broker}.yaml`. Final probe state:
**11 OK, 8 MISSING**, where the 8 split cleanly between (a) **5
app-team-reserved ports** for services not yet deployed in the cluster
(PriceWhisperer API:8000, BRRTRouter:8080, Pyroscope:4040,
PriceWhisperer mocks:7497/9999) — MISSING is the correct signal until
those teams deploy, and (b) **3 alt-port duplicates**
(`3002`/`9091`/`8889` second mappings for grafana / prometheus /
otel-collector exposition) which would need a `kind delete && create`
to remove from `kind-config.yaml` — too disruptive for now since they
cause no harm. Categorised in the
[concept page](./concepts/sparks-observability-pipeline.md#critical-service-type-gotcha-resolved-automated-detection).

Wiki cross-refs: both gotchas now appear as "RESOLVED" sections in
[`concepts/sparks-observability-pipeline.md`](./concepts/sparks-observability-pipeline.md)
with the reproducible fix steps.

## [2026-05-01] dashboards | sparks-cluster + vllm-performance + gx10-power-off-hunt | shipped
Three Grafana dashboards live in `shared-kind-cluster/k8s/observability/embedded/`,
mounted into Grafana's `Sparks` folder via three new ConfigMap/volumeMount/
provider entries (Tilt `local_resource` + `apply-sparks-dashboards` follows
the same `kubectl create configmap --from-file` pattern as
`apply-postgres-dashboards` to avoid kustomize re-render loops on JSON
edits). All filter on `cluster="cylon-sparks"` and provide a `host`
template variable.

**Dashboards**:
1. **DGX Spark Cluster — Overview** (`uid: spark-cluster-overview`) —
   fleet status, GPU power/temp/util/clock, host CPU+UMA mem (Grace
   shared with GPU)+load+hwmon, network+filesystem, tracked-systemd-units
   status table.
2. **vLLM — Performance** (`uid: vllm-performance`) — concurrency
   (running, waiting, KV occupancy, success rate), token throughput
   (prompt + generation tok/s), TTFT/ITL/e2e p50/p95/p99 from histograms,
   queue + prefill p95, prefix cache hit rate, preemptions/sec.
3. **DGX Spark — GX10 abrupt-power-off hunt** (`uid: gx10-power-off-hunt`) —
   the **forensic** dashboard. Status row (up, uptime, kernel, RAS
   counters, XID errors, PCIe replay) → GPU power with 150/200/240W
   thresholds (forum #359785's "sustained high power" trigger window) +
   temps → rasdaemon stacked counters → vLLM concurrent in-flight (the
   multi-Hermes-session amplifier) + KV cache → **two Loki log panels**
   (kernel/dockerd events matching `(?i)(mlx5|MCE|panic|aer|oom|hardlockup|
   softlockup|nvidia.*xid|vbios|gpu fallen|uncorrected|hard.*power.*off|
   abrupt|reset)`, plus all `vllm-ngc-ray-*` container logs) → NVLink +
   QSFP/RoCE throughput. Default 3h lookback. Page links to the
   2026-05-01 postmortem and NVIDIA forum #359785.

**Two cluster-side gotchas fixed at apply time** (both documented in
[`concepts/sparks-observability-pipeline.md`](./concepts/sparks-observability-pipeline.md)):

1. **OTel resource attributes weren't reaching metric labels.** The
   central otel-collector's `prometheus` exporter, by default, drops
   OTLP resource attributes (`cluster`, `host`, `os_kernel`,
   `os_distribution` set by Spark otel-agents) into a separate
   `target_info` series. Result: any `host=~"$host"` panel filter
   returned zero series. **Fixed** by adding
   `resource_to_telemetry_conversion: enabled: true` to
   `embedded/otel-collector-config.yml` `prometheus` exporter; restart
   makes every metric carry the resource labels directly.
2. **Service `type: ClusterIP` made the cluster invisible to the LAN.**
   The kind extraPortMappings (`containerPort: 31300/31310/31417/31418/
   31090 → hostPort: 3000/3100/4317/4318/9090`) forwarded to NodePorts
   that didn't exist — `grafana`, `loki`, `otel-collector`, `prometheus`
   were all ClusterIP. Result: `ms02:<port>` from any LAN client got
   `connection refused` (docker-proxy → kind:NodePort → kube-proxy
   refused). **Fixed** by adding `type: NodePort` + explicit `nodePort:`
   matching the kind portmap to all four Services. Tilt's existing
   `port_forward` aliases (`9230:3000`, `4319:4317`, etc.) bind to
   127.0.0.1 only and were the only LAN access path before this — they
   were silently working "from inside Tilt" and invisible to anything
   outside (including the Sparks).

**Verified end-to-end** 2026-05-01: all three dashboards visible at
`http://192.168.1.189:3000/dashboards/f/Sparks` (NodePort, admin/admin
or anonymous Viewer). Sample queries from the Mac:
`DCGM_FI_DEV_POWER_USAGE{cluster="cylon-sparks"}` → 2 series,
`vllm_num_requests_running` → 1 series (only nvidia1 head),
`rasdaemon_events_total` → 16 series (8 categories × 2 hosts),
`node_cpu_seconds_total{mode="idle"}` → 40 series (20 cores × 2 hosts),
Loki's crash-signature query processed 86,595 lines in 1h with 2
matching entries. **The forensic capability promised in the
[2026-05-01 postmortem follow-up](./runs/2026-05-01-nvidia2-abrupt-power-off-vllm-long-context.md#open-follow-ups)
is now live**: next abrupt power-off leaves a complete record across
GPU/SoC/RAS/vLLM/journald axes on a single time scale.

## [2026-05-01] role | spark_observability | shipped (push-based metrics + logs to ms02)
Sparks → ms02 observability pipeline live and verified end-to-end. New
[`roles/spark_observability/`](../roles/spark_observability/) installs
five components per Spark (all scoped to localhost / outbound-only):
**`node_exporter` 1.8.2** (host metrics + systemd unit collector +
hwmon/thermal/textfile), **`dcgm-exporter` 3.3.5-3.4.1** (NVIDIA NGC
container, host-network, `--gpus all` — `--runtime nvidia` is NOT
registered on DGX OS, gotcha), **`otel-collector-contrib` 0.96.0 in agent
mode** (scrapes node_exporter + dcgm + vLLM `/metrics` every 15s, attaches
`cluster=cylon-sparks` + `host` + `os_kernel` resource attributes, OTLP
gRPC pushes to ms02:4317), **`promtail` 2.9.4** (journald → Loki push to
ms02:3100), and a **`rasdaemon-textfile.timer`** (60s, parses
`ras-mc-ctl --summary` into Prometheus counters via node_exporter textfile
collector — gives us live `rasdaemon_events_total{category=memory_ce,
memory_ue, pcie_aer_*, mce_records, ...}` for the GX10 crash investigation).

Single log path: `group_vars/sparks.yml` `docker_daemon_config` switches
`dockerd` to `--log-driver=journald` + `tag: "{{.Name}}"`, so kernel +
systemd + dockerd + every container's stdout/stderr flow through one
Promtail scrape; `CONTAINER_NAME` becomes a Loki label.

**ms02 changes** (gap discovered at apply time): the kind extraPortMappings
in `kind-config.yaml` (`containerPort: 31310/31417/31418/31090 → hostPort:
3100/4317/4318/9090`) were forwarding to NodePorts that didn't exist —
the cluster's `loki`, `otel-collector`, `prometheus` Services were
`type: ClusterIP`. Result: `ms02:4317` reached docker-proxy → kind:31417
→ kube-proxy refused → `connection refused`. **Fixed** by adding
`type: NodePort` + explicit `nodePort:` to those Services in
`shared-kind-cluster/k8s/observability/{loki,otel-collector,prometheus}.yaml`.
Tilt picked up + applied automatically. Also extended
`inventory/host_vars/ms02.yml` `firewall_trusted_lan_tcp_ports` with
`3100`, `4317`, `4318` (the existing `7000-12000` range didn't cover them);
applied via `ansible-playbook playbooks/dev_hosts.yml --tags firewall -l ms02`.

**Wired**: new `kernel`-style phase in `roles/spark_provision/tasks/main.yml`
(toggle `spark_provision_observability: true`). Deployed on both `nvidia1`
and `nvidia2`. **Verified** end-to-end — `count by (host)({cluster="cylon-sparks"})`
in Prometheus returns rows for both Sparks; `streams=1` in Loki for
`{cluster="cylon-sparks"}`. Five new justfile recipes:
`spark-observability-{status,apply,check,pin,show-menu}`...
`spark-observability-{status,apply,check,probe}`. Two operator gotchas
fixed during apply: (1) `--runtime nvidia` doesn't exist on DGX OS; only
`--gpus all` is needed (nvidia-container-toolkit handles it); (2) Promtail
zip extract via Ansible `unarchive` `extra_opts` puts the file pattern
BEFORE the source — wrong arg order for `unzip`; dropped the filter since
the zip only contains one file. Full design + ops surface:
[`concepts/sparks-observability-pipeline.md`](./concepts/sparks-observability-pipeline.md).
**This is the highest-value tool added for the
[2026-05-01 GX10 abrupt-power-off](./runs/2026-05-01-nvidia2-abrupt-power-off-vllm-long-context.md)
bisection** — converts after-the-fact log forensics into live timeseries
with GPU power/temp/util, EDAC/PCIe AER/MCE counters, vLLM concurrent
in-flight, and searchable kernel/dockerd logs. Open: Grafana dashboards
(3 target — Spark Cluster, vLLM Performance, GX10 Power-Off Hunt),
Prometheus alert rules.

## [2026-05-01] role | spark_kernel | shipped (kernel-bisection scaffolding)
Added [`roles/spark_kernel/`](../roles/spark_kernel/) — idempotent, reversible
Ansible role that manages the GRUB default kernel, apt-mark holds on the
kernel + HWE meta packages, and the
`nvidia-spark-run-apt-upgrade-once.service` mask. Filed in response to operator
performing a manual `grub-reboot` from `6.17.0-1014-nvidia` →
`6.17.0-1008-nvidia` on `nvidia1` to bisect the GX10 abrupt-power-off
issue ([2026-05-01 postmortem](./runs/2026-05-01-nvidia2-abrupt-power-off-vllm-long-context.md)).
**Design**: 4 inventory flags (`spark_kernel_pin`, `spark_kernel_apt_hold`,
`spark_kernel_disable_auto_apt_upgrade`, `spark_kernel_show_grub_menu`),
all default to safe values. Empty `spark_kernel_pin` means "leave
GRUB_DEFAULT alone, manage holds + OTA mask only" — converges to a sane
baseline on a fresh host without making boot decisions for the operator.
**GRUB ID discovery**: deterministic construction from the root-fs UUID
(`findmnt -nrvo UUID /`) + kernel version → `gnulinux-<KVER>-advanced-<UUID>`,
verified by grep against `/boot/grub/grub.cfg` before writing. Avoids
fragile multi-line regex parsing. Recovery entries are excluded by
construction (`-advanced-` not `-recovery-`). **Reversibility**: setting
`spark_kernel_pin: ""` leaves GRUB_DEFAULT untouched; setting
`spark_kernel_apt_hold: false` releases ALL kernel-related apt holds
(`linux-image|linux-headers|linux-modules`); setting
`spark_kernel_disable_auto_apt_upgrade: false` unmasks the OTA service
(does not start it). **Wired**: `roles/spark_provision/{tasks,defaults}/main.yml`
with new `kernel` phase tag and `spark_provision_kernel` toggle.
Five new justfile recipes: `spark-kernel-status` (current kernel +
holds + OTA state on both hosts), `spark-kernel-apply` (full role
run; accepts `host=` arg), `spark-kernel-check` (`--check --diff`
dry-run), `spark-kernel-pin host=… ver=…` (ad-hoc one-off override),
`spark-kernel-show-menu on|off` (toggle GRUB visibility). **Inventory** (operator chose **cluster-wide step-back**, not A/B):
`group_vars/sparks.yml` declares `spark_kernel_pin: "6.17.0-1008-nvidia"`
+ enables `spark_kernel_apt_hold` and `spark_kernel_disable_auto_apt_upgrade`
cluster-wide. No per-host overrides — `host_vars/nvidia{1,2}.yml` carry
documentation comments only. **Rationale**: today's `uname -r` already
shows both Sparks running 1008 (operator's manual `grub-reboot` ended up
applied to both during this morning's recovery), so a cluster-wide pin
captures intent and gives a clean before/after MTBF comparison against
today's crash history (which was all on 1014). Easier to interpret than
an A/B that splits load asymmetrically. Operator applies via
`just spark-kernel-apply`. **Verified** with `--check --diff` on both
Sparks: GRUB_DEFAULT diff is the expected
`gnulinux-advanced-<UUID>>gnulinux-<KVER>-advanced-<UUID>` form,
discovery passes, all assertions green; `apt-mark hold` will hold 9
installed packages per host (HWE meta + 1008 kernel set);
`nvidia-spark-run-apt-upgrade-once.service` (currently `enabled, inactive`,
done-file present) will be masked. **Does NOT trigger
a reboot** — operator chooses via `just spark-reboot`. **Does NOT arm
kdump** — separate concern; current `crashkernel=1G-:0M` still leaves
no dump on next abrupt power-off. Full role docs:
[roles/spark_kernel/README.md](../roles/spark_kernel/README.md).

## [2026-05-01] concept | auxiliary-model-isolation | proposed (filed from postmortem)
Filed [concepts/auxiliary-model-isolation.md](./concepts/auxiliary-model-isolation.md)
articulating the architectural pattern of running Hermes's nine
auxiliary task classes (`vision`, `web_extract`, `compression`,
`session_search`, `skills_hub`, `approval`, `mcp`, `flush_memories`,
`title_generation`) on a **separate small model endpoint** rather than
funneling them onto the same `:8000` primary endpoint serving the
user's reasoning call. Derived from the [2026-05-01 nvidia2
postmortem](./runs/2026-05-01-nvidia2-abrupt-power-off-vllm-long-context.md)
finding that Hermes default `provider: auto` resolves all auxiliaries
to the main `qwen-spark` provider, generating concurrent KV-cache /
batching pressure on the main 35B-FP8 stack — one of the precursors
the [GX10 forum thread #359785](https://forums.developer.nvidia.com/t/title-asus-ascent-gx10-gb10-hard-power-off-unclean-reboot-under-vllm-gpt-oss-120b-long-context/359785)
correlates with the abrupt power-off bug. **Three deployment patterns**
laid out in the page: (A) **today, no new hardware** — Ollama on `ms02`
(Threadripper 3970X, no GPU, CPU-served Q4 model — `qwen2.5:3b-instruct`
or `phi-4-mini-instruct` recommended), reversible per-key rollout in
`~/.hermes/config.yaml`, ms02-uptime caveat documented; (B) **bridge** —
Ollama as a k8s Deployment on the [ComputeBlade Pi cluster](./concepts/8-spark-fabric-and-orchestrator.md)
once it's online, frees ms02 for dev work; (C) **final** — vLLM-CPU or
Ollama on `orchestrator1` (Phase 6 of the [8-spark plan](./concepts/8-spark-fabric-and-orchestrator.md#migration-phases)),
sub-options C.1 (vLLM-CPU on `:8001`, single-tech operator surface) and
C.2 (Ollama, simpler ops). **Hermes-key → tier mapping table** in the
page: `vision` **stays on `qwen-spark`** (needs Qwen3-VL when we cut
over); `compression` and `flush_memories` are highest-impact cutovers
(most frequent); `mcp` arg synth has a measure-before-cut caveat (rich
schemas may benefit from the big model). **Non-goals captured**: not a
quality-equivalent replacement for the auxiliaries — small models are
worse but for these specific tasks "good enough" — the win is
isolation, not parity. Pattern A is also articulated as a good idea
**irrespective of the GX10 platform bug** (capacity-planning + quality-
isolation are independent motives). **Status `proposed`** — not
deployed yet; sequenced after the Hermes single-user-side config
tightening (1-2 week trial first) per the page's "When to do this"
section. Index entry added to [index.md](./index.md). Links into
[2026-05-01 postmortem follow-ups](./runs/2026-05-01-nvidia2-abrupt-power-off-vllm-long-context.md#open-follow-ups)
and [Phase 6 of the 8-spark plan](./concepts/8-spark-fabric-and-orchestrator.md).

## [2026-04-29] concept | 8-spark-fabric-and-orchestrator | 📌 PINNED — blocked on hardware
Architecture is **fully designed**: 7-phase migration plan, three-plane
split (8 Sparks Docker + Ansible / 1 MS-A2 pure systemd, 32 GB / 20×
ComputeBlade Pi k8s), TP=8 single-replica deployment shape at
`gpu_memory_utilization=0.75-0.80`, **production model decided** as
**Qwen3-VL-235B-A22B-FP8** (vision + reasoning + coding unified, 22B-
active MoE for fast single-stream decode, 256k+ context — vision-as-
major-plus drives the unified model choice over coding-specialist
Coder-480B), with A/B targets `Qwen3-Coder-480B-Instruct-AWQ-INT4` /
`DeepSeek-V3.2-AWQ-INT4` / `Kimi K2.5-AWQ-INT4` accessible via
`spark-model-cutover`. **Execution paused** awaiting three pieces of
hardware: (1) Minisforum MS-A2 (32 GB) → unblocks Phases 2-3 (orchestrator1
provisioning, daemon migration, HF cache origin shift); (2) MikroTik
CRS804-4DDQ-HRM switch → unblocks Phases 1, 4 (link-local subnet
migration to `10.10.100.0/24`, switched fabric validation); (3) 6×
additional DGX Spark → unblocks Phases 5-7 (full 8-node fleet, TP=8
deploy, 26.04/torchrun re-evaluation). **Today's cluster (26.03-py3 +
Ray + TP=2 + Qwen3.6-35B-A3B-FP8) stays as is** — no inventory or role
changes scheduled. **Prefetch is deferred** — `Qwen3-VL-235B-A22B-FP8`
will NOT be added to `hf_prefetch_models` until Phase 3 lands; until
then there's no point filling Spark disks for a model the cluster
can't run yet. New "Next actions on hardware arrival" section in the
page captures the per-trigger checklist (steps + dependent phases) so
arrival-day work is mechanical. Optional pre-arrival work (sketch
`roles/orchestrator/`, file MikroTik config sketch, file Pi cluster
scope page) listed under "What to do while waiting" — none required.
Full content + Mermaid diagrams + decision matrix:
[concepts/8-spark-fabric-and-orchestrator.md](./concepts/8-spark-fabric-and-orchestrator.md).

## [2026-04-29] concept | 8-spark-fabric-and-orchestrator | shipped
Filed forward-looking architecture concept page for the
2 → 8 DGX Spark expansion. Captures the hardware roster (Minisforum MS-A2
control plane, MikroTik CRS804-4DDQ-HRM 4× QSFP-DD switch breakout to 8×
QSFP56 200 GbE for the Sparks), the **link-local → routable** subnet
migration (`169.254.0.0/16` → `10.10.100.0/24` in `host_vars` +
`firewall_spark_interconnect_cidr`; doable on today's direct cable before
the switch even arrives), a Mermaid topology diagram showing the
LAN-control-plane vs QSFP-data-plane separation (orchestrator1 is **not**
on the QSFP fabric — c10d rendezvous is LAN-side, NCCL data plane stays on
the fabric via `MASTER_ADDR=<rank-0 spark QSFP IP>`), a 7-phase migration
plan (each phase independently shippable in a maintenance window), and a
**Docker-vs-k8s decision** for the Sparks: stay on Docker + Ansible
(matches existing `roles/vllm_stacked_container`, sidesteps the
NCCL+CNI-overlay problem that makes RoCE-in-pods a research project), run
k3s on `orchestrator1` for everything **else** (gateway, daemons,
observability, future Hermes microservices). Decision is reversible — we
can join Sparks into the k3s cluster later if multi-tenant or fleet>16
becomes a concern. Two genuine k8s wins called out (auto-restart of
`vllm serve` on crash, and declarative rolling image upgrades replacing
the autoupgrade daemon); the first is achievable with simpler bare Docker
by baking `vllm serve` into the container entrypoint instead of
`docker exec -d`-ing it after Ray comes up — already an open follow-up
from the 2026-04-27 postmortem. Repo readiness check shows N=2 → N=8
generalises cleanly with two real action items: re-render
`vllm-stack-autoupgrade` `peers:` list from inventory iteration, and
rewrite `just spark-vllm-{head,worker}-*` recipes to iterate
`groups['sparks']` instead of hardcoding `nvidia1`/`nvidia2`. Full
content + diagrams + cross-refs:
[concepts/8-spark-fabric-and-orchestrator.md](./concepts/8-spark-fabric-and-orchestrator.md).

## [2026-04-29] run | cluster-recovery-and-26.04-rollback | shipped
Five-cause incident chain in 3 hours of a single morning after an ASUS Ascent
firmware update reboot (BIOS `0100.2025.0916.1213` → `0103.2026.0129.1152` on
both Sparks): (1) **OTel `getenv()` SIGSEGV** in `vllm/tracing/otel.py`'s
`OtlpGrpcMetricExporter` periodic exporter — known aarch64 + glibc + gRPC
race; kills the worker `RayWorkerWrapper` actor on nvidia2; engine core sees
`ActorDiedError`; API never binds. Fix: `OTEL_SDK_DISABLED=true` +
`OTEL_{METRICS,TRACES,LOGS}_EXPORTER=none` in `vllm_distributed_extra_env`.
(2) **HF Hub HEAD storm** at startup — `transformers` probes optional config
files (e.g. `preprocessor_config.json`) on every cold start; today HF
returned a transient `504`. Same retry path that hung 2026-04-27
post-engine-init. Fix: re-added `HF_HUB_OFFLINE=1` to
`vllm_distributed_extra_env` (paired with `hf-prefetch.service`'s local-cache
guarantee). (3) **nvidia2 abrupt power-off at 10:49:40 EEST** — all four
mlx5 NICs go LINK DOWN simultaneously on nvidia1, ARP entry to .229 goes
`FAILED`; nvidia2 is unreachable on LAN + interconnect. Same
kernel-undetectable platform-level shutdown postmortemed for nvidia1 on
[2026-04-27](./runs/2026-04-27-ray-head-exited-postmortem.md), now confirmed
on both Sparks. ASUS firmware update did NOT fix the underlying issue. (4)
While operator was triaging the abrupt-power-off, **`vllm-stack-autoupgrade`
fired** at 11:15:44 and promoted `nvcr.io/nvidia/vllm:26.03-py3` →
`26.04-py3` (synced earlier today by `ngc-image-sync.service`). The new
container exited 127 in a loop: `/bin/bash: line 1: ray: command not
found` — 26.04 puts `ray` on a path only on the **login shell** PATH; our
role's `--entrypoint /bin/bash -c 'ray start ...'` was broken on this
image. Fix: changed `bash -c` → **`bash -lc`** in
`roles/vllm_stacked_container/tasks/main.yml` for both head and worker
docker runs (forward-compat with any future image PATH change). Daemon
correctly went into `status=error, reason="promotion failed — operator
triage required"` per its safety rail; we manually rolled back via
`just spark-vllm-provision-recreate`. (5) **`spark_apt` role
tag-propagation bug** — `--tags apt` only matched the outer
`include_role` task, not the role's inner `apt update`/`upgrade`/
`autoremove` tasks. Same wart filed as a follow-up in
[runs/2026-04-19-qwen3_6-35b-a3b-promoted.md](./runs/2026-04-19-qwen3_6-35b-a3b-promoted.md).
Fix: added `apply: { tags: [spark, apt] }` to the `include_role` in
`roles/spark_provision/tasks/main.yml`. **apt upgrade then landed
properly**: `dgx-dashboard 0.23.3 → 0.25.11`,
`dgx-spark-ota-update-meta 26.03.1 → 26.04.1`,
`nvidia-dgx-telemetry 4.11 → 5.22`, plus 3 new `nvidia-spark-*`
packages (`avahi-conf`, `limits`, `ota-check`) pulled by `26.04.1`. Both
hosts now flag `*** System restart required *** nvidia-spark-limits`.
**Six new `just` recipes** for the lifecycle: `spark-reboot{,-required}`,
`spark-apt-upgrade`, and `spark-autoupgrade-{status,enable,disable}`.
Cluster fully recovered: 2 ALIVE Ray nodes, `/v1/models` returns
Qwen3.6-35B-A3B-FP8 advertised under multiple `served-model-name`s. Open
follow-ups: kdump-arm both hosts (per the 2026-04-27 hardening list, now
confirmed-needed on both), reboot for `nvidia-spark-limits`, only re-arm
the autoupgrade daemon AFTER the next `spark-vllm-provision-recreate`
captures the new `bash -lc` argv into running containers, retry
`iproute2` upgrade under `full` mode. Full timeline + per-issue triage:
[runs/2026-04-29-cluster-recovery-and-26.04-rollback.md](./runs/2026-04-29-cluster-recovery-and-26.04-rollback.md).

## [2026-04-27] run | ray-head-exited-postmortem | shipped
Operator returned to the cluster post-reboot to find `vllm-ngc-ray-head`
in `Exited (1)` despite `--restart unless-stopped`. Diagnosed via
`docker cp` of the stopped container's `/tmp/ray` session logs +
previous-boot journal grep — Ray took a clean SIGTERM at 14:28:31 EEST,
and the previous-boot journal pinned the SIGTERM origin to an Ansible
`docker stop vllm-ngc-ray-head 2>/dev/null || true` (i.e. the
`just spark-vllm-stop` operator path). Docker then refused to relaunch
the container at the 14:42 reboot because `unless-stopped` deliberately
respects manual stops (`hasBeenManuallyStopped=true`). The role's start
path tried `docker run -d --name vllm-ngc-ray-head ...` and collided
with the existing Exited container — fixed by mirroring the worker's
2026-04-19 "Start stopped Ray worker container (reuse existing name)"
fallback for the head: `docker start` first, only `docker run` when no
container exists by that name. New concept page filed:
[concepts/restart-unless-stopped-after-manual-stop.md](./concepts/restart-unless-stopped-after-manual-stop.md).
Operator surface filled out with **10 new `just spark-vllm-*` recipes**
(`ps`, `head-start`, `head-restart`, `worker-restart`, `start`,
`restart`, `api-kill`, `api-restart`, `dashboard`) closing the
"`spark-vllm-stop` had no symmetric `spark-vllm-start`" lifecycle gap.
Separately, exposed the **Ray dashboard on the LAN**: head's
`ray start --head` now passes
`--dashboard-host=0.0.0.0 --dashboard-port=8265`; new defaults
`vllm_stacked_container_dashboard_host` /
`vllm_stacked_container_dashboard_port`; 8265 added to
`firewall_trusted_lan_tcp_ports` only (LAN, not world); new recipe
`just spark-vllm-dashboard` opens
`http://192.168.1.104:8265/`. Hygiene: removed `HF_HUB_OFFLINE: "1"`
from `vllm_distributed_extra_env` again (had reappeared since the
2026-04-18 fix; `curl -4`/`curl -6` to huggingface.co both `200` after
this session's reboot), kept `RAY_CGRAPH_get_timeout: "900"`. Open
follow-up: `vllm serve` API server hangs post-engine-init with current
Qwen3 flag set on the 5-day-old container path; recommended recovery
matches the [2026-04-19 fp8-stack-cutover](./runs/2026-04-19-fp8-stack-cutover.md)
ritual (`spark-vllm-api-kill` + `drop_caches` +
`spark-vllm-provision-recreate`). Full timeline + diagnostic path:
[runs/2026-04-27-ray-head-exited-postmortem.md](./runs/2026-04-27-ray-head-exited-postmortem.md).

## [2026-05-19] run | nccl-gid-ray-carryover | shipped (infra + wiki sync)

Cross-node TP=2 failed at `ncclCommInitRank` (“unhandled system error”) even after
per-host `show_gids` / `spark_nccl_ib_gid_index` landed in Ansible: vLLM's Ray executor
still **copied `NCCL_IB_GID_INDEX` from the driver into followers**, overwriting
follower-local env-file values. Fix: bind-mount `/etc/vllm-ngc-stacked/ray_non_carry_over_env_vars.json`
→ `/root/.config/vllm/ray_non_carry_over_env_vars.json` on **both** NGC containers
(listing `NCCL_IB_GID_INDEX`) + **recreate** to pick up the volume. Canonical page:
[runs/2026-05-19-nccl-gid-ray-carryover.md](./runs/2026-05-19-nccl-gid-ray-carryover.md).
Concept refresh (fixes historical drift vs dual PCIe path Cage A):
[concepts/nccl-on-spark.md](./concepts/nccl-on-spark.md),
[concepts/ngc-stacked-container-stack.md](./concepts/ngc-stacked-container-stack.md).

## [2026-05-19] meta | bootstrap path | note for agents

The repo's agent bootstrap markdown is **`llmwiki/AGENTS.md`** (see “Bootstrapping order”).
There is **no top-level `./AGENTS.md`** in-tree today — start from **`llmwiki/AGENTS.md`**
when an operator mentions “AGENTS.md” without a path.

