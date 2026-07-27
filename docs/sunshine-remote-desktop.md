# Sunshine/Moonlight remote desktop

ms02 runs Sunshine against a dedicated headless Xorg/XFCE desktop. This avoids
GDM autologin and does not require a monitor or HDMI dummy plug. The session is
2560×1440 at 60 Hz, captured through X11 and encoded by the Intel GPU through
VA-API.

Sunshine is reachable only from picolino's WireGuard address (`10.77.0.3/32`).
UFW does not expose its control, web, or media ports to the public internet or
the home LAN. Sunshine UPnP is disabled.

## Provision and inspect

From this repository:

```console
just sunshine-check
just sunshine-up
just sunshine-status
just sunshine-logs
```

The three system services start at boot and restart after failures:

- `sunshine-virtual-xorg.service` provides display `:10`.
- `sunshine-virtual-desktop.service` runs the XFCE session as `casibbald`.
- `sunshine-headless.service` captures and streams that session.

## First-time pairing

1. Activate the away-network route with `just wireguard-mac-up`.
2. Install the official Moonlight Qt macOS Universal DMG from
   [moonlight-stream.org](https://moonlight-stream.org/). The App Store build
   is for iPhone, iPad, and Apple TV; Steam Link is a separate, incompatible
   Valve product.
3. Open Sunshine's self-signed Web UI with `just sunshine-web`.
4. Create the initial Sunshine Web UI username and password.
5. In Moonlight, add host `10.77.0.2`.
6. Enter Moonlight's displayed PIN in Sunshine's **PIN** page.
7. Launch **Desktop**, then start Cursor inside the XFCE session.

The Web UI password and paired-client state are intentionally not stored in
Ansible or Git. Re-running the role preserves them.

## Recovery

If Moonlight cannot connect, first confirm the WireGuard route and then inspect
the Sunshine services:

```console
just wireguard-mac-status
just sunshine-status
just sunshine-logs
```

The Ansible role pins the official Debian Trixie package and verifies its
SHA-256 checksum. That build matches Ubuntu 25.10's glibc 2.42 and ICU 76;
the Ubuntu 24.04 build requires obsolete ICU 74, while the Ubuntu 26.04 build
requires newer glibc 2.43 and ICU 78.

References:

- [Sunshine getting started](https://docs.lizardbyte.dev/projects/sunshine/latest/md_docs_2getting__started.html)
- [Sunshine configuration](https://docs.lizardbyte.dev/projects/sunshine/latest/md_docs_2configuration.html)
