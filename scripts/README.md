# Scripts for DGX Spark / Ansible

## discover-sparks.sh

**Source:** [NVIDIA DGX Spark – Connect two Sparks](https://build.nvidia.com/spark/connect-two-sparks/stacked-sparks)  
Script: [NVIDIA/dgx-spark-playbooks – connect-two-sparks/assets/discover-sparks](https://github.com/NVIDIA/dgx-spark-playbooks/blob/main/nvidia/connect-two-sparks/assets/discover-sparks)

### What it does

- Discovers the other Spark(s) on the interconnect using **avahi-browse** (mDNS) on interfaces reported **Up** by `ibdev2netdev`.
- Generates a **shared SSH key** (`~/.ssh/id_ed25519_shared`) if missing.
- Distributes that key so **all discovered nodes** can SSH to each other without a password (bidirectional, same user on all nodes).

Use it when you rely on **link-local / automatic addressing** (e.g. netplan with `link-local: [ ipv4 ]`) and want discovery and SSH setup in one step from **one** of the Sparks, as a **non-root user**.

### When to use it

- **Preferred when:** You are on the Spark network (e.g. logged in as `casibbald` on nvidia1), have completed [Connect two Sparks](https://build.nvidia.com/spark/connect-two-sparks/stacked-sparks) (QSFP connected, netplan applied, interfaces Up), and **avahi** is available. Run it **once** from one node; you may be prompted for your password on the other node(s).
- **Alternative:** The **Ansible** playbook `playbooks/nccl_sparks.yml` also sets up passwordless SSH for the NCCL test user between the two hosts using inventory hostnames (nvidia1, nvidia2) and each host’s key; no avahi or discovery script needed.

### Prerequisites

- Same username on both systems (e.g. `casibbald`).
- **Not** run as root or with sudo (script exits with an error).
- **avahi-utils** installed on the node where you run the script (e.g. `sudo apt install avahi-utils`).
- QSFP connected and interfaces Up (`ibdev2netdev` shows at least one `(Up)` interface).
- You can log in with a password to the other node(s) at least once (so the script can `scp`/`ssh` and install the shared key).

### Usage

1. Install avahi-utils on the Spark from which you will run the script:
   ```bash
   sudo apt install -y avahi-utils
   ```
2. Copy the script to that Spark (or use the copy deployed by Ansible; see below).
3. As the **non-root user** (e.g. `casibbald`), from that Spark’s shell:
   ```bash
   bash ./discover-sparks
   ```
4. When prompted, enter your password for the other node(s). After it finishes, all listed nodes can SSH to each other without a password using the shared key.

### Integration with this repo

- The file `scripts/discover-sparks.sh` in the repo root is the NVIDIA script (Apache-2.0) for reference and optional deployment.
- The playbook **`playbooks/nccl_sparks.yml`** can deploy this script and install avahi-utils on the Sparks so you can run it manually from one node as **`nvidia`** (see repo `README.md` for the optional `discover_sparks` tag).
- The same playbook also performs **inventory-based** SSH key exchange for the NCCL test user; that does not use avahi or this script.
