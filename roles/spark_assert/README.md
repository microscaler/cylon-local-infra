# spark_assert — end-state gate for Spark provisioning

Runs at the **end** of `playbooks/provision_sparks.yml` (role `spark_provision`).
**Fails the play** if declared infrastructure state is not true — Wi-Fi off, Docker
up, vLLM containers running, `/v1/models` reachable, NCCL GID matches inventory, etc.

Disable for dry debugging: `-e spark_provision_assert=false`.

Partial reruns (escape hatch): always include **`spark_assert`**. Prefer full
`just spark-provision` over `--tags vllm_ngc_stack` alone — partial vLLM-only
runs skip Wi-Fi, network, and observability phases and caused fleet drift.

```bash
just spark-provision -- --tags hf_prefetch,spark_assert
```
