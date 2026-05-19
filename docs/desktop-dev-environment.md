# desktop-dev-environment

**Canonical brief for any AI agent or new engineer working in a Microscaler repo.** Read this once per session before touching code.

> Source of truth: [`cylon-local-infra/docs/desktop-dev-environment.md`](https://github.com/microscaler/cylon-local-infra/blob/main/docs/desktop-dev-environment.md). Any repo's `AGENTS.md` that references the desktop dev environment should link to this file, not duplicate it.

---

## TL;DR — where are you?

You are running on **`picolino`** (macOS laptop, `ansible_user: casibbald`, UID 502). **You are not on the machine that owns the code.** Most repos live on **`ms02`** (Linux dev host) and are surfaced to the Mac over NFSv4 at `~/Workspace/remote`. The Kind cluster runs on `ms02`. The LLM inference stack runs on the **DGX Spark cluster** (`nvidia1` + `nvidia2`).

If you need to "run a command on ms02", you **SSH to ms02**. Editing a file in `~/Workspace/remote/...` writes to ms02 over NFS; executing a binary in `~/Workspace/remote/...` executes it on the **Mac** against code-on-ms02 (usually wrong — most tools need to run where the code lives).

---

## Host topology

```
                            ┌──────────────────────────────────────────────────┐
                            │  DGX Spark cluster                               │
                            │                                                  │
                            │  ┌──────────────────┐      ┌──────────────────┐  │
                            │  │  nvidia1 (lead)  │      │  nvidia2 (follow)│  │
                            │  │  gx10-e1ce       │◄────►│  gx10-47b5       │  │
                            │  │  169.254.102.149 │      │  169.254.37.109  │  │
                            │  │  192.168.1.104   │ QSFP │                  │  │
                            │  └──────────────────┘      └──────────────────┘  │
                            │         ▲                                        │
                            │         │ LAN 1GbE (service port)                │
                            │         │ vLLM :8000, Ray :8265/:6379            │
                            │         │                                        │
                            │  Interconnect between nvidia1 ↔ nvidia2:         │
                            │    NCCL / RoCE v2 over ConnectX-7 QSFP, MTU 9000 │
                            │    (GPU-direct RDMA — used ONLY for inter-node   │
                            │     tensor-parallel NCCL collectives; not        │
                            │     reachable from ms02 or the Mac)              │
                            │                                                  │
                            │  runtime user: nvidia                            │
                            │  ops user:     casibbald                         │
                            └──────────────────────────────────────────────────┘
                                      ▲
                                      │ LAN 100 Mbit switch port
                                      │ (HTTP/API only; NOT NCCL / NOT RoCE)
                                      │
┌───────────────────────┐   SSH :22   │                 ┌────────────────────────────┐
│  picolino  (macOS)    │◄────────────┼───────────────► │  ms02  (Linux dev host)    │
│  user: casibbald      │  +NFS :2049 │                 │  user: casibbald (UID 1000)│
│        (UID 502)      │  over SSH   │                 │  root via ansible_user=root│
│  Ansible controller   │  Forward    │                 │                            │
│  Editor host          │◄────────────┘                 │  • Source code (canonical) │
│                       │  SOCKS5 :1080                 │    /home/casibbald/…       │
│                       │  over SSH (opt-in)            │  • Kind cluster (shared)   │
│                       │◄────────────────────────────► │  • Docker / ctr            │
│                       │                               │  • Tilt targets (build +   │
│                       │                               │    push from ms02 → Kind)  │
└───────────────────────┘                               └────────────────────────────┘
         ▲
         │ wired LAN (pending): collapses SSH-tunnel tax on Mac↔ms02
         │ once Starlink is replaced with a router that does NOT
         │ filter Wi-Fi↔LAN TCP traffic on all ports except 22.
         │ See llmwiki/concepts/starlink-wifi-lan-port-filter.md
```

**Three distinct network paths, don't conflate them:**

| Edge | Link | Carries |
|---|---|---|
| `nvidia1 ↔ nvidia2` | ConnectX-7 QSFP, RoCE v2, MTU 9000, link-local `169.254.0.0/16` | NCCL tensor-parallel collectives, GPUDirect RDMA. **Not routable** — you cannot reach it from ms02 or the Mac. |
| `{ms02, picolino} ↔ {nvidia1, nvidia2}` | 100 Mbit switch port on LAN (`192.168.1.0/24`) | HTTP / OpenAI API to vLLM, Ray dashboard, SSH. Shared, modest bandwidth, **no RDMA**. |
| `picolino ↔ ms02` | Wi-Fi → Starlink → ms02 wired, multiplexed over SSH `:22` | NFS, Tilt UI, Postgres, kubeconfig, Ray dashboard forward (all via SSH LocalForward until direct LAN lands). |

**Why this shape.** ms02 owns the canonical filesystem for two reasons: (a) disk space — the Mac's SSD is too small for the full Microscaler tree + build caches + container images; (b) Tilt needs the code where the Kind cluster runs, to skip rsync round-trips on every file save.

**Why Starlink matters.** The Starlink Gen3 router filters Wi-Fi↔LAN TCP traffic on all ports except 22. Every service on `ms02` that the Mac needs (NFS, Postgres, Kubernetes API, Ray head, Tilt UI…) reaches the Mac via SSH `LocalForward` until direct LAN lands. See [`llmwiki/concepts/starlink-wifi-lan-port-filter.md`](../llmwiki/concepts/starlink-wifi-lan-port-filter.md).

---

## Filesystem layout on the Mac

```
~/Workspace/
├── local/                       ← small set of repos cloned directly on the Mac.
│   ├── cylon-local-infra/       ← THIS REPO. Ansible + justfile + llmwiki.
│   │                              Ansible runs from HERE (Mac is the controller).
│   └── minion-farm/             ← agent tooling (legacy `farm` CLI — being retired
│                                  in favor of reliable GitHub MCPs).
│
├── remote/                      ← NFSv4 mount of `ms02:/home/casibbald/Workspace`.
│   ├── microscaler/             ← ALL Microscaler org repos live here.
│   │   ├── hauliage/            ← Rust microservices (BRRTRouter + Lifeguard).
│   │   ├── tiffany/             ← SHaaS agent platform.
│   │   ├── mayfly/              ← Sandbox runtime.
│   │   ├── cylon-local-infra/   ← remote copy (read via editor; commit from ./local/).
│   │   ├── shared-kind-cluster/ ← Manifests for the ms02 Kind cluster.
│   │   └── … (~80 repos)
│   └── …                        ← other upstream sources mirrored on ms02.
│
└── microscaler/                 ← legacy/parallel checkout of microscaler repos
                                   (pre-NFS-migration scratch; prefer `remote/microscaler/`).
```

**Mount options (for diagnosis; managed by `just nfs-up`):**

```
vers=4,rsize=1048576,wsize=1048576,hard,noresvport,nfc
```

**UID squashing.** Mac `casibbald` is UID 502; ms02 `casibbald` is UID 1000. NFS exports use `all_squash,anonuid=1000,anongid=1000` so every Mac-side write lands as UID 1000 on ms02. `sec=sys` without Kerberos cannot map identities cross-OS — squashing is the pragmatic single-user fix. See [`llmwiki/concepts/workspace-mount-protocols.md`](../llmwiki/concepts/workspace-mount-protocols.md).

**Lifecycle** (from `cylon-local-infra/justfile`):

```
just nfs-up         # mount (idempotent)
just nfs-status     # one-line check + directory listing
just nfs-down       # unmount (clears stacked mounts if any)
just nfs-reconnect  # down + up atomically
```

---

## Where do commands execute?

**The single most common agent mistake:** editing a file on the Mac and then running `cargo test` or `docker build` or `kubectl apply` from the Mac terminal, which runs the command against Mac-local tooling instead of the right host. Think explicitly about *where* each command needs to execute.

| Operation | Runs on | How to invoke |
|---|---|---|
| Edit source file in `~/Workspace/remote/microscaler/<repo>/…` | Writes to `ms02` (over NFS) | Any editor on the Mac |
| Build / test Rust / Go / Python / Node code | `ms02` | `ssh ms02 'cd ~/Workspace/microscaler/<repo> && <cmd>'` |
| `docker build` / `docker compose` | `ms02` (that's where the Docker daemon and image cache live) | SSH to `ms02` |
| `kubectl` / `k9s` against the dev Kind cluster | `ms02` | SSH to `ms02`, or `kubectl --context kind-dev` on the Mac if kubeconfig is forwarded |
| `tilt up` for a repo | `ms02` (builds via local daemon, pushes to Kind registry on ms02) | SSH to `ms02`, cd into the repo, `tilt up` — view the UI through an SSH LocalForward to `:10350` |
| `ansible-playbook` (any infra provisioning) | **Mac** (controller) | From `~/Workspace/local/cylon-local-infra/` |
| `just` recipes from `cylon-local-infra/justfile` | **Mac** — but many SSH into `ms02` as their work | From `~/Workspace/local/cylon-local-infra/` |
| vLLM / Ray inference | `nvidia1` (head) + `nvidia2` (worker) | See Ray / vLLM section below |

**Anti-pattern:** `cd ~/Workspace/remote/microscaler/hauliage && cargo test`. This runs `cargo` on the Mac against code on ms02 over NFS — wrong toolchain (aarch64 Darwin vs x86_64 Linux), wrong build cache, and orders of magnitude slower. **Always SSH to `ms02` for builds/tests.**

**Correct pattern:**

```bash
ssh ms02 'cd ~/Workspace/microscaler/hauliage && cargo test -p hauliage_consignments'
```

Or interactive:

```bash
ssh ms02
cd ~/Workspace/microscaler/hauliage
cargo test -p hauliage_consignments
```

---

## SSH configuration

SSH aliases for every Microscaler host live in `~/.ssh/config.d/<group>`. Convention documented at [`llmwiki/concepts/ssh-alias-convention.md`](../llmwiki/concepts/ssh-alias-convention.md):

```
ssh ms02              # dev host, user=casibbald
ssh ms02-root         # dev host, user=root (use sparingly)
ssh nvidia1           # DGX Spark leader, user=casibbald
ssh nvidia1-runtime   # same host, user=nvidia (runs Ray/vLLM)
ssh nvidia2           # DGX Spark follower
```

The **ms02 dev tunnel** (`just dev-tunnel-up`) is a multiplexed SSH connection that holds open LocalForwards for:

| Port | Maps to | Purpose |
|---|---|---|
| 2049 | `ms02:2049` | NFS (keep alive while mount is up) |
| 8265 | `nvidia1:8265` | Ray dashboard |
| 10350 | `ms02:10350` | Tilt UI (when a repo has Tilt running) |
| 5432 | `ms02:5432` | Postgres |
| 1080 | SOCKS5 | Browser access to private LAN services |

See [`justfile`](../justfile) `dev-tunnel-*` recipes and [`llmwiki/concepts/starlink-wifi-lan-port-filter.md`](../llmwiki/concepts/starlink-wifi-lan-port-filter.md) for the *why*.

---

## The Kind cluster on ms02

**One shared cluster per dev host, namespace-per-repo.** The Kind cluster lives on `ms02` (not the Mac — needs Linux, disk space, and it's where Tilt builds land). Manifests and bootstrap scripts live in [`microscaler/shared-kind-cluster`](https://github.com/microscaler/shared-kind-cluster).

**The shared-infra namespace** hosts cross-cutting services (Postgres, NATS, observability stack, gateway, etc.) — these are provisioned once and every repo consumes them.

**Each service repo** (`hauliage`, `mayfly`, `tiffany`, …) deploys into **its own namespace** so Tilt's resource-diffing and namespace-scoped RBAC don't collide. Convention: **namespace = repo name** (lowercase, kebab-case).

Typical Tilt workflow:

```bash
ssh ms02
cd ~/Workspace/microscaler/<repo>
tilt up                   # runs on ms02; builds into local Docker, pushes to
                          # kind-registry:5000 (on ms02), updates Helm/manifests
                          # in namespace <repo>
```

From the Mac, browse the Tilt UI at `http://localhost:10350` (via SSH LocalForward). From the Mac, hit Kubernetes with `kubectl --context kind-dev ...` if you've forwarded kubeconfig — otherwise use `k9s` on `ms02` over SSH.

**Do not `kind create cluster` on the Mac.** There is one cluster; creating a local Mac cluster fragments the dev environment and nothing in `shared-kind-cluster` is wired to deploy into it.

---

## The DGX Spark cluster (nvidia1, nvidia2)

Two NVIDIA DGX Spark nodes on the LAN. The two nodes are additionally bridged directly to each other by a dedicated QSFP ConnectX-7 link running RoCE v2 with GPUDirect RDMA. **That RoCE link is internal to the pair** — used only for NCCL tensor-parallel collectives between `nvidia1` and `nvidia2`. Everything external (ms02, the Mac, agent HTTP traffic) reaches the nodes over the ordinary 100 Mbit LAN switch port, not via RoCE.

| Role | Host | User | Notes |
|---|---|---|---|
| Ray head | `nvidia1` | `nvidia` | Runs Ray head + vLLM engine. Dashboard :8265. |
| Ray worker | `nvidia2` | `nvidia` | Joins the head. Uses QSFP interconnect for NCCL. |
| Operator | both | `casibbald` | Ansible-only. Don't run workloads as this user. |

**Current workload:** vLLM serving `Qwen/Qwen3.6-35B-A3B-FP8` at `http://nvidia1:8000/v1` (OpenAI-compatible). Provisioned by [`roles/spark_provision`](../roles/spark_provision/) and [`roles/vllm_stacked_container`](../roles/vllm_stacked_container/) via [`playbooks/provision_sparks.yml`](../playbooks/provision_sparks.yml).

**Agents consume via the OpenAI API**, not by SSHing into the boxes. The vLLM server exposes:

```
POST http://nvidia1:8000/v1/chat/completions   (from LAN, including ms02)
POST https://<ngrok-domain>/v1/chat/completions (from outside LAN, opt-in tunnel)
```

Model name: `Qwen/Qwen3.6-35B-A3B-FP8` (real id) or `qwen3` (short alias for tools that gag on `/` in names — Cursor). Both are advertised via vLLM's `--served-model-name`.

**Why this matters for agents:** any work you do that could run a model locally should prefer self-hosted vLLM over a cloud endpoint. Customer PII and business documents **must not** cross a network boundary to an LLM vendor we don't control — see [`llmwiki/concepts/pii-ai-data-plane.md`](../llmwiki/concepts/pii-ai-data-plane.md) (the architectural invariant behind the whole stack).

**Do not SSH to `nvidia1` / `nvidia2`** for routine tasks. The only legitimate reasons to SSH in are (a) Ansible is telling you to for bring-up, (b) you're reading `journalctl -u vllm-stacked` to debug a failed inference, or (c) a playbook change needs to be applied. Routine model interaction is always API.

---

## Passwordless sudo on the Mac

A narrow [`sudoers`](/etc/sudoers.d/cylon-local-infra-ops) drop-in is installed by [`roles/mac_sudoers`](../roles/mac_sudoers/). The operator (`casibbald`) can run these commands without a password prompt:

```
/sbin/mount_nfs
/sbin/umount
/usr/sbin/diskutil unmount *
/usr/bin/mdutil -X *
/usr/bin/mdutil -i *
/usr/bin/tmutil addexclusion -p *
/usr/bin/tmutil removeexclusion -p *
/usr/bin/killall mds
```

Every other `sudo` invocation prompts normally. Ansible's own `become: true` also prompts once per run because sudo sees the Python interpreter path, not the target command. The drop-in pays rent for `just nfs-up` / `just nfs-down` / `just spotlight-exclude-remote` / direct mount ops, not for Ansible escalation.

---

## Agent pitfalls (read this list)

1. **Don't `cargo build` / `npm install` / `pip install` from the Mac pointed at `~/Workspace/remote/...`.** Always SSH to `ms02` and run from `~/Workspace/microscaler/<repo>` there. The NFS mount is for reading and editing, not for running toolchains with the wrong architecture.
2. **Don't create a local Kind cluster on the Mac.** There is exactly one dev cluster — on `ms02`. Adding a second fragments state.
3. **Don't commit from `~/Workspace/remote/microscaler/cylon-local-infra/`.** That's the ms02-side copy accessed over NFS; commits go through `~/Workspace/local/cylon-local-infra/` (Mac-local clone, faster git ops, no NFS metadata round-trips). Same applies to `minion-farm`.
4. **Don't assume `nvidia1`/`nvidia2` are reachable from off-LAN.** The ngrok tunnel to vLLM is opt-in (`setsid ngrok http --url <domain> 8000`), not always up. Check `just vllm-status` or read the most recent `llmwiki/runs/*-vllm-*.md`.
5. **Don't commit `Co-authored-by: Cursor …`.** Customer policy prohibits it across every Microscaler repo. No exceptions.
6. **Don't use `--no-verify` on commits.** Pre-commit hooks are there deliberately; fix what they flag.
7. **Don't paraphrase this doc into your own repo's `AGENTS.md`.** Link to it so it stays in sync. If the topology changes, this file is the one place that gets updated.
8. **Check the mount before doing anything remote-looking.** `just nfs-status` confirms the mount is up; if it isn't, `ls ~/Workspace/remote/microscaler/<repo>` will return "Device not configured" and every subsequent file op will fail with cryptic errors. Run `just nfs-reconnect`.
9. **The dev tunnel must be up for any LAN service access.** If `just dev-tunnel-status` shows no tunnel, NFS, Tilt UI, Ray dashboard, and Postgres are all unreachable from the Mac. Run `just dev-tunnel-up`.
10. **Farm CLI is deprecated outside of minion-farm.** For GitHub operations prefer `gh` CLI or the GitHub MCP; farm-git will be refactored once the MCP surface stabilizes.

---

## Canonical references

Per-topic depth lives in the cylon-local-infra wiki:

| Topic | Page |
|---|---|
| NFS Phase-2 (SSH tunnel) — actual mount options and rationale | [`llmwiki/concepts/workspace-mount-protocols.md`](../llmwiki/concepts/workspace-mount-protocols.md) |
| Network constraint behind all the SSH tunneling | [`llmwiki/concepts/starlink-wifi-lan-port-filter.md`](../llmwiki/concepts/starlink-wifi-lan-port-filter.md) |
| SSH alias scheme for every host | [`llmwiki/concepts/ssh-alias-convention.md`](../llmwiki/concepts/ssh-alias-convention.md) |
| mDNS `.local` trap (why we use `.lan`) | [`llmwiki/concepts/macos-mdns-local-tld-trap.md`](../llmwiki/concepts/macos-mdns-local-tld-trap.md) |
| PII data-plane (the architectural *why*) | [`llmwiki/concepts/pii-ai-data-plane.md`](../llmwiki/concepts/pii-ai-data-plane.md) |
| DGX Spark provisioning | [`docs/provision_sparks.md`](./provision_sparks.md), [`docs/PRD-spark-stacking-nvidia2.md`](./PRD-spark-stacking-nvidia2.md) |
| HF model cache layout | [`docs/hf-cache-sparks.md`](./hf-cache-sparks.md) |
| ms02 docker + dev tooling | [`docs/dev_hosts.md`](./dev_hosts.md), [`docs/docker-dev-host.md`](./docker-dev-host.md) |

---

## When this doc is wrong

The environment changes: the Starlink is getting replaced, NFS Phase 3 (direct LAN) will collapse the tunnel tax, more DGX hardware may land, namespaces may split. **If you observe a mismatch between this doc and reality, fix the doc as part of your session's deliverable.** A stale topology doc is worse than no doc — it routes agents into the wrong host.

Owner: `cylon-local-infra` maintainers. PRs to this file are reviewed against running infrastructure, not against opinions.
