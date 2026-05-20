---
title: 8× Ascent GX10 hardware arrival — discovery and onboarding
kind: run
status: in-progress
tags: [8-spark, ascent, gx10, fleet, inventory, fabric]
updated: 2026-05-20
related:
  - concepts/8-spark-fabric-and-orchestrator.md
  - runs/2026-05-20-torchrun-migration-kickoff.md
  - entities/nvidia1.md
  - entities/nvidia2.md
---

# 8× Ascent GX10 hardware arrival — discovery and onboarding

Six additional DGX Spark (ASUS Ascent GX10 / GB10) units have arrived to complete the 8-node
fleet in [8-spark-fabric-and-orchestrator](../concepts/8-spark-fabric-and-orchestrator.md).
Production pair `nvidia1` + `nvidia2` stays on Ray + TP=2 until fabric and torchrun paths validate.

**Baseline tagged:** `spark-ray-baseline-2026-05-20`

## Discovery checklist (per box)

| Check | Command | Pass criteria |
|-------|---------|---------------|
| Serial | `sudo dmidecode -s system-serial-number` | Unique |
| BIOS | `sudo dmidecode -s bios-version` | Compare to nvidia1/nvidia2 |
| GPU | `nvidia-smi -L` | 1× GB10, driver loaded |
| PCIe | `sudo lspci -vv` (GPU + ConnectX-7 LnkSta) | 32GT/s ×4 |
| LAN | `ip -br a` | Reachable from Ansible control |
| QSFP | `ip -br a` on `enp1s0f0np0` | Link up |
| RoCE GIDs | `show_gids` | Record `NCCL_IB_GID_INDEX` |
| SSH | `ansible -m ping` after key deploy | pong |

## Proposed inventory (Phase 5)

| Host | QSFP fabric IP | LAN |
|------|----------------|-----|
| nvidia3 | 10.10.100.3 | TBD |
| nvidia4 | 10.10.100.4 | TBD |
| nvidia5 | 10.10.100.5 | TBD |
| nvidia6 | 10.10.100.6 | TBD |
| nvidia7 | 10.10.100.7 | TBD |
| nvidia8 | 10.10.100.8 | TBD |

Confirm `10.10.100.0/24` has no LAN conflict before renumbering.

## Repo changes (deferred until discovery complete)

- Add `nvidia3`..`nvidia8` to `inventory/hosts.yml`
- Create `inventory/host_vars/nvidia<N>.yml` per host
- Run `just spark-provision` (base roles first — no TP=8 yet)

## Dependency order

1. **Track A:** discovery + base provision for Sparks 3–8 (this run)
2. **Track B:** [torchrun migration](./2026-05-20-torchrun-migration-kickoff.md) on nvidia1+2
3. **Track C:** MS-A2 orchestrator + MikroTik switch (if not yet arrived)

Do **not** add TP=8 model to `hf_prefetch_models` until Phase 3 (orchestrator) lands.

## Discovery results (fill in)

| Unit | Serial | BIOS | LAN IP | QSFP IP | GID index | Notes |
|------|--------|------|--------|---------|-----------|-------|
| Spark 3 | | | | | | |
| Spark 4 | | | | | | |
| Spark 5 | | | | | | |
| Spark 6 | | | | | | |
| Spark 7 | | | | | | |
| Spark 8 | | | | | | |

## Immediate next actions

1. Rack/power/network each box; note LAN vs QSFP cabling
2. Deploy SSH keys (same pattern as nvidia1/nvidia2)
3. Run discovery checklist; fill table above
4. Coordinate with torchrun migration — 8-node TP=8 targets torchrun + 26.04, not Ray
