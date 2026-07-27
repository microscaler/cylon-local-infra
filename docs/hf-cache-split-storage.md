# HF model storage split — Kimi local, MiniMax on ms02 NFS

Two-tier weight storage for the GB10 Spark pair:

| Model | Storage | Path on nvidia1 |
|-------|---------|-----------------|
| **moonshotai/Kimi-K2.7-Code** | Local GB10 NVMe + QSFP rsync to nvidia2 | `/home/nvidia/.cache/huggingface/hub` |
| **MiniMaxAI/MiniMax-M3** | ms02 NFS cold-store (~796 GiB) | `/mnt/ms02-hf-archive/hub` (NFS → `ms02:/srv/hf-archive`) |

Production Qwen checkpoints stay on local Spark cache with normal peer sync.

## Architecture

```text
nvidia1 (leader)
  ├─ hf download Kimi  →  local HF_HOME/hub  →  rsync QSFP → nvidia2
  └─ hf download MiniMax  →  /mnt/ms02-hf-archive/hub  ──NFS──►  ms02:/srv/hf-archive/hub

ms02
  └─ /srv/hf-archive  (NFS export to 192.168.1.104 + .229)
```

MiniMax never duplicates onto Spark NVMe. Kimi never rsyncs to ms02.

## Inventory

| File | Keys |
|------|------|
| `inventory/group_vars/sparks.yml` | `hf_prefetch_models`, `spark_hf_nfs_*`, `hf_prefetch_archive_enabled: false` |
| `inventory/host_vars/ms02.yml` | `nfs_server_exports` (`/srv/hf-archive`), `nfs_server_export_paths` |

## Apply

```bash
just spark-hf-nfs-up

# Or stepwise:
ansible-playbook playbooks/nfs_server.yml -l ms02
ansible-playbook playbooks/spark_hf_nfs.yml
```

## Operating

```bash
# Prefetch state
ssh casibbald@nvidia1 'jq .models /var/lib/hf-prefetch/state.json'

# NFS mount on Spark
ssh casibbald@nvidia1 'mount | grep ms02-hf-archive; du -sh /mnt/ms02-hf-archive/hub/models--*'

# Cold store on ms02 (same bits, local path)
ssh casibbald@ms02 'du -sh /srv/hf-archive/hub/models--*'
```

## Capacity notes

- **MiniMax** needs **~796 GiB** on ms02's ZFS pool — tight against **~800 GiB** free; drop stale `~/.cache/huggingface` partial copies on ms02 if needed.
- **Kimi** needs **~554 GiB** on nvidia1 local disk alongside Qwen3.6 (~35 GiB).

## References

- [`docs/hf-cache-sparks.md`](./hf-cache-sparks.md)
- [`playbooks/spark_hf_nfs.yml`](../playbooks/spark_hf_nfs.yml)
- [`roles/spark_nfs_client/`](../roles/spark_nfs_client/)
