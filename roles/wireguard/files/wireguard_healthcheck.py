#!/usr/bin/env python3
"""Repair a WireGuard interface when its required peer is missing or stale."""

from __future__ import annotations

import argparse
import subprocess
import time
from collections.abc import Callable, Sequence


RunCommand = Callable[..., subprocess.CompletedProcess[str]]


def latest_handshake_for_peer(output: str, peer_key: str) -> int | None:
    """Return the required peer's latest handshake timestamp."""
    for line in output.splitlines():
        fields = line.split()
        if len(fields) == 2 and fields[0] == peer_key:
            try:
                return int(fields[1])
            except ValueError:
                return None
    return None


def restart_unit(unit: str, run: RunCommand) -> None:
    result = run(
        ["systemctl", "restart", unit],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "unknown error"
        raise RuntimeError(f"failed to restart {unit}: {detail}")


def ensure_peer_health(
    *,
    interface: str,
    peer_key: str,
    max_age_seconds: int,
    unit: str,
    now: int | None = None,
    run: RunCommand = subprocess.run,
) -> bool:
    """Return True when the interface was restarted."""
    current_time = int(time.time()) if now is None else now
    result = run(
        ["wg", "show", interface, "latest-handshakes"],
        check=False,
        capture_output=True,
        text=True,
    )

    if result.returncode == 0:
        latest = latest_handshake_for_peer(result.stdout, peer_key)
        if latest is not None and latest > 0:
            age = current_time - latest
            if 0 <= age <= max_age_seconds:
                print(
                    f"{interface}: required peer handshake is healthy "
                    f"({age}s old)"
                )
                return False

    print(f"{interface}: required peer is missing or stale; restarting {unit}")
    restart_unit(unit, run)
    return True


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--interface", required=True)
    parser.add_argument("--peer-key", required=True)
    parser.add_argument("--max-age", type=int, required=True)
    parser.add_argument("--unit", required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    ensure_peer_health(
        interface=args.interface,
        peer_key=args.peer_key,
        max_age_seconds=args.max_age,
        unit=args.unit,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
