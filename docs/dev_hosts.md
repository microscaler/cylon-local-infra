# Dev hosts — operator guide

Authoritative doc for the non-Spark developer workstation, currently **`ms02`**.

| Field | Value |
|---|---|
| Ansible group | `dev_hosts` |
| Ansible user | `root` |
| Primary playbook | `playbooks/dev_hosts.yml` |
| Secondary | `playbooks/docker_dev_engine.yml` |
| Roles | `sudoers`, `docker`, `firewall`, `kind`, `dev_workstation` |

## Layout at a glance

```
Mac (Wi-Fi → Starlink)
   │  ssh / SOCKS / port-forwards (see "SSH tunnel workaround" below)
   ▼
ms02 (wired → Starlink LAN out → switch)
   │  docker-proxy binds 0.0.0.0:{3000,3100,8080,9090,...}
   ▼
kind-control-plane container  (172.19.0.2)
   │  kube-apiserver on :6443 (host-mapped via DNAT to 127.0.0.1:38839)
   ▼
platform namespaces: observability, data, pipeline, scheduling, ...
```

Tilt lives inside ms02 and orchestrates the whole app stack; its UI is on
**`10348`**, bound to `0.0.0.0` via the `TILT_HOST` env var / `shared-kind-cluster`
justfile, but at present Tilt only opens a v6 socket — dual-stack falls back to
v4 via `bindv6only=0` so loopback/SSH-forward both work.

## SSH tunnel workaround (active, 2026-04-19 →)

### What problem this solves

The Mac is on Starlink Wi-Fi (`192.168.1.130`) and ms02 is on the Starlink
LAN-out port via a switch (`192.168.1.189`). They're supposed to be on the same
`/24`, and `ping` + `ssh` (port 22) work. But **TCP to any other port between
the two is silently dropped or RST'd inside the Starlink router** — confirmed
on 2026-04-19 by:

1. `ufw` accepts the packets (rule counter increments) — not our firewall.
2. With `iptables -F; iptables -P INPUT ACCEPT` and `ufw disable`, the Mac
   still can't reach `ms02:10348/8080/10349/...`.
3. `tcpdump -i eno4` on ms02 shows the **first SYN** arriving, then subsequent
   same-destination SYNs never reach `eno4` — the router is holding them.
4. Same SSH session on port 22 continues to work indefinitely.

This matches the well-known Starlink Gen3 behaviour where the Wi-Fi ↔ LAN
bridge filters unfamiliar TCP destinations between clients.

### How it works

A single multiplexed SSH connection over port 22 (which the router honours)
with ~22 `LocalForward`s + a SOCKS5 `DynamicForward`:

```
Mac loopback : 10348 ──ssh──► ms02 loopback : 10348  (Tilt UI)
Mac loopback : 3000  ──ssh──► ms02 loopback : 3000   (Grafana)
Mac loopback : 38839 ──ssh──► ms02 loopback : 38839  (kube-apiserver)
...
Mac SOCKS5 127.0.0.1:1080  ← catch-all for ports not in the list
```

`ControlMaster` keeps the channel warm so `ssh -O check` / `ssh -O exit` give
the justfile a clean on/off switch.

### Operator usage

All lifecycle is driven from the repo root:

```bash
just dev-tunnel-up        # start (idempotent)
just dev-tunnel-status    # which Mac ports are actively forwarded
just dev-tunnel-check     # HTTP-probe every UI through the tunnel
just dev-tunnel-config    # dump effective ssh -G lines (debug)
just dev-tunnel-restart   # down + up (after editing forwards)
just dev-tunnel-logs      # tail ssh stderr
just dev-tunnel-down      # stop + delete ControlMaster socket
```

Once up, the Mac treats `localhost:<port>` as if it were ms02's loopback:

```bash
open http://localhost:10348/               # Tilt UI
open http://localhost:9001/                # MinIO console
kubectl --kubeconfig ~/.kube/config-ms02 get pods -A   # kubeconfig from ms02 points at 127.0.0.1:38839 — works verbatim
```

Fetching the kubeconfig once per cluster lifetime:

```bash
scp casibbald@ms02:.kube/config ~/.kube/config-ms02
```

For anything not explicitly forwarded (e.g. an app listening on an
experimental port), point curl / a browser at the SOCKS5 catch-all:

```bash
curl --socks5 127.0.0.1:1080 http://ms02:12345/healthz
```

### Files that make it work

| Path | Purpose |
|---|---|
| `~/.ssh/config.d/ms02-dev-tunnel` | Host stanza, forwards, ControlMaster |
| `~/.ssh/config` | `Include ~/.ssh/config.d/*` directive |
| `cylon-local-infra/justfile` | `dev-tunnel-*` recipes |
| `~/.ssh/cm/casibbald@ms02:22` | ControlMaster socket (created at runtime) |
| `/tmp/ms02-dev-tunnel.log` | ssh stderr captured on startup |

### Retire path (USB 2.5GbE adapter, ETA days)

When the wired USB → 2.5GbE adapter arrives:

