#!/usr/bin/env python3
"""Sync Galaxy Fit3 vitals (heart rate + battery) to the portfolio's `data`
branch via BLE GATT, so /status can fetch them straight from
raw.githubusercontent.com — no GitHub Pages rebuild, no history bloat on
main (the site's own principle: no versionar datos, solo manifiestos).

Reads the standard BLE SIG services the Fit3 exposes alongside its
proprietary Samsung protocol: Heart Rate Service (0x180D) and, if present,
Battery Service (0x180F). Pairing/BLE address discovery is the same one
used in the Galaxy Fit3 BLE RE project.

One-time setup, from your portfolio checkout:
    git worktree add ../portfolio-data data
    pip install bleak

Then run this periodically (cron, or a systemd --user timer) on a machine
near the watch — wiring it into systemd-hooks as a Hook works too:

    python3 sync_vitals.py AA:BB:CC:DD:EE:FF --worktree ../portfolio-data

Each run overwrites vitals.json in that worktree and force-pushes a single
amended commit to `data` — the branch never grows, it's just a pointer to
the latest reading (same pattern as a gh-pages deploy).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from bleak import BleakClient

HEART_RATE_MEASUREMENT = "00002a37-0000-1000-8000-00805f9b34fb"
BATTERY_LEVEL = "00002a19-0000-1000-8000-00805f9b34fb"


async def read_vitals(address: str, timeout: float) -> dict:
    heart_rate: int | None = None
    got_reading = asyncio.Event()

    def on_hr(_, data: bytearray) -> None:
        nonlocal heart_rate
        flags = data[0]
        heart_rate = int.from_bytes(data[1:3], "little") if flags & 0x01 else data[1]
        got_reading.set()

    async with BleakClient(address) as client:
        await client.start_notify(HEART_RATE_MEASUREMENT, on_hr)
        try:
            await asyncio.wait_for(got_reading.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            pass
        await client.stop_notify(HEART_RATE_MEASUREMENT)

        try:
            battery_raw = await client.read_gatt_char(BATTERY_LEVEL)
            battery = battery_raw[0]
        except Exception:
            battery = None

    return {
        "heart_rate_bpm": heart_rate,
        "battery_pct": battery,
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "galaxy-fit3-ble",
    }


def publish(vitals: dict, worktree: Path, push: bool) -> None:
    if not (worktree / ".git").exists():
        raise SystemExit(
            f"{worktree} no es un worktree de git. Primero: "
            f"git worktree add {worktree} data"
        )
    (worktree / "vitals.json").write_text(json.dumps(vitals, indent=2) + "\n")

    def git(*args: str) -> None:
        subprocess.run(["git", "-C", str(worktree), *args], check=True)

    git("add", "vitals.json")
    git("commit", "--amend", "-m", f"vitals @ {vitals['updated_at']}")
    if push:
        git("push", "--force", "origin", "data")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("address", help="dirección BLE (MAC) del Galaxy Fit3")
    parser.add_argument("--worktree", default=Path("../portfolio-data"), type=Path, help="worktree local de la rama `data`")
    parser.add_argument("--timeout", default=15.0, type=float, help="segundos esperando una lectura de heart rate")
    parser.add_argument("--no-push", action="store_true", help="solo escribe/commitea local, no hace push")
    args = parser.parse_args()

    vitals = asyncio.run(read_vitals(args.address, args.timeout))
    publish(vitals, args.worktree, push=not args.no_push)
    print(json.dumps(vitals, indent=2))


if __name__ == "__main__":
    main()
