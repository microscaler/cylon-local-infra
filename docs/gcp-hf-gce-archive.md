# GCE Hugging Face archive watcher

`hf-archive-downloader` is an IAP-only `e2-medium` GCE VM (2 vCPU, 4 GiB RAM)
that preserves Kimi K3 weights in `gs://microscaler-huggingface`.

## Selection policy

The watcher polls the verified `moonshotai` Hugging Face organization every
15 minutes. It normalizes repository names and metadata, so `Kimi-K3`,
`Kimi_K3`, and `KimiK3` are equivalent.

It processes one archive target at a time:

1. The exact `RedHatAI/Kimi-K3-NVFP4` NVIDIA NVFP4 repository.
2. FP8, identified primarily by predominantly `F8_*` safetensor metadata or an
   explicit FP8 quantization configuration.
3. FP16/BF16 full precision, identified by predominantly F16/BF16 safetensor
   metadata.

GGUF, MLX, ONNX, AWQ, GPTQ, EXL2, INT4/INT8, FP4, MXFP4, and NVFP4 candidates
are rejected. A candidate must be published by `moonshotai`, contain
safetensors, and expose a valid 40-character commit SHA. The sole exception is
the exact `RedHatAI/Kimi-K3-NVFP4` target, which must identify itself as Kimi
K3, declare `moonshotai/Kimi-K3` lineage, use compressed-tensors NVFP4, and
contain safetensors. Archiving it does not satisfy or disable the FP8 objective.

## Transfer lifecycle

For each precision the watcher:

1. Pins the selected repository to its immutable SHA.
2. Runs `hf download --dry-run` and checks available disk space.
3. Downloads to an on-demand 3 TiB ext4 disk at `/srv/hf-archive`.
4. Runs a serial `gcloud storage rsync --checksums-only`.
5. Repeats the rsync in dry-run mode to verify no files remain pending.
6. Saves the repository ID, SHA, and bucket path in watcher state.
7. Deletes only that verified local snapshot.
8. Continues polling for official FP8 after NVFP4, and proceeds to
   FP16/BF16 only after FP8 has completed.

Interrupted downloads and uploads are safe to retry. The state file is
`/srv/hf-archive/.watcher/state.json`.

## Operator commands

```bash
just hf-kimi-watcher-status
just hf-kimi-watcher-logs
just hf-kimi-watcher-poll
```

The deployed timer is `hf-archive-watcher.timer`; its oneshot worker is
`hf-archive-watcher.service`. The source CLI and tests are:

- `scripts/hf_archive_watcher.py`
- `scripts/test_hf_archive_watcher.py`

The VM's GCS process and thread concurrency are both fixed at one.

The data disk is disposable after GCS verification. The stopped VM retains its
30 GiB boot disk and configuration. Before restarting it for another archive,
create and attach a new disk as device `archive-data`, format and mount it at
`/srv/hf-archive`, and initialize watcher state as appropriate. Do not start the
VM while its configured archive mount is absent.
