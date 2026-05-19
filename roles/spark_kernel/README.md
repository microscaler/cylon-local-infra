# `spark_kernel`

Manages the **GRUB default kernel**, **apt holds** on the kernel/HWE meta
packages, and the **NVIDIA OTA `nvidia-spark-run-apt-upgrade-once.service`** mask
on DGX Spark hosts. Designed to support **kernel bisection** during the GX10
abrupt-power-off investigation
([llmwiki/runs/2026-05-01-nvidia2-abrupt-power-off-vllm-long-context.md](../../llmwiki/runs/2026-05-01-nvidia2-abrupt-power-off-vllm-long-context.md)).

All operations are **idempotent and reversible**. Set the inventory flags and
re-run the role; the next pass converges to the new state.

## Quick reference

```yaml
# Pin the GRUB default to a specific kernel (any installed kernel — verify
# with `dpkg -l 'linux-image-*' | grep '^ii'` on the target). Empty string
# means "leave GRUB_DEFAULT untouched".
spark_kernel_pin: "6.17.0-1008-nvidia"

# Hold the pinned kernel + the HWE meta packages so apt won't bump them.
spark_kernel_apt_hold: true

# Mask the NVIDIA OTA service so it can't undo the pin during an
# unattended apt-upgrade window.
spark_kernel_disable_auto_apt_upgrade: true

# Show the GRUB menu for N seconds at boot (0 = hidden, default).
# Useful while bisecting — set to true to override at the console.
spark_kernel_show_grub_menu: false
spark_kernel_grub_menu_timeout: 5
```

## Boolean recipes (the "true/false flags for the kernel versions" the
operator asked for)

