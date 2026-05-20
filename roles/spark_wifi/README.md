# spark_wifi — disable Wi-Fi on DGX Sparks

Sparks use **Ethernet** (`enP7s7`, home LAN) for operator SSH and `:8000` API access.
QSFP interconnect carries Ray/NCCL traffic. Leaving Wi-Fi enabled can introduce a
second default route and duplicate LAN IPs — vLLM's Ray executor then fails with
*"Every node should have a unique IP address"* ([vllm#43095](https://github.com/vllm-project/vllm/pull/43095)).

## What it does

1. Removes the legacy `/etc/NetworkManager/conf.d/99-cylon-wifi-disabled.conf` drop-in
   (`wifi.enabled=false` is an **unknown key** on NetworkManager 1.46 / DGX Spark).
2. Installs `/etc/systemd/system/cylon-wifi-radio-off.service` — oneshot after
   `NetworkManager.service` that runs `nmcli radio wifi off` on every boot.
3. Disables autoconnect on saved Wi-Fi profiles, runs `nmcli radio wifi off` now,
   disconnects active Wi-Fi, and **fails** if the radio is still enabled.

Runs as the **last network mutation** in `spark_provision`, immediately before
`spark_assert`.

## Enable

Wired into `playbooks/provision_sparks.yml` via `roles/spark_provision` (tag `spark_wifi`).
Default on: `spark_wifi_disable: true` in `defaults/main.yml`.

```bash
ansible-playbook playbooks/provision_sparks.yml -l sparks --tags spark_wifi
```

## Disable (e.g. temporary Wi-Fi bring-up)

```bash
ansible-playbook playbooks/provision_sparks.yml -l sparks --tags spark_wifi \
  -e spark_wifi_disable=false
```

Disable/remove the systemd unit manually if you turned the role off after it was applied.
