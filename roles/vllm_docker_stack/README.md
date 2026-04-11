# `vllm_docker_stack`

Deploys vendored **`contrib/spark-vllm-docker`** scripts to the **leader** Spark and installs **`vllm-docker-cluster.service`**, which runs the same flow as:

`./launch-cluster.sh --config … exec vllm serve …`

so **Ansible + systemd** own the lifecycle (push config, `systemctl restart`, journal).

## Playbook

```bash
# spark_provision_docker_vllm: true in inventory/group_vars/sparks.yml
ansible-playbook playbooks/provision_sparks.yml --tags docker_vllm
```

## Requirements

- **`vllm_docker_image`** (default `vllm-node`) present on **each** Spark (`docker images`). Build/copy via upstream `build-and-copy.sh` if needed.
- Passwordless **SSH** from leader **`vllm_docker_systemd_user`** to followers (same as NCCL/Ray).
- **`uvx`** only if you use `hf-download.sh` manually (not required for the systemd unit).

## Variables

See `defaults/main.yml`. Override in `inventory/group_vars/sparks.yml` (e.g. `vllm_docker_model`, `vllm_docker_api_port`, `vllm_docker_ib_if`, `vllm_docker_extra_serve_args`).

## Conflicts

With defaults, this **stops** bare-metal **`vllm-stacked`**, **`ray-head`**, **`ray-worker`**. Do not run both stacks. Set `vllm_docker_stop_bare_metal_stack: false` only if you manage conflicts yourself.

## Firewall

Default API port is **8000** — add it to `firewall_allow_tcp_ports` / `firewall_trusted_lan_tcp_ports` when using this stack.
