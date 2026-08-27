# Spark LLM profiles (Qwen ↔ DeepSeek)

Mutually exclusive stacks on the 2× DGX Spark pair. Set `spark_llm_profile` in
[`inventory/group_vars/sparks.yml`](../inventory/group_vars/sparks.yml) (or pass
`-e` once via the just recipes below).

| Profile | Stack | API | Containers |
|---|---|---|---|
| `qwen38` (default) | NGC Ray or torchrun (`vllm_stack_kind`) | `:8000` | `vllm-ngc-ray-*` / `vllm-ngc-torchrun-*` |
| `deepseek_v4_flash` | Anemll DSpark mp TP=2 | `:8888` | `vllm-dspark-<host>` |

Only one profile owns both GPUs. Asserts in `roles/spark_assert` gate Ray/torchrun
checks on `qwen38` and DSpark checks on `deepseek_v4_flash`.

## Switch profiles

```bash
# Qwen (NGC) — force recreate stacked containers
just spark-profile-qwen

# DeepSeek-V4-Flash — force recreate DSpark containers
just spark-profile-deepseek
```

Equivalent Ansible:

```bash
ansible-playbook playbooks/provision_sparks.yml -l sparks \
  -e spark_llm_profile=qwen38 -e vllm_stacked_container_recreate=true

ansible-playbook playbooks/provision_sparks.yml -l sparks \
  -e spark_llm_profile=deepseek_v4_flash -e vllm_dspark_recreate=true
```

Persist the choice in `sparks.yml` (`spark_llm_profile: …`) so the next
`just spark-provision` keeps the same stack. Qwen inventory
(`vllm_default_model`, YaRN, parsers) is preserved while DeepSeek is active.

## Prune HF cache

After switching away from a large checkpoint, free hub disk (does **not** stop
vLLM). Keep list: `hf_cache_keep_models` in `sparks.yml`.

```bash
just spark-hf-prune-check   # dry-run
just spark-hf-prune         # delete unused hub trees
```

## DeepSeek runbook (short)

1. Vendor hotfixes into `roles/vllm_dspark_stacked/files/patches/` (from
   MiaAI-Lab `DeepSeek-v4-Flash-DSpark-2x-DGX-Spark/patches/`, `cp -a`).
2. Prefetch / prepare weights (`vllm_dspark_prepare_weights`, HF cache).
3. `just spark-profile-deepseek` (or set profile + recreate).
4. Verify leader: `curl -sS http://nvidia1:8888/v1/models` — expect
   `dspark_served_model_name` (default `deepseek-v4-flash-0731`).
5. Confirm no NGC containers: `docker ps` must not show `vllm-ngc-ray` /
   `vllm-ngc-torchrun`.

Back to Qwen: `just spark-profile-qwen`, then optional `just spark-hf-prune`.

See also: [provision_sparks.md](provision_sparks.md) (HF sync / model switchover).
