# ms02 LAN edge — Ansible ownership (audit)

**Last audited:** 2026-07-16

Ad-hoc host networking for Mac ↔ shared-k8s was installed via
`shared-k8s-cluster` Python/`just` recipes. Ownership is now split across
three homes — do not dump everything into one repo.

## Ownership split

| Concern | Home | Recipe |
|---|---|---|
| Mac `/etc/resolver`, MetalLB static route, ms02 L3 forward, split-horizon DNS | **`cylon-local-infra`** | `just metallb-lan-route-*`, `just dev-dns-*`, `just mac-dev-dns` |
| CSI / Multipass bridge UFW, `microscaler-lan-proxy` **unit**, k8s API LAN, **Tilt `systemd --user` units** | **`shared-gitops-k8s-cluster/ansible`** (run **on ms02**) | `just cluster-edge-apply` / `just tilt-units-apply` |
| Multipass/k3s VM create, haproxy **config render** (`lan-exposure.yaml`) | **`shared-k8s-cluster`** | `just cluster-create`, Python `configure_lan_proxy.py` |
| MetalLB Services, democratic-csi Helm | **Flux (shared-gitops)** | inventory + stacks |

Authority for cluster edge:  
[`shared-gitops-k8s-cluster/docs/day0-host-edge-ansible.md`](../../../../remote/microscaler/shared-gitops-k8s-cluster/docs/day0-host-edge-ansible.md)
(on ms02 NFS: `~/Workspace/microscaler/shared-gitops-k8s-cluster/docs/day0-host-edge-ansible.md`).

## Live state → Ansible (this repo)

| Piece | Ansible home |
|---|---|
| `microscaler-dev-dns` + resolved drop-in | `cylon-local` role **`dev_dns`** |
| UFW :53/:80/:443 from LAN | `cylon-local` **`firewall`** + `host_vars/ms02.yml` |
| `ip_forward` + UFW FORWARD LAN ↔ `10.177.76.0/24` | `cylon-local` **`metallb_lan_route`** |
| Mac `/etc/resolver/dev.microscaler.local` | `cylon-local` **`mac_workstation`** `--tags mac_dev_dns` |
| Mac static route `10.177.76.0/24` | `cylon-local` **`mac_workstation`** `--tags metallb_route` |

## Cluster edge (shared-gitops thin Ansible)

| Piece | Role / tag |
|---|---|
| Multipass → host UFW (3260 iSCSI, NFS, SSH, hub/squid) | `multipass_bridge` |
| `microscaler-lan-proxy.service` install + render/start | `lan_proxy` |
| k3s tls-san + `shared-k8s-mac.yaml` + UFW :6443 | `k8s_api_lan` |

```bash
# From Mac:
cd ~/Workspace/remote/microscaler/shared-gitops-k8s-cluster
just cluster-edge-apply
just cluster-edge-apply tags=k8s_api_lan
```

### Kubernetes API LAN path

`192.168.1.189:6443` (haproxy) → k3s CP `:6443` (runtime IP, e.g. `10.177.76.137`).  
Mac: `KUBECONFIG=…/shared-k8s-mac.yaml` with `server: https://192.168.1.189:6443`.

## Dual-path risk

- Prefer cylon-local Ansible over `shared-k8s just dev-dns-*` (overwrites dnsmasq conf).
- Prefer `shared-gitops just cluster-edge-apply` over ad-hoc UFW / hand unit edits.
- `shared-k8s just lan-proxy-up` / `cluster-mac-access` still work; converge on the same unit paths.
