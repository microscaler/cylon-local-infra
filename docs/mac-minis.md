# Mac minis — development machines

Three Apple Mac minis are the Microscaler **development machines**: repos are
cloned, edited, compiled, tested and containerised **on the minis**. picolino
stays the Ansible controller and laptop; ms02 hands over development and later
becomes a Kubernetes node only (see [Next phase](#next-phase-ms02-becomes-kubernetes-only)).

| Field | Value |
|---|---|
| Ansible group | `mac_minis` (separate from `macs` = picolino, local connection) |
| Hosts | `mini1` / `mini2` / `mini3` — **placeholders** in `inventory/host_vars/mini<N>.yml` |
| LAN | `192.168.1.201–203` placeholders (same `192.168.1.x` range as the Sparks) |
| Connection | SSH from picolino, `ansible_user: casibbald`, `/usr/bin/python3` |
| `mac_mini_role` | `speed` / `feature` / `training` (reserved for later use) |
| Playbook | [`playbooks/mac_dev.yml`](../playbooks/mac_dev.yml) |
| Network | **Tailscale** (not the repo's WireGuard mesh) |
| FileVault | **Off** — so a mini restarts unattended after a power cut |

When the real hostnames/IPs are known, edit one line per mini in
`inventory/host_vars/mini<N>.yml` (`ansible_host`, `mac_base_hostname`; rename
the file and the `inventory/hosts.yml` entry together if the name changes).
picolino's `/etc/hosts` block and the `just` recipes derive the IPs from there.

## The one manual step: bootstrap

On the mini (signed in as `casibbald`): **System Settings → General → Sharing →
Remote Login: ON** (optionally tick *Allow full disk access for remote users*,
which lets Ansible add the Spotlight Privacy exclusion). Then on picolino:

```bash
just mac-mini-bootstrap host=192.168.1.201     # or host=mini1
```

It runs `ssh-copy-id` with picolino's public key (you type the mini's login
password once). If port 22 is closed it prints exactly what to click. Everything
after this is Ansible — including a headless Command Line Tools install
(`softwareupdate`), so `/usr/bin/python3` exists for Ansible.

## Provision, check, re-run

```bash
just mac-mini-provision-check host=mini1   # --check --diff (Charles types the sudo password)
just mac-mini-provision host=mini1         # full run (--ask-become-pass; nothing stores it)
just mac-mini-provision host=mini1         # second run → changed=0
just mac-mini-check host=mini1             # smoke test on the mini
just mac-mini-lint                         # syntax-check + ansible-lint + pytest
```

Optional run-time secrets, read from picolino's environment for that run only
(never written to git or disk by this repo):

```bash
TAILSCALE_AUTH_KEY=tskey-auth-…            # a FRESH one-off key; never an old/revoked one
HERMES_OPENAI_API_KEY="$(sops -d --extract '["…"]' …)"   # key for the Sparks' vLLM
```

Single role: `just mac-mini-provision host=mini1 --tags rust` (every task is tagged).

### Rebuild a mini from scratch

1. Erase the mini (System Settings → General → Transfer or Reset → Erase All
   Content and Settings), run Setup Assistant, create `casibbald`, **leave
   FileVault off**.
2. Remote Login on → `just mac-mini-bootstrap host=<ip>`.
3. `just mac-mini-provision host=<name>`, then the [manual sign-ins](#manual-sign-ins).
4. `just mac-mini-check host=<name>`.

Nothing on a mini is precious: code lives on GitHub, toolchains are pinned
here, and the per-host GitHub SSH key is regenerated (add the new one, delete
the old one from GitHub).

## What is installed, and why

One role per component, in this order (`playbooks/mac_dev.yml`):

| Role | What | Why |
|---|---|---|
| `mac_base` | `scutil` hostname; `pmset` sleep 0 / autorestart / wake-on-LAN; FileVault-off assert (never changes encryption); launchd maxfiles/maxproc LaunchDaemon; Spotlight + Time Machine exclusions for `~/Workspace` and toolchain roots; software-update policy (auto check/download/security, never auto-install macOS) | Always-on, headless build box that survives power cuts |
| `shell_env` | One managed line in `~/.zshrc` sourcing `~/.zshrc.d/*.zsh`; `cylon-rc-guard` wrapper | Each tool's init is a drop-in owned by its role; user rc files are never rewritten |
| `homebrew` | Homebrew checked out at a pinned tag into `/opt/homebrew`; [`roles/homebrew/files/Brewfile`](../roles/homebrew/files/Brewfile) via `brew bundle --no-upgrade`; cluster CLIs `brew pin`ned | CLI tools the repos' justfiles, Tiltfiles and CI call (grepped on ms02) |
| `rust` | rustup (pinned, sha256-checked) → fallback 1.99.0 (+1.97.0) with clippy, rustfmt, rust-src, llvm-tools; musl + glibc Linux targets; cargo-nextest, cargo-llvm-cov, cargo-zigbuild, cargo-audit, sea-orm-cli at pinned versions; sccache + musl linkers in a managed `~/.cargo/config.toml` | Fleet toolchain; `--target x86_64-unknown-linux-musl` for the amd64 cluster images |
| `golang` | gvm (pinned commit, + mercurial), Go from binaries (`gvm install -B`), fallback go1.27.4 | Charles's choice of manager |
| `python` | pyenv (pinned tag) with 3.13 global + 3.12, 3.10; uv (pinned binary) using pyenv's interpreters only | BRRTRouter/PW tooling venvs, CI parity |
| `node` | fnm (pinned) with Node 24 LTS default + 22, corepack enabled | `.nvmrc` / engines, PW gates (`tsc`, `npm`), pnpm via corepack |
| `docker_desktop` | Docker Desktop cask; CPU/RAM/disk from facts; VZ + Rosetta; containerd store; start at login; buildx verified for linux/amd64 + arm64; `DOCKER_DEFAULT_PLATFORM=linux/amd64` | pp/prod images are amd64 (PW Tiltfile, gcp-infrastructure image workflows) |
| `claude` | Claude Code at a pinned version via the official native installer; auto-updater off to hold the pin | Agent on every mini |
| `hermes` | Hermes agent via its upstream installer pinned to a release commit; `model.base_url` / `model.default` from the same `hermes_agent_openai_*` vars ms02 uses | Self-hosted LLM agent against the Sparks' vLLM |
| `tailscale` | Open-source `tailscaled` as a boot-time LaunchDaemon | Remote access; joins only by Charles's login or a fresh key |
| `dev_workspace` | `~/Workspace/microscaler`; git identity (mirrors picolino) + `init.defaultBranch main`; per-host ed25519 key; GitHub host key pinned; `gh auth status` | Ready to clone and push |

Versions are pinned in each role's `defaults/main.yml` (override in
`inventory/group_vars/mac_minis.yml`). Homebrew formulae cannot be version-pinned
individually: the Brewfile fixes *which* tools, `--no-upgrade` stops drift, and
cluster-facing CLIs are `brew pin`ned at whatever was first installed.

## Per-repo version pins (conventions)

The managers are installed so **each repo's own pin wins**; the global default
is only a fallback outside a repo.

| Language | Repo pin | How it is honoured on the minis |
|---|---|---|
| Rust | `rust-toolchain.toml` (`channel = "1.99.0"` fleet-wide; some 1.97.0 / nightly) | rustup reads it per directory and installs that toolchain on first use. Fallback `rust_default_toolchain`. Never export `RUSTUP_TOOLCHAIN`. Targets for 1.99.0/1.97.0 are pre-added (the toml files do not list them) |
| Go | `go.mod` `go` / `toolchain` lines; `.go-version` | `GOTOOLCHAIN=auto` downloads the exact toolchain a module asks for; a zsh `chpwd` hook runs `gvm use` for `.go-version` (`gvm install goX -B` if missing) |
| Python | `.python-version`; `requires-python` | pyenv shims; uv uses the same interpreters (`UV_PYTHON_PREFERENCE=only-system`, downloads off). Missing version → `pyenv install <v>` |
| Node | `.nvmrc` / `.node-version` / `package.json` `engines`; `packageManager` | fnm switches on `cd` (recursive lookup, `--resolve-engines`); corepack provides the pinned pnpm/yarn. Missing version → `fnm install` |

BRRTRouter's tooling: `just init` in the repo creates `tooling/.venv` with
`python3 -m venv` (pyenv's 3.13; the package needs `>=3.10`). PriceWhisperer's
recipes expect that BRRTRouter venv at `~/.local/share/brrtrouter/venv`
(`BRRTRouter_VENV`) — create it per the repo's own docs.

### No ancestor `.cargo/config.toml`

ms02 has `~/Workspace/microscaler/.cargo/config.toml` with `[patch]` path
overrides pointing git deps at sibling checkouts. Building with it **rewrote
repos' `Cargo.lock` files**, so it is deliberately **not** replicated on the
minis (`dev_workspace` warns if one appears). Repos build from their pinned git
dependencies. If you need to co-develop crates, add a `[patch]` in that repo
temporarily and regenerate the lockfile cleanly before committing.

## Manual sign-ins

These are Charles's clicks — Ansible detects and reports them, never performs them:

| What | How |
|---|---|
| **FileVault** (if Setup Assistant enabled it) | System Settings → Privacy & Security → FileVault → Turn Off. Provisioning fails until it is off or decrypting |
| **Docker licence** | Open Docker Desktop on the mini, accept the Docker Subscription Service Agreement. The play launches the app and reports `ENGINE NOT UP` until this is done |
| **Rosetta** (if reported missing) | On the mini: `softwareupdate --install-rosetta` (accept its licence) |
| **Claude Code** | `ssh -t casibbald@<ip> claude` → complete the browser login |
| **Hermes** | Supply the vLLM key with `HERMES_OPENAI_API_KEY` at provision time; optionally `hermes setup` on the mini for extra providers/tools |
| **Tailscale** | `ssh -t casibbald@<ip> sudo /opt/homebrew/bin/tailscale up --hostname=<name> --operator=casibbald` and open the URL, or provision with a fresh `TAILSCALE_AUTH_KEY` |
| **GitHub SSH key** | The play prints the mini's public key: add it at https://github.com/settings/ssh/new |
| **gh** | `ssh -t casibbald@<ip> gh auth login` |
| **Auto-login** (optional) | For Docker Desktop to come back after a power cut without anyone logging in, enable System Settings → Users & Groups → Automatically log in as `casibbald` (needs FileVault off; the password is yours to type) |

## Next phase: ms02 becomes Kubernetes-only

Out of scope for this change — ms02 and its playbooks are untouched. Once all
three minis pass `just mac-mini-check` and day-to-day work has moved:

1. Move remaining clones/branches from ms02 `~/Workspace/microscaler` to GitHub
   (push), then clone on the minis. Retire the NFS mount (`just nfs-*`) and
   `sync_workspace.yml`.
2. Move Hermes' gateway/Web UI and any agent workers off ms02.
3. Strip dev tooling from `dev_hosts` (Kind, `dev_workstation`,
   `cursor_agent_worker`, build caches) so ms02 runs only the k3s/Kubernetes
   node role, the ARC runners and the LAN edge.
4. Update [`desktop-dev-environment.md`](desktop-dev-environment.md) and
   [`dev_hosts.md`](dev_hosts.md) accordingly.
