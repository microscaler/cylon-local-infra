# ms02 reverse tunnel (Hostinger VPS)

Reach **ms02** from anywhere while away from the home LAN. ms02 opens an **outbound** reverse SSH tunnel to `root@srv1719193.hstgr.cloud` (`76.13.1.95`). No inbound ports on the home router are required.

## Quick start

From the Mac (on LAN, before leaving):

```bash
just ms02-reverse-tunnel-up      # ansible: VPS sshd + ms02 systemd + Mac alias
just ms02-reverse-tunnel-status  # verify service + port 22002
```

From away:

```bash
ssh ms02-away                    # ~/.ssh/config.d/ms02-via-hostinger
# or
ssh -p 22002 casibbald@76.13.1.95
```

## What gets provisioned

| Host | Component | Purpose |
|------|-----------|---------|
| `srv1719193` | `GatewayPorts clientspecified` sshd drop-in | Allows `-R 0.0.0.0:22002:…` bind |
| `srv1719193` | `root` authorized_keys | ms02 + Mac keys |
| `ms02` | `ms02-reverse-tunnel.service` (autossh) | Persistent outbound tunnel |
| `ms02` | `~casibbald/.ssh/config.d/hostinger-tunnel` | SSH alias for VPS |
| Mac | `~/.ssh/config.d/ms02-via-hostinger` | `Host ms02-away` alias |

## Ports

| Port | Where | Maps to |
|------|-------|---------|
| `22002` | VPS public IP | ms02 SSH (`127.0.0.1:22`) |

VPS SSH stays on `:22`. ms02 LAN SSH (`192.168.1.189:22`) is unchanged.

## Resilience (systemd + autossh)

The tunnel is **`ms02-reverse-tunnel.service`** on ms02 — not a manual `ssh -fN` or cron job.

| Mechanism | Behaviour |
|-----------|-----------|
| `systemd` `Restart=always` | Restarts the unit whenever autossh exits (network blip, VPS reload, ssh crash) |
| `StartLimitIntervalSec=0` | No systemd rate-limit — keeps retrying indefinitely |
| `After=network-online.target` | Waits for routable network after reboot |
| `autossh -M 0` | Supervises the ssh child; `AUTOSSH_GATETIME=0` = restart immediately |
| `ServerAliveInterval=30` × `CountMax=3` | Detects dead tunnel in ~90s and reconnects |
| `ConnectTimeout=30` | Fail fast when VPS/internet unreachable instead of hanging |

```bash
sudo systemctl status ms02-reverse-tunnel
sudo journalctl -u ms02-reverse-tunnel -f
```

After a reboot, systemd starts the tunnel automatically (`enabled`).

## Operations

```bash
# On ms02
sudo systemctl status ms02-reverse-tunnel
sudo journalctl -u ms02-reverse-tunnel -f

# On VPS — confirm listener
ssh root@76.13.1.95 'ss -tlnp | grep 22002'

# Disable tunnel (return to LAN-only)
ansible-playbook playbooks/ms02_reverse_tunnel.yml -l ms02 \
  -e ms02_reverse_tunnel_enabled=false
```

## Security notes

- Tunnel auth: ms02 uses `casibbald`'s Ed25519 key to connect **outbound** to the VPS as `root`.
- Inbound to ms02 via the tunnel still requires ms02 `casibbald` credentials (or your ms02 authorized_keys).
- Rotate by regenerating keys and re-running the playbook.
- The VPS exposes port `22002` to the internet — consider fail2ban or firewall allow-lists if brute-force noise appears.

## Related

- `playbooks/ms02_reverse_tunnel.yml`
- `roles/ms02_reverse_tunnel/`, `roles/tunnel_hub/`
- LAN access (at home): `docs/dev_hosts.md`, `Host ms02` in `~/.ssh/config.d/ms02`
