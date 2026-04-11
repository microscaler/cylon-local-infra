# Hugging Face cache on Sparks

Target layout: **`HF_HOME=/home/nvidia/.cache/huggingface`** (see `vllm_hf_home` in `inventory/group_vars/sparks.yml`) so downloads and vLLM use the **`nvidia`** account consistently.

## Inventory snapshot (run `provision_sparks.yml --tags diagnostics` with `spark_provision_diagnostics: true` for current state)

**Migration done:** `/root/.cache/huggingface` was **`mv`**’d to **`/home/nvidia/.cache/huggingface`** on **nvidia1** (same filesystem), then **`chown -R nvidia:nvidia`**. **nvidia2** had no root cache.

| Host | `/home/nvidia/.cache/huggingface` | `/root/.cache/huggingface` |
|------|-----------------------------------|------------------------------|
| **nvidia1** | **~149 GiB** (was under root; now owned by **nvidia**) | **Removed** (directory moved) |
| **nvidia2** | Empty | Empty |

**`vllm.service` on nvidia1** still had **`HF_HOME=/root/.cache/huggingface`** until redeployed from the current role (templates use **`nvidia`**). Redeploy: `ansible-playbook playbooks/provision_sparks.yml --tags vllm_serve` (when single-node service is enabled).

## Probe (read-only)

```bash
ansible-playbook playbooks/provision_sparks.yml --tags diagnostics -l sparks
```

## CLI tools on the host

With **`huggingface_hub[cli]`** in the venv, use **`hf`** (recommended). **`huggingface-cli`** still exists but Hugging Face **deprecates** it in favor of **`hf`**.

- **`nvidia`** login shells get **`/opt/vllm/venv/bin`** on **`PATH`** via **`/etc/profile.d/cylon-nvidia-vllm.sh`**. Other users (e.g. **`casibbald`**) should call **`hf`** by **full path** or **`sudo -u nvidia -H bash -lc 'hf …'`**.

Example (list cached repos; paths follow **`HF_HOME`** — replaces deprecated **`huggingface-cli scan-cache`**):

```bash
sudo -u nvidia bash -c 'export HF_HOME=/home/nvidia/.cache/huggingface; /opt/vllm/venv/bin/hf cache ls'
```

## Decisions (next steps)

1. **Migrate vs re-download**  
   - **Move** `/root/.cache/huggingface` → `/home/nvidia/.cache/huggingface` with `rsync`/`mv` and fix ownership (`chown -R nvidia:nvidia`), **or**  
   - **Delete** root cache and re-pull only the models you want under `nvidia` (saves space if you drop Qwen3-Coder-Next).

2. **Ansible-managed models**  
   - Set **`hf_prefetch_models`** in **`inventory/group_vars/sparks.yml`** (list of repo strings and/or `{ repo, revision }` dicts), set **`spark_provision_hf: true`**, then run **`ansible-playbook playbooks/provision_sparks.yml --tags hf`**. This runs **`hf download`** as **`nvidia`** with **`HF_HOME`** set.

3. **Removing cache**  
   - Per repo: remove `hub/models--Org--Name` under `HF_HOME`, or use **`hf cache delete`** / legacy **`huggingface-cli delete-cache`** where supported.  
   - **Root** cache removal (after migrating vLLM to `nvidia`): `rm -rf /root/.cache/huggingface` only when nothing else needs it.

## References

- [Hugging Face Hub — cache layout](https://huggingface.co/docs/huggingface_hub/guides/manage-cache)
- [Hugging Face Hub CLI (`hf`)](https://huggingface.co/docs/huggingface_hub/guides/cli)
