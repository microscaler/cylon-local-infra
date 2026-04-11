# Vendored scripts: [spark-vllm-docker](https://github.com/eugr/spark-vllm-docker)

These files are copied from the **MIT-licensed** community project (see `LICENSE`) so we can **operate and inspect** the same start flow as a known-good DGX Spark stack, independently of Ansible. They are **not** executed by default from our playbooks.

| File | Role |
|------|------|
| `hf-download.sh` | Download a Hugging Face model with `uvx hf download`, optionally **rsync** to peer Sparks (uses `autodiscover.sh` / `.env` for `COPY_HOSTS`). |
| `launch-cluster.sh` | Build/run **Docker** containers with **host networking**, inject **NCCL / Ray / `VLLM_HOST_IP`**, start Ray head+workers, then `exec vllm serve …`. |
| `autodiscover.sh` | Interface + peer discovery (GB10 check), `.env` loading (`CLUSTER_NODES`, `COPY_HOSTS`, `ETH_IF`, `IB_IF`, …). |
| `.env.example` | Template for cluster configuration. |

**Upstream:** https://github.com/eugr/spark-vllm-docker — track updates there; refresh this directory when you need newer behavior.

## Prerequisites (on Sparks)

- Docker with GPU + `nvidia-container-toolkit` (for `launch-cluster.sh`).
- `uvx` (for `hf-download.sh`; script prints install hints if missing).
- Passwordless SSH between nodes (same as our NCCL / Ray playbooks).
- Optional: `ibdev2netdev` for autodiscovery.

## Example: prefetch + MiniMax M2 (upstream README pattern)

From this directory on the **head** Spark (after `docker` works and image is built per upstream `build-and-copy.sh` in their repo):

```bash
./hf-download.sh QuantTrio/MiniMax-M2-AWQ -c --copy-parallel
```

```bash
./launch-cluster.sh exec vllm serve \
  QuantTrio/MiniMax-M2-AWQ \
  --port 8000 --host 0.0.0.0 \
  --gpu-memory-utilization 0.7 \
  -tp 2 \
  --distributed-executor-backend ray \
  --max-model-len 128000 \
  --load-format fastsafetensors \
  --enable-auto-tool-choice --tool-call-parser minimax_m2 \
  --reasoning-parser minimax_m2_append_think
```

**Note:** Their examples often use port **8000**; our Ansible vLLM units default to **8080** — keep ports consistent with firewall and clients.

## Relation to `cylon-local-infra` Ansible

See **`docs/contrib-spark-vllm-docker.md`** for how these practices map to **systemd**, **`vllm_distributed_extra_env`**, **`vllm_load_format`**, and Ray — so you can either run Docker for a model or mirror flags in bare-metal templates.
