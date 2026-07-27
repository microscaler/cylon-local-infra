# Routed WireGuard mesh

Self-hosted, subscription-free remote network for Microscaler development.

## Always-on Linux backbone

The first deployed leg is deliberately independent of the Mac:

```text
Hostinger VPS                         ms02
76.13.1.95:51820/udp                 behind Starlink/NAT
wg0 10.77.0.1/24  <===============>  wg0 10.77.0.2/32
                 encrypted WireGuard
```

Both hosts enable `wg-quick@wg0` at boot. ms02 sends a keepalive every 25
seconds, so its outbound NAT mapping remains usable without an inbound home
router rule.

The existing reverse SSH service remains enabled and independent:

```text
ssh ms02-away  ->  Hostinger :22002/tcp  ->  ms02 :22
```

Use reverse SSH when UDP is blocked or while repairing WireGuard.

## Provisioning

From the Mac-local `cylon-local-infra` checkout:

```bash
just wireguard-mesh-check
just wireguard-mesh-up
just wireguard-linux-status
```

The first real apply generates private keys locally on each Linux host under
`/etc/wireguard/privatekey`. Private keys never enter inventory or Git. Ansible
derives and exchanges only public keys when it renders the peer configuration.

## Self-healing

WireGuard retries handshakes automatically. The role adds two additional
recovery layers:

1. `wg-quick@wg0.service` is enabled at boot.
2. `wireguard-healthcheck-wg0.timer` runs every two minutes. If the required
   peer is absent, the interface cannot be queried, or the latest handshake is
   older than three minutes, it restarts `wg-quick@wg0.service`.

Inspect:

```bash
sudo wg show wg0
sudo systemctl status wg-quick@wg0
sudo systemctl status wireguard-healthcheck-wg0.timer
sudo journalctl -u wireguard-healthcheck-wg0.service
```

## Address and route contract

- VPS: `10.77.0.1/24`
- ms02: `10.77.0.2/32`
- on-demand Mac: `10.77.0.3/32`
- ms02 advertises `10.177.76.0/24` for Kubernetes.
- ms02 advertises only `192.168.1.189/32`, not the common
  `192.168.1.0/24` hotel/home subnet.

## On-demand Mac peer

The Mac configuration is installed but never started at boot. Its split routes
are limited to:

- `10.77.0.0/24` — WireGuard mesh
- `192.168.1.189/32` — ms02 services, DNS, and NFS
- `10.177.76.0/24` — Kubernetes/MetalLB bridge

The default internet route never enters WireGuard.

Activate before leaving the home LAN:

```bash
just wireguard-mac-up
just wireguard-mac-status
```

Activation unloads the LAN-only MetalLB LaunchDaemon and removes its conflicting
static route before `wg-quick` starts. If startup fails, the LAN route is
restored automatically.

Deactivate:

```bash
just wireguard-mac-down
```

This stops WireGuard and restores the LAN MetalLB LaunchDaemon. Unmount NFS
before changing networks, then remount after the tunnel is active.

## Away-network validation

After switching Wi-Fi or using a phone hotspot:

1. `just wireguard-mac-up`
2. Confirm `10.77.0.1`, `10.77.0.2`, and `192.168.1.189` respond.
3. Resolve a `*.dev.microscaler.local` hostname through ms02 DNS.
4. Mount NFS with the existing `just nfs-up` recipe.
5. Verify the Kubernetes API and a MetalLB ingress endpoint.
6. Confirm `ssh ms02-away` still works as the independent fallback.

## Failure behaviour

- VPS reboot: systemd recreates `wg0`; ms02 keepalive re-establishes the peer.
- ms02 reboot: systemd recreates `wg0` and starts keepalives.
- Internet outage: both interfaces remain configured; handshakes resume when
  connectivity returns.
- Deleted or wedged interface: the health timer restarts `wg-quick@wg0`.
- UDP blocked: use `ssh ms02-away`; do not disable the reverse tunnel.
