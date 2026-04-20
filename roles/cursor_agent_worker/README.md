# cursor_agent_worker

Installs Cursor's `agent` CLI and runs it as a self-hosted Cloud Agents
worker (aka "My Machines") via systemd on a dev host.

## Why this role exists

The operator wants all repo clones, docker image builds, Rust `target/`
directories, `tilt up` artifacts, and kind-cluster traffic to live on
`ms02` — *not* on the Mac laptop. Running a Cursor worker on `ms02` means:

- Cloud Agents launched from `cursor.com/agents`, Slack, GitHub, Linear, etc.
  execute their shell / file / docker / browser tool-calls **on ms02**.
- The agent loop itself still runs on Cursor's AWS, so no GPU/CPU tax on
  the worker — it just does I/O.
- Zero inbound firewall changes — the worker opens outbound HTTPS to
  `api2.cursor.sh` + `api2direct.cursor.sh`.

Relationship to Remote-SSH (which Cursor 3.x still supports):

| Surface        | What it is                              | Runs where                    |
|----------------|------------------------------------------|-------------------------------|
| **Remote-SSH** | Editor window rooted on a remote host    | Editor = Mac, files = ms02    |
| **This role**  | Cloud Agents execution target             | Agent loop = cloud, hands = ms02 |

They are complementary. You typically want both on `ms02`.

## What it does

1. Downloads the pinned `agent-cli-package.tar.gz` from Cursor's CDN and
   extracts under `~cursor_agent_worker_user/.local/share/cursor-agent/versions/<ver>/`.
2. Symlinks `~/.local/bin/agent` (+ `~/.local/bin/cursor-agent`) to the
   versioned binary.
3. Renders `/etc/systemd/system/cursor-agent-worker.service` that runs
   `agent worker start --name <host> --worker-dir <workspace>` as the
   runtime user (so docker group / ~/.ssh/config / HOME are inherited).
4. Enables + starts the unit.

## Variables (defaults in `defaults/main.yml`)

| Variable                                  | Default                                            | Purpose |
|-------------------------------------------|----------------------------------------------------|---------|
| `cursor_agent_worker_version`             | pinned lab version from Cursor CDN                 | CLI version — bump to upgrade |
| `cursor_agent_worker_user`                | `casibbald`                                         | Unix user the worker runs as |
| `cursor_agent_worker_name`                | `{{ inventory_hostname }}`                          | Name shown at cursor.com/agents |
| `cursor_agent_worker_dir`                 | `~/Workspace/microscaler`                           | Default CWD for agent tool-calls |
| `cursor_agent_worker_api_key`             | *empty*                                             | Service-account API key; use vault |
| `cursor_agent_worker_service_enabled`     | `true`                                              | Flip to `false` to install CLI but not start |
| `cursor_agent_worker_extra_env`           | `{}`                                                | Extra env for the unit (e.g. `HTTPS_PROXY`) |

## Auth: API key vs interactive login

**Recommended (headless):** create a service-account API key at
Cursor → Settings → API Keys, put it in ansible-vault (or pass via `-e`),
let the role render it into the systemd unit's ExecStart.

```
ansible-playbook playbooks/refresh_cursor_agent_worker.yml \
    -e cursor_agent_worker_api_key="cak_..."
```

**Alternative (one-time interactive):** leave the key blank and set
`cursor_agent_worker_service_enabled: false`, apply, then SSH into the
host and run `agent login` as the runtime user. The CLI writes a token
under `~/.config/cursor-agent/`. Flip the flag back to `true` and rerun —
the systemd unit will pick up those credentials.

## Rollout

```bash
# Full dev_hosts provision (includes this role):
ansible-playbook playbooks/dev_hosts.yml -l ms02

# Just this role:
ansible-playbook playbooks/refresh_cursor_agent_worker.yml -l ms02

# Verify on the host:
ssh ms02 'systemctl status cursor-agent-worker --no-pager'
ssh ms02 'sudo -u casibbald -H /home/casibbald/.local/bin/agent --version'
```

Then visit <https://cursor.com/agents>; `ms02` should appear in the
environment dropdown within ~10 seconds of the service reaching active.

## Upgrading

Bump `cursor_agent_worker_version` in the inventory (or in this role's
defaults), rerun the playbook. The new tarball extracts to its own
versioned directory and the symlinks flip atomically; the systemd unit
restarts cleanly.

Find the current version string in the upstream installer:

```
curl -fsSL https://cursor.com/install | grep 'DOWNLOAD_URL='
```

## Troubleshooting

- **Worker never appears in the dropdown** — `journalctl -u cursor-agent-worker -f`;
  most commonly an auth failure. `sudo -u casibbald -H agent worker start --debug`
  runs a preflight that prints what's wrong.
- **`agent --version` works for root but not casibbald** — check symlink
  owner + target (`ls -la ~/.local/bin/agent`).
- **Can't reach `api2.cursor.sh`** — check `HTTPS_PROXY` / `https_proxy`;
  set via `cursor_agent_worker_extra_env`.
