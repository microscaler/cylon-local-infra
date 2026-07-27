# Resurrection nodes on ms02 (Multipass)

**Resurrection nodes** are Multipass guests that run **`cylon-daemon`** and host nested **Firecracker microVMs** (Cylon VMs). The Resurrection Hub routes work across them.

## Topology

```
ms02 (nested KVM=Y, Multipass snap)
  ├── resurrection-node-base   stopped clone source (from cylon-images/multipass)
  ├── resurrection-node-1  16 GiB / 6 vCPU  →  bridge ms02:50052 → :50051
  ├── resurrection-node-2  16 GiB / 6 vCPU  →  bridge ms02:50053
  └── resurrection-node-3  16 GiB / 6 vCPU  →  bridge ms02:50054
        └── virtiofs: tiffany→/opt, mayfly→/mayfly
```

## Image roll-out (`cylon-images/multipass`)

| Artifact | Role |
|---|---|
| `cylon-images/multipass/cloud-init.yaml` | Portable guest definition (git) |
| `resurrection-node-base` | Built once on ms02; workers cloned from it |

```bash
cd ~/Workspace/microscaler/cylon-images/multipass
just build-base    # optional manual; Ansible also builds base
```

## Prerequisites

| Requirement | Notes |
|---|---|
| **ms02 RAM** | ≥ ~56 GiB MemAvailable for 3 × 16 GiB. Stop Kind: `cd shared-kind-cluster && just kind-stop` |
| **cylon-images** | Checkout at `~/Workspace/microscaler/cylon-images` on ms02 |
| **Multipass** | Runs as **`casibbald`** (`sudo -u casibbald -H` in Ansible) |

## Operator commands (Mac, `cylon-local-infra/`)

```bash
just resurrection-nodes-up
just resurrection-nodes-status
just resurrection-nodes-down
just resurrection-nodes-shell 2
just resurrection-nodes-build
just resurrection-nodes-bridge
```

Legacy aliases: `far-multipass-*` → same recipes.

## Configuration

[`roles/far_multipass/defaults/main.yml`](../roles/far_multipass/defaults/main.yml)

| Variable | Default |
|---|---|
| `far_multipass_vm_prefix` | `resurrection-node` |
| `far_multipass_base_instance` | `resurrection-node-base` |
| `far_multipass_vm_memory` | `16G` |

## Related

- [`cylon-images/multipass/README.md`](../../cylon-images/multipass/README.md)
- Tiffany FAR: `tiffany/llmwiki/topics/far-architecture.md`
- Kind stop: `shared-kind-cluster` `just kind-stop`
