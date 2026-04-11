# Docker on DGX / Spark hosts

## Why not `docker.io` from Ubuntu here?

DGX and DGX Spark systems ship with **NVIDIA-managed apt sources** (e.g. `dgx.sources`, `repo.download.nvidia.com` base OS repos) and a **Docker + containerd stack** that is validated on that image. Installing **`docker.io`** from plain Ubuntu archives on top of that can pull **`containerd`** and conflict with **`containerd.io`** from Docker CE, or otherwise duplicate the engine.

For Sparks, this repo sets **`docker_install_packages: false`** in `inventory/group_vars/sparks.yml`: Ansible **does not** install Docker packages; it only ensures **service state**, optional **`daemon.json`**, **docker group** membership, and BuildKit recovery.

## What you should rely on

- **Docker** as provided by the **DGX / Spark software image** and NVIDIA base OS updates.
- **[NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)** for GPU containers (`nvidia-ctk runtime configure --runtime=docker`, etc.) — not part of this docker role unless you add a separate role or tasks.

## Bare Ubuntu without DGX repos

On a **generic** Ubuntu node, set in group_vars:

```yaml
docker_install_packages: true
docker_packages: [docker.io]   # or your chosen docker-ce packages
```

## References

- [DGX OS 7 — installing/using containers](https://docs.nvidia.com/dgx/dgx-os-7-user-guide/appendix_g_installing_docker_containers.html) (NGC / `docker pull` workflow assumes Docker is available)
- [NVIDIA Container Toolkit — install & configure Docker](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)
