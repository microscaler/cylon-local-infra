# cylon-local-infra — operator command surface
#
# Primary use today: `dev-tunnel-*` recipes to keep an SSH tunnel open between
# this Mac and ms02 while the Starlink router filters LAN traffic on the ports
# our dev stack needs. Retires when the USB 2.5GbE adapter arrives.
#
# See:
#   - docs/dev_hosts.md
#   - ~/.ssh/config.d/ms02-dev-tunnel
#   - llmwiki/concepts/starlink-wifi-lan-port-filter.md

set shell := ["bash", "-uc"]

# SSH alias defined in ~/.ssh/config.d/ms02-dev-tunnel
tunnel_host := "ms02-dev-tunnel"
# ControlMaster socket — matches the ControlPath in the SSH config stanza.
# Prefixed `tunnel-` to keep it distinct from the plain `Host ms02` alias used
# by Cursor Remote-SSH, so `dev-tunnel-down` doesn't kill the editor session.
# %h in the ControlPath expands to the HostName from the SSH config (the IP
# 192.168.1.189), not the alias — that's deliberate: aliases move, IPs mostly
# don't.
tunnel_sock := env_var("HOME") + "/.ssh/cm/tunnel-casibbald@192.168.1.189:22"
# Where `ssh -f` writes the "connection established" confirmation.
tunnel_log  := "/tmp/ms02-dev-tunnel.log"

default:
    @just --list

# ── Tunnel lifecycle ────────────────────────────────────────────────────────

# Start the SSH tunnel in the background (idempotent).
dev-tunnel-up:
    #!/usr/bin/env bash
    set -euo pipefail
    if ssh -O check {{tunnel_host}} 2>/dev/null; then
        echo "✓ tunnel already up ({{tunnel_host}})"
        exit 0
    fi
    : > {{tunnel_log}}
    # -N = no remote command; -f = background after auth; ExitOnForwardFailure
    # aborts if any port is already bound locally.
    ssh -N -f {{tunnel_host}} 2>>{{tunnel_log}}
    sleep 1
    ssh -O check {{tunnel_host}}
    echo
    echo "Tilt UI:     http://localhost:10348/"
    echo "Grafana:     http://localhost:3000/"
    echo "Prometheus:  http://localhost:9090/"
    echo "Jaeger:      http://localhost:16686/"
    echo "MinIO:       http://localhost:9001/   (s3 on :9000)"
    echo "kube-apiserver:  https://localhost:38839   (matches ms02 kubeconfig verbatim)"
    echo "SOCKS5:      127.0.0.1:1080   (catch-all for un-mapped ports)"

# Stop the SSH tunnel + remove the ControlMaster socket.
dev-tunnel-down:
    #!/usr/bin/env bash
    set -euo pipefail
    if ssh -O check {{tunnel_host}} 2>/dev/null; then
        ssh -O exit {{tunnel_host}} 2>&1 || true
        echo "✓ tunnel stopped"
    else
        echo "tunnel was not running"
    fi
    rm -f "{{tunnel_sock}}"

# Show whether the ControlMaster is up + list all forwarded ports that are
# actually bound on the Mac right now.
dev-tunnel-status:
    #!/usr/bin/env bash
    set -euo pipefail
    if ssh -O check {{tunnel_host}} 2>/dev/null; then
        echo "✓ tunnel UP  (socket: {{tunnel_sock}})"
    else
        echo "✗ tunnel DOWN"
        exit 1
    fi
    echo
    echo "Listening ports on the Mac (from the tunnel):"
    lsof -nP -iTCP -sTCP:LISTEN 2>/dev/null \
        | awk '$1 == "ssh" {print "  " $9}' \
        | sort -u

# Probe every forwarded UI/API port. Prints one line per port with HTTP code.
# Green (2xx/3xx) = reachable; '000' = tunnel up but backend not listening yet
# (e.g. Tilt still pulling images, Grafana not rolled out).
dev-tunnel-check:
    #!/usr/bin/env bash
    set -u
    if ! ssh -O check {{tunnel_host}} 2>/dev/null; then
        echo "tunnel is DOWN — run 'just dev-tunnel-up' first" >&2
        exit 1
    fi
    probe() {
        local port="$1" label="$2" code
        code=$(curl -s -o /dev/null -w "%{http_code}" -m 3 "http://localhost:${port}/" 2>/dev/null || true)
        [[ -z "$code" ]] && code="000"
        printf "  %-5s  %-24s  http=%s\n" "$port" "$label" "$code"
    }
    echo "Probing dev-stack UIs via tunnel (3s timeout each):"
    probe 10348 "Tilt UI"
    probe 3000  "Grafana"
    probe 9090  "Prometheus"
    probe 16686 "Jaeger"
    probe 4040  "Pyroscope"
    probe 3100  "Loki"
    probe 9001  "MinIO console"
    probe 8080  "BRRTRouter"
    probe 8000  "Gateway (PW API)"
    probe 5001  "Docker registry"

# Tail whatever ssh has emitted (connection errors, keepalive drops, etc).
dev-tunnel-logs:
    @tail -f {{tunnel_log}}

# Quick sanity: show the SSH alias's effective config (--host, keepalives,
# forwards) that ssh -G would use. Handy when debugging "why isn't port X
# forwarded?".
dev-tunnel-config:
    @ssh -G {{tunnel_host}} 2>/dev/null | grep -E "^(hostname|user|port|controlmaster|controlpath|serveraliveinterval|exitonforwardfailure|localforward|dynamicforward) " | sort

# Restart: down then up. Use after adding/removing forwards in the SSH config.
dev-tunnel-restart: dev-tunnel-down dev-tunnel-up

# ── Workspace sync (thin wrapper for the ansible playbook) ──────────────────

# Push ~/Workspace/microscaler/ from Mac → ms02 additively (no deletions).
# Use after editing shared-kind-cluster or other microscaler repos locally.
sync-to-ms02:
    ansible-playbook playbooks/sync_workspace.yml -l ms02

# Push a single subtree to ms02 (e.g. just sync-path-to-ms02 shared-kind-cluster/)
sync-path-to-ms02 path:
    ansible-playbook playbooks/sync_workspace.yml -l ms02 \
        -e workspace_sync_src="$HOME/Workspace/microscaler/{{path}}" \
        -e workspace_sync_dest=/home/casibbald/Workspace/microscaler/{{path}}
