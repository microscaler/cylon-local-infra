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
Mac (Wi-Fi or same switch as ms02)
   │  direct LAN TCP for UIs, NFS, and kind API (no SSH port-forwards in-repo)
   ▼
ms02 (wired → Starlink LAN out → switch)
   │  docker-proxy binds 0.0.0.0:{3000,3100,8080,9090,...}
   ▼
kind-control-plane container  (172.19.0.2)
   │  kube-apiserver on :6443 (host-mapped to LAN :38839 — use that from the Mac)
   ▼
platform namespaces: observability, data, pipeline, scheduling, ...
```

Tilt lives inside ms02 and orchestrates the whole app stack; its UI is on
**`10348`**, bound to `0.0.0.0` via the `TILT_HOST` env var / `shared-kind-cluster`
justfile, but at present Tilt only opens a v6 socket — dual-stack falls back to
v4 via `bindv6only=0` so the LAN IP works from picolino.

## Direct LAN (default)

When the Mac can reach ms02 on the home LAN, use **direct URLs** — no SSH
`LocalForward` / SOCKS tunnel is maintained in **cylon-local-infra** anymore
(ms02 `ufw` + inventory open the ports you need from `192.168.1.0/24`).

- Hermes: `http://ms02:9119/` or `http://192.168.1.189:9119/`
- Tilt, Grafana, etc.: `http://192.168.1.189:<port>/` (see `just ms02-lan-check`)
- NFS: `192.168.1.189:/…` (see `just nfs-up` in the repo justfile)
- **Kubernetes API** (kind host map): `https://192.168.1.189:38839/` — set this
  as the `server:` in `~/.kube/config-ms02` (or `https://ms02:38839/` if `ms02`
  resolves). If your kubeconfig still says `https://127.0.0.1:38839`, change it
  to the LAN URL after copying from ms02.

`just ms02-lan-check` probes common HTTP ports on the LAN.

### kubectl from the Mac

```bash
scp casibbald@ms02:.kube/config ~/.kube/config-ms02
# Edit server: to https://192.168.1.189:38839 (or https://ms02:38839)
kubectl --kubeconfig ~/.kube/config-ms02 get pods -A
```

### Historical: SSH tunnel + SOCKS (retired)

Older docs used `~/.ssh/config.d/ms02-dev-tunnel` (`LocalForward 38839`,
`DynamicForward 1080`) when Wi-Fi↔LAN filtering blocked direct TCP. **`mac-provision`**
now **removes** that fragment if present; use the LAN URLs above. For one-off
break-glass forwards, add a **personal** stanza under `~/.ssh/config.d/` — not
this repo.

### Hermes Web UI

When the Mac shares a flat L2 with ms02 (USB Ethernet to the same switch, or a router that does **not** filter Wi-Fi↔LAN TCP), open **`http://ms02:9119/`** (or `http://192.168.1.189:9119/`) directly. `ufw` on ms02 allows **9119/tcp** from `192.168.1.0/24`, and `mac_workstation` adds **`ms02 → 192.168.1.189`** to `/etc/hosts` on the Mac.

If something blocks direct HTTP again, fix routing or `ufw` on ms02 — ad-hoc
`LocalForward` lines in a **personal** `~/.ssh/config.d/*` file are outside this
repo.

### Optional: wired USB Ethernet

Same-switch L2 (USB Ethernet to the switch that feeds ms02) is the usual way to
get stable sub-ms RTT to **`192.168.1.189`**.

## Cursor / VS Code Remote-SSH

ms02 is a first-class remote-IDE target. Typical SSH aliases on the Mac live in
`~/.ssh/config.d/` (operator-maintained), e.g.:

| Alias | User | Intended use |
|---|---|---|
| `ms02` | `casibbald` | Cursor Remote-SSH, everyday ssh, git |
| `ms02-root` | `root` | `/etc` surgery, ansible module tests |

`ansible-playbook playbooks/mac_workstation.yml` ensures **`Include ~/.ssh/config.d/*`**
in `~/.ssh/config` and **removes** the retired **`ms02-dev-tunnel`** fragment.

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