1. Plug the adapter into the Mac and the same switch that feeds ms02.
2. `ping -c 4 192.168.1.189` — expect sub-millisecond RTT, not ~120 ms (the
   single cleanest signal that we're on the same L2).
3. `curl http://192.168.1.189:10348/` — expect `HTTP/1.1 200`.
4. `just dev-tunnel-down` — tear down the tunnel.
5. Keep the SSH config stanza in place (cheap insurance for future off-LAN
   work: café, hotel, etc.); just don't start it by default.
6. Optionally add a `Host ms02-lan` stanza with no `LocalForward`s so normal
   `ssh ms02` still works straight.

## Cursor / VS Code Remote-SSH

ms02 is a first-class remote-IDE target. Three SSH aliases are defined on the
Mac (`~/.ssh/config.d/ms02`, `~/.ssh/config.d/ms02-dev-tunnel`):

| Alias | User | ControlMaster socket | Intended use |
|---|---|---|---|
| `ms02` | `casibbald` | `~/.ssh/cm/casibbald@ms02:22` | Cursor Remote-SSH, everyday ssh, git |
| `ms02-root` | `root` | `~/.ssh/cm/root@ms02:22` | `/etc` surgery, ansible module tests |
| `ms02-dev-tunnel` | `casibbald` | `~/.ssh/cm/tunnel-casibbald@ms02:22` | LocalForward bundle + SOCKS5 (see tunnel workaround above) |

Distinct sockets on purpose — `just dev-tunnel-down` only affects the tunnel,
never the editor session.

### One-time Cursor setup

1. Cursor → `Cmd+Shift+P` → **Remote-SSH: Connect to Host…** → **`ms02`**.
2. First connect installs `~/.cursor-server/` on ms02 (~80 MB); subsequent
   connections are instant.
3. `File → Open Folder…` → `/home/casibbald/Workspace/microscaler/`.
4. Integrated terminal (`Ctrl+``) runs as `casibbald` on ms02.

The `dev_workstation` role bumps inotify limits so large workspaces don't
trip `ENOSPC: System limit for number of file watchers reached`:

```
fs.inotify.max_user_watches   524288
fs.inotify.max_user_instances    512
```

Re-apply if it ever drifts:

```bash
ansible-playbook playbooks/dev_hosts.yml -l ms02 --tags dev_workstation
```

### Coexistence with the tunnel

Cursor's Remote-SSH opens its own SSH connection over port 22 — doesn't use,
or share state with, the tunnel's ControlMaster. You can freely:
- Run Cursor Remote-SSH without the tunnel up (browser-based UIs unreachable
  until you `just dev-tunnel-up`).
- Run the tunnel without Cursor attached (UIs reachable, no editor session).
- Run both — they coexist on separate TCP handshakes.

Agent forwarding (`ForwardAgent yes`) means `git push` from Cursor's remote
terminal uses the Mac's SSH keys — no secrets copied to ms02.

## Local names for kind services (`registry.lan`, `*.kind.lan`)

### Active today

`registry.lan:5001` → Mac loopback → SSH tunnel → `kind-registry` container
on ms02's `127.0.0.1:5001`.

```
# /etc/hosts on the Mac
127.0.0.1  registry.lan
127.0.0.1  *.kind.lan kind.lan
```

```bash
docker pull registry.lan:5001/<repo>:<tag>       # from the Mac, via tunnel
curl -sI http://registry.lan:5001/v2/            # → HTTP/1.1 200 OK
```

No server-side rename — kind internally still uses `localhost:5001` and
`kind-registry:5000` (Docker DNS name on the kind network).

### Staged for when a kind ingress controller lands

`*.kind.lan` is aspirational until an ingress controller (Contour, NGINX) is
deployed in the kind cluster *and* `shared-kind-cluster/kind-config.yaml`
maps `80:30080` / `443:30443` (or similar). At that point:

1. Add host-port mappings for 80/443 to `kind-config.yaml` and recreate the
   cluster (`just cluster-recreate` — destructive).
2. Append these lines to `~/.ssh/config.d/ms02-dev-tunnel` and
   `just dev-tunnel-restart`:

   ```
   LocalForward 80  localhost:80
   LocalForward 443 localhost:443
   ```

   macOS allows non-root processes to bind 80/443 on loopback since Mojave,
   so no sudo needed.
3. Add explicit per-service `/etc/hosts` entries (wildcards aren't honoured):

   ```
   127.0.0.1  grafana.kind.lan prometheus.kind.lan jaeger.kind.lan  # etc
   ```

### Why `.lan`, not `.local`

macOS's mDNS (Bonjour) resolver claims **all** `.local` lookups *before*
`/etc/hosts`. Any `*.local` name — even with a correct hosts entry — stalls
for ~3 s on multicast before falling back, and most clients (curl, docker,
browsers) have given up by then. Full rationale + diagnostic checklist in
[`llmwiki/concepts/macos-mdns-local-tld-trap.md`](../llmwiki/concepts/macos-mdns-local-tld-trap.md).

`.lan` is short, universal, never mDNS-advertised. Other safe choices:
`.test` (RFC 2606), `.internal` (ICANN-reserved for private use),
`.home.arpa` (RFC 8375). Linux and Windows work fine under `.local` — this
is purely a macOS gotcha.

## Firewall state

Managed by `roles/firewall`, variables in `inventory/group_vars/dev_hosts.yml`:

```yaml
firewall_allow_tcp_ports: [22, 80, 5000, 5001, 10350]
firewall_trusted_lan_cidr: "192.168.1.0/24"
firewall_trusted_lan_tcp_ports:
  - 22
  - "8000:11000"   # ufw range syntax — covers Tilt 10348 + microservices
```

Even with these in place, Starlink's in-router filter is the outer constraint
on LAN-to-LAN traffic until the adapter lands. `ufw` is kept correct so the
moment we're on a flat L2, nothing else needs to change.

## Related

- `llmwiki/entities/ms02.md`
- `llmwiki/entities/starlink-router.md`
- `llmwiki/concepts/starlink-wifi-lan-port-filter.md`
- `llmwiki/runs/2026-04-19-starlink-tunnel-workaround.md`
- `llmwiki/concepts/workspace-sync.md`
