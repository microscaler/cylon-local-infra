#!/usr/bin/python3
"""Toggle the Microscaler split-tunnel WireGuard peer on macOS."""

from __future__ import annotations

import argparse
import os
import subprocess
from collections.abc import Callable, Sequence


WG = "/opt/homebrew/bin/wg"
WG_QUICK = "/opt/homebrew/bin/wg-quick"
WIREGUARD_CONFIG = "/usr/local/etc/wireguard/ms02-away.conf"
LAN_ROUTE_PLIST = (
    "/Library/LaunchDaemons/com.microscaler.metallb-lan-route.plist"
)
LAN_ROUTE_LABEL = "com.microscaler.metallb-lan-route"
METALLB_CIDR = "10.177.76.0/24"
LAN_GATEWAY = "192.168.1.189"
COMMAND_PATH = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

RunCommand = Callable[..., subprocess.CompletedProcess[str]]


def execute(
    command: list[str],
    *,
    run: RunCommand,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["PATH"] = COMMAND_PATH
    result = run(
        command,
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )
    if check and result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "unknown error"
        raise RuntimeError(f"{' '.join(command)} failed: {detail}")
    return result


def assert_root(require_root: bool) -> None:
    if require_root and os.geteuid() != 0:
        raise PermissionError("run with sudo: sudo microscaler-wireguard <command>")


def restore_lan_route(*, run: RunCommand) -> None:
    execute(
        ["/bin/launchctl", "bootstrap", "system", LAN_ROUTE_PLIST],
        run=run,
        check=False,
    )
    route = execute(
        [
            "/sbin/route",
            "-n",
            "add",
            "-net",
            METALLB_CIDR,
            LAN_GATEWAY,
        ],
        run=run,
        check=False,
    )
    if route.returncode != 0:
        execute(
            [
                "/sbin/route",
                "-n",
                "change",
                "-net",
                METALLB_CIDR,
                LAN_GATEWAY,
            ],
            run=run,
            check=False,
        )
    execute(
        [
            "/bin/launchctl",
            "kickstart",
            "-k",
            f"system/{LAN_ROUTE_LABEL}",
        ],
        run=run,
        check=False,
    )


def status(*, run: RunCommand = subprocess.run) -> str:
    result = execute([WG, "show", "interfaces"], run=run, check=False)
    return result.stdout.strip() or "down"


def activate(
    *,
    run: RunCommand = subprocess.run,
    require_root: bool = True,
) -> str:
    assert_root(require_root)
    execute(
        ["/bin/launchctl", "bootout", "system", LAN_ROUTE_PLIST],
        run=run,
        check=False,
    )
    execute(
        [
            "/sbin/route",
            "-n",
            "delete",
            "-net",
            METALLB_CIDR,
            LAN_GATEWAY,
        ],
        run=run,
        check=False,
    )

    try:
        execute([WG_QUICK, "up", WIREGUARD_CONFIG], run=run)
    except RuntimeError:
        restore_lan_route(run=run)
        raise

    return status(run=run)


def deactivate(
    *,
    run: RunCommand = subprocess.run,
    require_root: bool = True,
) -> None:
    assert_root(require_root)
    execute(
        [WG_QUICK, "down", WIREGUARD_CONFIG],
        run=run,
        check=False,
    )
    restore_lan_route(run=run)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("up", "down", "status"))
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.command == "up":
        interface = activate()
        print(f"WireGuard active: {interface}")
    elif args.command == "down":
        deactivate()
        print("WireGuard down; LAN MetalLB route restored")
    else:
        print(status())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
