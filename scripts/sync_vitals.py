#!/usr/bin/env python3
"""Sync Galaxy Fit3 vitals (heart rate + battery) into data/vitals.json via BLE GATT.

Reads the standard BLE SIG services the Fit3 exposes alongside its
proprietary Samsung protocol: Heart Rate Service (0x180D) and, if present,
Battery Service (0x180F). Pairing/BLE address discovery is the same one
used in the Galaxy Fit3 BLE RE project.

Run this on a machine near the watch (cron or a systemd --user timer work
fine, or wire it into systemd-hooks as a Hook). It just writes the JSON
file locally — committing/pushing data/vitals.json is left to that
scheduler, e.g.:

    python3 sync_vitals.py AA:BB:CC:DD:EE:FF && \
      git -C /path/to/portfolio add data/vitals.json && \
      git -C /path/to/portfolio commit -m "sync vitals" && \
      git -C /path/to/portfolio push

Usage:
    pip install bleak
    python3 sync_vitals.py AA:BB:CC:DD:EE:FF --out ../data/vitals.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("address", help="dirección BLE (MAC) del Galaxy Fit3")
    parser.add_argument("--out", default=Path("data/vitals.json"), type=Path)
    parser.add_argument("--timeout", default=15.0, type=float, help="segundos esperando una lectura de heart rate")
    args = parser.parse_args()

    vitals = asyncio.run(read_vitals(args.address, args.timeout))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(vitals, indent=2) + "\n")
    print(json.dumps(vitals, indent=2))


if __name__ == "__main__":
    main()
