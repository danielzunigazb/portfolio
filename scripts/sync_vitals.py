#!/usr/bin/env python3
"""Sync Galaxy Fit3 vitals (heart rate + battery) to the portfolio's `data`
branch, so /status can fetch them straight from raw.githubusercontent.com.

Reads the standard BLE SIG services the Fit3 exposes alongside its
proprietary Samsung protocol: Heart Rate Service (0x180D) and, if present,
Battery Service (0x180F). Publishing goes through GitHub's Contents API
directly (one authenticated PUT) instead of a local git checkout — the
same endpoint also works from a phone automation (Tasker/Shortcuts) if
that ends up being the actual data source for a given metric, so this
script and that path stay interchangeable.

Setup:
    pip install bleak requests
    export GITHUB_TOKEN=<fine-grained PAT, Contents: read/write, this repo only>

Run periodically (cron / systemd --user timer) on a machine near the watch:
    python3 sync_vitals.py AA:BB:CC:DD:EE:FF
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
from datetime import datetime, timezone

import requests
from bleak import BleakClient

HEART_RATE_MEASUREMENT = "00002a37-0000-1000-8000-00805f9b34fb"
BATTERY_LEVEL = "00002a19-0000-1000-8000-00805f9b34fb"

REPO = "danielzunigazb/portfolio"
BRANCH = "data"
FILE_PATH = "vitals.json"
API_URL = f"https://api.github.com/repos/{REPO}/contents/{FILE_PATH}"


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


def publish(vitals: dict, token: str) -> None:
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    current = requests.get(API_URL, headers=headers, params={"ref": BRANCH}, timeout=10)
    sha = current.json()["sha"] if current.status_code == 200 else None

    body = {
        "message": f"vitals @ {vitals['updated_at']}",
        "content": base64.b64encode((json.dumps(vitals, indent=2) + "\n").encode()).decode(),
        "branch": BRANCH,
    }
    if sha:
        body["sha"] = sha

    resp = requests.put(API_URL, headers=headers, json=body, timeout=10)
    resp.raise_for_status()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("address", help="dirección BLE (MAC) del Galaxy Fit3")
    parser.add_argument("--timeout", default=15.0, type=float, help="segundos esperando una lectura de heart rate")
    parser.add_argument("--dry-run", action="store_true", help="solo lee e imprime, no publica")
    args = parser.parse_args()

    vitals = asyncio.run(read_vitals(args.address, args.timeout))
    print(json.dumps(vitals, indent=2))

    if args.dry_run:
        return

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise SystemExit("Falta GITHUB_TOKEN en el entorno (PAT con Contents: read/write sobre este repo)")
    publish(vitals, token)


if __name__ == "__main__":
    main()