Agent forwarding (`ForwardAgent yes`) in your personal `Host ms02` stanza
means `git push` from Cursor's remote terminal can use the Mac's SSH keys — no
secrets copied to ms02 unless you choose otherwise.

## Local names for kind services (`registry.lan`, `*.kind.lan`)

### Active today

**LAN-first:** point `registry.lan` at **ms02's LAN IP** so Docker on the Mac hits
the registry without an SSH `LocalForward`:

```
# /etc/hosts on the Mac
192.168.1.189  registry.lan
127.0.0.1      *.kind.lan kind.lan
```

(`ufw` on ms02 should allow **5001/tcp** from the trusted LAN — see inventory.)

```bash
docker pull registry.lan:5001/<repo>:<tag>
curl -sI http://registry.lan:5001/v2/            # → HTTP/1.1 200 OK
```

**Fallback** if you intentionally have no LAN path: use a **personal** SSH
`LocalForward` or fix routing — not managed in this repo.

No server-side rename — kind internally still uses `localhost:5001` and
`kind-registry:5000` (Docker DNS name on the kind network).

### Staged for when a kind ingress controller lands

`*.kind.lan` is aspirational until an ingress controller (Contour, NGINX) is
deployed in the kind cluster *and* `shared-kind-cluster/kind-config.yaml`
maps `80:30080` / `443:30443` (or similar). At that point:

1. Add host-port mappings for 80/443 to `kind-config.yaml` and recreate the
   cluster (`just cluster-recreate` — destructive).
2. Expose ingress on the **LAN** (ms02 IP) via kind port maps + `ufw`, then
   point DNS or `/etc/hosts` on the Mac at **`192.168.1.189`** for the hostnames
   you choose (not loopback forwards).
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

Managed by `roles/firewall`. **Group** defaults live in `inventory/group_vars/dev_hosts.yml`; **ms02** overrides in `inventory/host_vars/ms02.yml` (adds **2049** for NFSv4 from the Mac and widens the LAN dev TCP range).

```yaml
firewall_allow_tcp_ports: [22, 80, 5000, 5001, 10350]
firewall_trusted_lan_cidr: "192.168.1.0/24"
firewall_trusted_lan_tcp_ports:
  - 22
  - "7000:12000"   # ufw range — picolino/LAN: Tilt, Hermes, nodeports, observability, …
```

On **ms02**, **`22`**, **`2049`**, and **`7000:12000`** are allowed from **`192.168.1.0/24`** (includes picolino on the home LAN). Re-apply after inventory edits: `ansible-playbook playbooks/dev_hosts.yml -l ms02 --tags firewall`.

### UFW vs firewalld (dual stack)

If **`firewalld` and `ufw` are both enabled**, both can program Netfilter/nftables
and traffic can drop in ways that look like “bad UFW rules” even when
`ufw status` shows allows (including for **9119** / Hermes). This role **stops and
disables `firewalld` when its unit exists** (default: `firewall_disable_firewalld: true`
in `roles/firewall/defaults/main.yml`), then **`ufw reload`**, so **UFW is the
single source of truth**. Re-apply with:

```bash
ansible-playbook playbooks/dev_hosts.yml -l ms02 --tags firewall
```

To keep firewalld for an unusual layout, set `firewall_disable_firewalld: false`
in inventory for that host/group and manage rules manually (not recommended on
hosts that also use this role’s UFW).

If a router filter blocks client-to-client TCP again, symptoms look like firewall
drops but are outside `ufw`; use `just ms02-lan-check` from the Mac to see which
HTTP ports answer on the LAN. `ufw` stays the single on-host source of truth (see **UFW vs firewalld** above).

## Related

- `llmwiki/entities/ms02.md`
- `llmwiki/entities/starlink-router.md`
- `llmwiki/concepts/starlink-wifi-lan-port-filter.md`
- `llmwiki/runs/2026-04-19-starlink-tunnel-workaround.md`
- `llmwiki/concepts/workspace-sync.md`
