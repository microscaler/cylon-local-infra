---
title: NGC stacked-container stack
kind: concept
status: active
tags: [ngc, docker, vllm, ray, recommended]
updated: 2026-04-18
related: [concepts/bare-metal-venv-stack.md, entities/ngc-vllm-image.md, sources/nvidia-stacked-sparks.md]
---

# NGC stacked-container stack

Docker-based TP=2 vLLM using the NGC image and host-network Ray, no systemd for the
stack itself. Implemented by `roles/vllm_stacked_container/`.

## Why we prefer this (2026-04-18)

The bare-metal venv path keeps breaking on pip resolution
([transformers-huggingface-hub-mismatch](./transformers-huggingface-hub-mismatch.md),
uvloop, fastsafetensors, …). The NGC image ships a tested, coherent set of wheels per
tag. One knob (`vllm_stacked_container_image`) moves the entire stack.

## Enable

`inventory/group_vars/sparks.yml`:

```yaml
spark_provision_vllm_stack: false              # disable bare-metal stack
spark_provision_vllm_stacked_container: true   # enable NGC container stack
# Optional: skip the venv install entirely
# spark_provision_vllm: false
# Firewall — NGC binds 8000 by default
firewall_allow_tcp_ports: [22, "{{ vllm_ray_port }}", "{{ vllm_api_port }}", 8000]
firewall_trusted_lan_tcp_ports: [22, "{{ vllm_api_port }}", "{{ vllm_ray_port }}", 8000]
```

Run: `ansible-playbook playbooks/provision_sparks.yml --tags vllm_ngc_stack`.

## What the role does

1. Asserts `spark_provision_vllm_stack: false` (no dual-ownership of Ray).
2. Stops + disables `vllm-stacked`, `ray-head`, `ray-worker`, `vllm` systemd units.
3. Discovers each host's IPv4 on `nccl_interface` → `spark_interconnect_ip` fact.
4. Pulls `nvcr.io/nvidia/vllm:<tag>` when `vllm_stacked_container_pull_image: true`.
5. Renders `head.env` (leader) and `worker-<host>.env` (follower) from
   `vllm_distributed_extra_env`.
6. `docker run -d --restart unless-stopped --network host --gpus all --shm-size 10.24g`
   with env-file + HF cache mount:
   - Leader: `ray start --block --head --node-ip-address=<leader ip> --port=6379`.
   - Follower: `ray start --block --address=<leader ip>:6379 --node-ip-address=<this ip>`.
7. Waits for Ray GCS on leader (`127.0.0.1:6379`), pauses for worker join.
8. `docker exec -d vllm-ngc-ray-head bash -lc 'vllm serve <model> --tp 2 --host 0.0.0.0
   --port 8000 --distributed-executor-backend ray ...'`.

## Operating

- Logs: `docker logs -f vllm-ngc-ray-head` (leader), `docker logs -f
  vllm-ngc-ray-worker-nvidia2` (follower).
- Restart: `docker restart vllm-ngc-ray-head vllm-ngc-ray-worker-nvidia2` (Docker
  handles this automatically on reboot via `--restart unless-stopped`).
- Recreate (image / env change): set
  `vllm_stacked_container_recreate: true` and rerun role.

## Image tag discipline

- Role default: `nvcr.io/nvidia/vllm:25.11-py3`.
- **Currently on hosts**: `nvcr.io/nvidia/vllm:26.01-py3` (pulled manually).
- Action item: when we validate a tag end-to-end with Gemma, bump the role default and
  file a note here.

## Open questions

- Does the 26.01 image include `fastsafetensors` by default? The role passes
  `--load-format {{ vllm_load_format }}` when set; verify the container has the matching
  loader.
- Can we drop `--distributed-executor-backend ray` in newer vLLM? Probably not for TP=2
  across nodes.
