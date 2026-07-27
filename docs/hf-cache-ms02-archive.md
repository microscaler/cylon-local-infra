# Hugging Face model archive on ms02

The Spark leader (`nvidia1`) already downloads Hugging Face weights via
**`hf-prefetch.service`** (see [`docs/hf-cache-sparks.md`](./hf-cache-sparks.md) and
[`llmwiki/entities/hf-prefetch-service.md`](../llmwiki/entities/hf-prefetch-service.md)).

This doc covers the **LAN archive** on **ms02**: after each model is cached on
the leader and rsync'd to `nvidia2` over QSFP, the same daemon rsyncs the
repo's `hub/models--*` subtree to ms02 over the home network.

## How nvidia1 pulls models (summary)

| Step | What happens |
|------|----------------|
| 1 | Ansible renders `/etc/hf-prefetch/config.yaml` from `hf_prefetch_models` in `inventory/group_vars/sparks.yml`. |
| 2 | `hf-prefetch.service` on **nvidia1** (leader) polls the config every 30 s. |
| 3 | For each repo, it runs `docker run … nvcr.io/nvidia/vllm:<tag> hf download <repo>` with `HF_HOME` bind-mounted from `/home/nvidia/.cache/huggingface`. |
| 4 | When the cache is complete, it `rsync`s `models--Org--Name/` to each **sync_target** (today: `nvidia2` at `169.254.x` over QSFP). |
| 5 | Progress is in `/var/lib/hf-prefetch/state.json` (`jq .models …`). |

ms02 does **not** run the NGC vLLM image (x86 dev host vs aarch64 Sparks). The
archive is a **push from the leader**, not a second download on ms02.

## ms02 layout

| Path | Purpose |
|------|---------|
| `/home/casibbald/.cache/huggingface/hub/` | Archive mirror (same HF hub layout as Sparks) |
| `~/.ssh/authorized_keys` | `nvidia@nvidia1` key, `from="192.168.1.104"` |

Disk: check with `ssh ms02 'df -h /home'`. Full archive of all `hf_prefetch_models`
can exceed **400 GiB** — size ms02 storage before enabling every checkpoint.

## Inventory knobs

| File | Variables |
|------|-----------|
| `inventory/group_vars/sparks.yml` | `hf_prefetch_models`, `hf_prefetch_archive_enabled: true` |
| `inventory/host_vars/ms02.yml` | `hf_archive_enabled`, `hf_archive_lan_ip`, `hf_archive_ssh_user`, `hf_archive_cache_dir`, `hf_archive_ssh_from` |

## Apply

```bash
# Wire SSH + cache dir, refresh leader config, bootstrap rsync for cached models
just hf-prefetch-archive-up

# Preview only
just hf-prefetch-archive-up -- --check

# Skip the long one-shot rsync on first apply (config + SSH only)
ansible-playbook playbooks/hf_prefetch_archive_ms02.yml \
  -e hf_prefetch_archive_bootstrap_sync=false
```

Bootstrap rsync runs **`hf_prefetch_service.py --once`** on the leader (can take
hours over 1 GbE for large checkpoints). The daemon resumes normal operation
afterward.

## Operating

```bash
# Leader prefetch state (includes ms02 in reason when syncing)
ssh casibbald@nvidia1 'jq .models /var/lib/hf-prefetch/state.json'

# Archive contents on ms02
ssh casibbald@ms02 'du -sh ~/.cache/huggingface/hub/models--* 2>/dev/null | sort -h'

# Add a model: edit hf_prefetch_models in sparks.yml, then
just spark-provision -- --tags hf_prefetch
# New models sync to ms02 automatically once the leader marks them ready.
```

## References

- [`docs/hf-cache-sparks.md`](./hf-cache-sparks.md)
- [`docs/provision_sparks.md`](./provision_sparks.md) — HF prefetch + cutover runbook
- [`playbooks/hf_prefetch_archive_ms02.yml`](../playbooks/hf_prefetch_archive_ms02.yml)
