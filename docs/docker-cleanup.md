# Cleaning up mixed Docker installs

Use this after experiments with **Ubuntu `docker.io`**, **Docker CE** (`docker-ce`, `containerd.io`), and extra repos. Goal: **one engine per host**, aligned with [docker-dgx.md](docker-dgx.md).

## Probe (safe, read-only)

From the repo root:

```bash
# Sparks: set spark_provision_diagnostics: true, then:
ansible-playbook playbooks/provision_sparks.yml --tags diagnostics -l sparks
# Dev hosts: no Spark diagnostics role — inspect manually or extend a dev playbook.
```

## What we saw on your fleet (reference snapshot)

| Host | Situation |
|------|-----------|
| **Sparks** | **Docker CE** (`docker-ce`, `containerd.io`, plugins) + **NVIDIA** `nv-docker-gpus`, `nv-docker-options`. **DGX baseos** repo (`repo.download.nvidia.com/.../dgx`). **`docker.io` not installed** — this matches the NVIDIA-on-Docker-CE layout. **No purge required** for strategy; Ansible should keep **`docker_install_packages: false`**. |
| **ms02** | **Mixed**: `docker.io` + Ubuntu **`containerd`** **and** leftover **`rc`** rows for **`docker-ce`** and **`containerd.io`**, plus CE plugins (buildx, compose, rootless-extras) and **`/etc/apt/sources.list.d/docker.list`**. Runtime in use is **`docker.io` (moby 28.x)** — not Docker CE. **Pick Path A or B below** and purge the other side. |

## Sparks (nvidia1 / nvidia2)

- **Do not** install `docker.io` via Ansible.
- Optional hygiene if `apt` ever complains about a phantom `docker.io`:
  ```bash
  sudo apt purge -y docker.io
  sudo apt -f install
  ```
- If you add **[NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)** for GPU containers, configure it against the **existing Docker** engine: `sudo nvidia-ctk runtime configure --runtime=docker` then `sudo systemctl restart docker`.

## Dev host (ms02) — choose **one** path

### Path A — **Docker CE only** (closest to sparks / same docs as docker.com)

1. Stop Docker: `sudo systemctl stop docker`
2. Remove Ubuntu engine and distro containerd (Docker CE brings `containerd.io`):
   ```bash
   sudo apt purge -y docker.io docker-doc docker-compose docker-compose-v2 podman-docker containerd runc
   ```
   (Adjust if some packages are not installed; `apt` will skip.)
3. Remove residual CE config if any: `sudo dpkg --purge docker-ce 2>/dev/null || true`
4. Ensure **Docker.com** repo is enabled (`/etc/apt/sources.list.d/docker.list` is fine).
5. Install: `sudo apt update && sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin`  
   **Or** from your Ansible controller: `ansible-playbook playbooks/docker_dev_engine.yml -l ms02`
6. `sudo systemctl enable --now docker`
7. Re-add users to group `docker` if needed (Ansible `docker_group_users` via `dev_hosts.yml`).

### Path B — **Ubuntu `docker.io` only** (no Docker.com engine)

1. Stop Docker: `sudo systemctl stop docker`
2. Purge CE-side packages and plugins from Docker.com:
   ```bash
   sudo apt purge -y docker-ce docker-ce-cli docker-ce-rootless-extras docker-buildx-plugin docker-compose-plugin containerd.io
   sudo rm -f /etc/apt/sources.list.d/docker.list
   sudo rm -f /usr/share/keyrings/docker-archive-keyring.gpg
   ```
3. `sudo apt update`
4. `sudo apt install -y docker.io`
5. `sudo systemctl enable --now docker`

Then set in **`inventory/group_vars/dev_hosts.yml`**:

- Path A: `docker_install_packages: false` **if** you manage CE outside Ansible, **or** set `docker_packages` to the CE metapackages you want and document the repo.
- Path B: `docker_install_packages: true`, `docker_packages: [docker.io]`, and **no** `docker.list` from Docker.com.

## After cleanup

- `ansible-playbook playbooks/provision_sparks.yml --tags diagnostics` — confirm a single coherent stack on Sparks (when diagnostics enabled).
- `ansible-playbook playbooks/provision_sparks.yml` — users, groups, Docker, optional `daemon.json` (no conflicting `apt` install on sparks).

## Kind + GPU on dev host

If you use **Kind** with GPUs later, you still need **[NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)** and often **`nvidia-ctk`** for **both** `docker` and `containerd` depending on how you run Kubernetes — see [docker-dgx.md](docker-dgx.md) and upstream Kind/nvkind notes.
