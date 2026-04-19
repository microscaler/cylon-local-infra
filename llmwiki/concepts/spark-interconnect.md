---
title: Spark QSFP interconnect (link-local)
kind: concept
status: active
tags: [networking, qsfp, link-local, interconnect]
updated: 2026-04-18
related: [concepts/nccl-on-spark.md, sources/nvidia-stacked-sparks.md, sources/dgx-spark-playbooks.md]
---

# Spark QSFP interconnect

Two-Spark direct connection over QSFP cable. Used by Ray, NCCL, vLLM TP, and any
peer-to-peer collective. Separate from the LAN and from Ansible's controller→host SSH.

## Addressing

- Interface: `enp1s0f0np0` on both Sparks.
- CIDR: link-local `169.254.0.0/16` (no DHCP, no router).
- Current IPs:
  - `nvidia1` = `169.254.102.149`
  - `nvidia2` = `169.254.37.109`

## Derivation in Ansible

`roles/vllm_stacked_container/tasks/preflight_interconnect.yml` (and the
bare-metal equivalent) run

```
ip -4 -o addr show dev enp1s0f0np0 | awk '{print $4}' | cut -d/ -f1
```

and save it as `spark_interconnect_ip`. The leader's IP is then shared as
`spark_ray_head_ip` to followers for `ray start --address=...`.

## SSH over the interconnect

`nvidia`@peer over 169.254.x must work passwordless for NCCL `mpirun` + NVIDIA's
`discover-sparks` script. `playbooks/nccl_sparks.yml` sets this up; if the probe
fails, pass `-e nccl_test_user_password=...` once to seed `ssh-copy-id`.

## Firewall

ufw `firewall_spark_interconnect_cidr: 169.254.0.0/16` is trusted for all TCP —
Ray opens a wide port range between raylets / workers / plasma stores.

## See also

- `nvidia/connect-two-sparks/` in `dgx-spark-playbooks` (physical setup).
- [`docs/spark-parity-pre-stack.md`](../../docs/spark-parity-pre-stack.md) — getting
  both Sparks aligned before stacking.