Set exactly one true. If multiple, `spark_kernel_pin` (an explicit string)
wins — see [inventory examples](#inventory-examples).

| Flag combination | Meaning |
|---|---|
| `spark_kernel_pin: "6.17.0-1014-nvidia"` | Pin to the **current HWE default**. Useful as an explicit no-op (lets you also hold the HWE meta without changing which kernel boots). |
| `spark_kernel_pin: "6.17.0-1008-nvidia"` | **One step back** on the 6.17 line. Same nvidia-580-open module ABI as 1014. Lowest-risk downgrade. |
| `spark_kernel_pin: "6.11.0-1014-nvidia"` | **Older line** (6.11). Bigger jump back; matches an earlier DGX OS HWE. |
| `spark_kernel_pin: ""` | Leave GRUB_DEFAULT alone (manage holds and OTA service only). |

The role rejects any value that isn't an installed kernel (`dpkg -l
'linux-image-*'` must show it as `ii`).

## Inventory examples

`inventory/group_vars/sparks.yml` — fleet-wide default (commented out by
default; uncomment to apply to both Sparks):

```yaml
# spark_kernel_pin: "6.17.0-1008-nvidia"
# spark_kernel_apt_hold: true
# spark_kernel_disable_auto_apt_upgrade: true
```

`inventory/host_vars/nvidia1.yml` — per-host pin (pin one Spark while the
other stays on the current HWE as a control):

```yaml
# Bisecting GX10 hard-power-off — nvidia1 control kernel.
spark_kernel_pin: "6.17.0-1008-nvidia"
```

`inventory/host_vars/nvidia2.yml` — leave default (pin nothing) while
nvidia1 is the experiment.

## Apply

```bash
# Run only the kernel phase on both Sparks:
ansible-playbook playbooks/provision_sparks.yml --tags kernel

# Apply to one host:
ansible-playbook playbooks/provision_sparks.yml --tags kernel -l nvidia1

# Ad-hoc pin without editing inventory (useful for one-shot experiments):
ansible-playbook playbooks/provision_sparks.yml --tags kernel \
  -l nvidia1 -e spark_kernel_pin=6.17.0-1008-nvidia

# Operator surface (justfile):
just spark-kernel-status         # show current kernel + holds + OTA state
just spark-kernel-apply          # full kernel-phase run on both Sparks
just spark-kernel-show-menu yes  # expose GRUB menu for next boot
```

## What the role does (in order)

1. **Discovery** — `uname -r`, installed `linux-image-*-nvidia` packages,
   currently held packages. Always runs (safe in `--check`); emits a
   debug summary.
2. **Validate `spark_kernel_pin`** — must be one of the installed
   `linux-image-*-nvidia` packages. Fails fast if not.
3. **Discover GRUB menuentry IDs** — slurps `/boot/grub/grub.cfg`, finds
   the `Advanced options` submenu id and the `with Linux <ver>` menuentry
   id (recovery entries explicitly excluded). The IDs are per-host
   because they embed the root filesystem UUID — discovery handles this
   automatically.
4. **Set `GRUB_DEFAULT`** in `/etc/default/grub` to
   `<submenu_id>>$menuentry_id` and `update-grub` (handler).
5. **Apt holds** — narrows the candidate package list (HWE meta + kernel
   variants) to packages actually installed on the host, then
   `apt-mark hold`s them. When `spark_kernel_apt_hold: false`, releases
   ALL kernel-related apt holds.
6. **NVIDIA OTA service** — `mask`+`stop` (or `unmask`) of
   `nvidia-spark-run-apt-upgrade-once.service` if the unit exists. (This
   is a oneshot gated by `/var/lib/nvidia-spark-run-apt-upgrade-once/done`,
   so it only re-arms after a NVIDIA dpkg trigger removes the done-file.
   The main recurring upgrader is Ubuntu's `apt-daily-upgrade.timer`,
   which respects the `apt-mark hold`s above.)
7. **Reboot reminder** — debug message if running kernel ≠ pinned
   kernel. Does NOT trigger a reboot — that's the operator's call (see
   `just spark-reboot`).

## Reverting

To unpin a host completely:

```yaml
spark_kernel_pin: ""
spark_kernel_apt_hold: false
spark_kernel_disable_auto_apt_upgrade: false
spark_kernel_show_grub_menu: false
```

Then `ansible-playbook playbooks/provision_sparks.yml --tags kernel -l <host>`
followed by `just spark-reboot` if you want to actually adopt whatever the
HWE meta now points at.

## Limitations

- **DGX OS GRUB titles are hard-coded.** The discovery regex looks for
  `menuentry 'DGX OS GNU/Linux, with Linux <ver>'` inside a
  `submenu 'Advanced options for DGX OS GNU/Linux'`. If NVIDIA changes
  the title format under a future DGX OS update, the role fails fast
  with a clear message rather than silently writing a bad GRUB_DEFAULT.
- **Does NOT trigger reboots.** The role updates the persistent boot
  default; the operator chooses when to reboot. This is deliberate — a
  reboot of the Spark cluster is a coordinated event that takes vLLM
  out of service, and `just spark-reboot` already handles the
  graceful-stop sequence.
- **Does NOT install kernels.** It can only pin / hold / unhold ones
  already on disk. To install a new kernel for use here:
  `apt install linux-image-<ver>-nvidia` first.
- **No kdump arming.** The role does not configure crashkernel reservation
  (separate concern; current `/proc/cmdline` shows `crashkernel=1G-:0M` →
  no dump on next abrupt power-off). See open follow-up in
  [2026-05-01 postmortem](../../llmwiki/runs/2026-05-01-nvidia2-abrupt-power-off-vllm-long-context.md#open-follow-ups).

## Related

- [`roles/spark_apt`](../spark_apt/) — apt update/upgrade lifecycle. Be
  aware that `apt upgrade` will respect the holds this role sets.
- [`vllm-stack-autoupgrade.service`](../vllm_stack_autoupgrade/) —
  separate Docker-image autoupgrade for the NGC vLLM container; unrelated
  to the kernel autoupgrade. Both are mask-able independently.
- [llmwiki/runs/2026-05-01-nvidia2-abrupt-power-off-vllm-long-context.md](../../llmwiki/runs/2026-05-01-nvidia2-abrupt-power-off-vllm-long-context.md) —
  the GX10 platform crash this role helps investigate.
