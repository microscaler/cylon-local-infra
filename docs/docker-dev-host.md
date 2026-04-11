# Docker on the dev host (`ms02`)

Sparks use the **DGX / NVIDIA** image and **do not** install Docker via Ansible ([docker-dgx.md](docker-dgx.md)). The **dev host** is different: it is for day-to-day development — **Kind**, **Docker Buildx**, **Compose**, and tools like **[nektos/act](https://github.com/nektos/act)** (GitHub Actions locally). Those workflows expect a **full Docker Engine** with the **Buildx** and **Compose v2** plugins, which is the **Docker CE** stack from **Docker’s official Ubuntu repository**, not the standalone Ubuntu **`docker.io`** package.

## Recommended stack (ms02)

| Goal | Why CE from Docker.com |
|------|-------------------------|
| **buildx** | Shipped as **`docker-buildx-plugin`** alongside `docker-ce`. |
| **Compose v2** | **`docker-compose-plugin`** (`docker compose`). |
| **act** | Needs a working **Docker daemon**; CE is the usual choice on Ubuntu dev machines. |
| **Kind** | Uses the host Docker API; CE is well-tested with Kind. |

After a **one-time** install of CE, routine Ansible uses the **`docker`** role with **`docker_install_packages: false`** so we do not fight the engine with **`docker.io`**.

## One-time install (Ansible)

1. If the host still has a **mixed** `docker.io` + Docker.com leftovers, clean it ([docker-cleanup.md](docker-cleanup.md) **Path A**), or at least **`apt purge docker.io`** and remove **`rc`** rows for `docker-ce` / `containerd.io` as described there.

2. Install CE + plugins:

   ```bash
   ansible-playbook playbooks/docker_dev_engine.yml -l ms02
   ```

3. Apply sudoers, docker group, optional `daemon.json`, firewall, Kind:

   ```bash
   ansible-playbook playbooks/dev_hosts.yml -l ms02
   ```

If **`docker_ce`** fails because your Ubuntu codename is not in Docker’s repo yet, override (example):

```bash
ansible-playbook playbooks/docker_dev_engine.yml -l ms02 -e docker_ce_release_override=noble
```

## Inventory defaults (`inventory/group_vars/dev_hosts.yml`)

- **`docker_install_packages: false`** — do not install **`docker.io`** via the generic **`docker`** role.
- **`docker_group_users`** — e.g. `casibbald` in the `docker` group.

## NVIDIA GPUs on the dev host (optional)

If you later run GPU containers on `ms02`, add the **[NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)** and run `nvidia-ctk runtime configure --runtime=docker` (and **`containerd`** only if something on the host uses it directly). Kind + GPU is a separate topic ([docker-cleanup.md](docker-cleanup.md) Kind note).

## References

- [Docker Engine install (Ubuntu)](https://docs.docker.com/engine/install/ubuntu/)
- [act prerequisites](https://github.com/nektos/act#overview--installation)
