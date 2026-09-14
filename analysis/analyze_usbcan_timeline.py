#!/usr/bin/env python3
# Copyright (C) 2026 wentywenty
# SPDX-License-Identifier: GPL-3.0
"""Build a time-bucketed timeline for the busiest usbmon bulk-IN endpoint."""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import math
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any


FIELDS = (
    "frame.time_epoch",
    "usb.bus_id",
    "usb.device_address",
    "usb.endpoint_address",
    "usb.urb_type",
    "usb.urb_status",
    "usb.data_len",
)


def number(value: str) -> int:
    return int(value, 0 if value.lower().startswith("0x") else 10)


def new_endpoint() -> dict[str, Any]:
    return {
        "records": 0,
        "submits": 0,
        "completes": 0,
        "successful_completes": 0,
        "errors": Counter(),
        "data_bytes": 0,
        "first_timestamp": None,
        "last_timestamp": None,
        "last_success_timestamp": None,
        "max_success_gap_s": 0.0,
        "max_gap_start": None,
        "max_gap_end": None,
        "buckets": {},
    }


def new_bucket() -> dict[str, int]:
    return {
        "records": 0,
        "submits": 0,
        "completes": 0,
        "successful_completes": 0,
        "errors": 0,
        "data_bytes": 0,
    }


def analyze(path: Path, interval: float) -> dict[str, Any]:
    command = [
        "tshark", "-n", "-r", str(path), "-Y", "usb.transfer_type == 3",
        "-T", "fields", "-E", "separator=/t", "-E", "quote=n",
    ]
    for field in FIELDS:
        command.extend(("-e", field))

    endpoints: dict[tuple[int, int, int], dict[str, Any]] = {}
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    assert process.stdout is not None
    for row in csv.reader(process.stdout, delimiter="\t"):
        if len(row) != len(FIELDS) or not all(row[:6]):
            continue
        timestamp = float(row[0])
        key = (number(row[1]), number(row[2]), number(row[3]))
        urb_type = row[4].strip("'\"")
        status = number(row[5])
        data_len = number(row[6]) if row[6] else 0
        stats = endpoints.setdefault(key, new_endpoint())
        bucket_time = math.floor(timestamp / interval) * interval
        bucket = stats["buckets"].setdefault(bucket_time, new_bucket())

        stats["records"] += 1
        bucket["records"] += 1
        stats["first_timestamp"] = (
            timestamp if stats["first_timestamp"] is None
            else stats["first_timestamp"]
        )
        stats["last_timestamp"] = timestamp

        if urb_type == "S":
            stats["submits"] += 1
            bucket["submits"] += 1
        elif urb_type in ("C", "E"):
            stats["completes"] += 1
            bucket["completes"] += 1
            stats["data_bytes"] += data_len
            bucket["data_bytes"] += data_len
            if status == 0:
                stats["successful_completes"] += 1
                bucket["successful_completes"] += 1
                previous = stats["last_success_timestamp"]
                if previous is not None and timestamp - previous > stats["max_success_gap_s"]:
                    stats["max_success_gap_s"] = timestamp - previous
                    stats["max_gap_start"] = previous
                    stats["max_gap_end"] = timestamp
                stats["last_success_timestamp"] = timestamp
            else:
                stats["errors"][status] += 1
                bucket["errors"] += 1

    stderr = process.stderr.read() if process.stderr is not None else ""
    return_code = process.wait()
    if return_code:
        raise RuntimeError(f"tshark failed ({return_code}): {stderr.strip()}")

    bulk_in = [
        (key, stats) for key, stats in endpoints.items() if key[2] & 0x80
    ]
    if not bulk_in:
        raise ValueError("no USB bulk-IN endpoint found")
    target_key, target = max(bulk_in, key=lambda item: item[1]["records"])
    bus, device, endpoint = target_key
    rows = []
    for timestamp, bucket in sorted(target["buckets"].items()):
        rows.append({
            "timestamp": f"{timestamp:.6f}",
            "time_utc": dt.datetime.fromtimestamp(
                timestamp, dt.timezone.utc
            ).isoformat(),
            **bucket,
        })

    return {
        "capture": str(path),
        "interval_s": interval,
        "target": {"bus": bus, "device": device, "endpoint": f"0x{endpoint:02x}"},
        "records": target["records"],
        "submits": target["submits"],
        "completes": target["completes"],
        "successful_completes": target["successful_completes"],
        "errors": {str(k): v for k, v in sorted(target["errors"].items())},
        "data_bytes": target["data_bytes"],
        "first_timestamp": target["first_timestamp"],
        "last_timestamp": target["last_timestamp"],
        "max_success_gap_s": target["max_success_gap_s"],
        "max_gap_start": target["max_gap_start"],
        "max_gap_end": target["max_gap_end"],
        "bulk_in_endpoints": [
            {
                "bus": key[0],
                "device": key[1],
                "endpoint": f"0x{key[2]:02x}",
                "records": stats["records"],
            }
            for key, stats in sorted(bulk_in)
        ],
        "timeline": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture", type=Path)
    parser.add_argument("--interval", type=float, default=1.0)
    parser.add_argument("--csv", type=Path, help="write the bucket timeline")
    parser.add_argument("--json", type=Path, help="write the complete report")
    args = parser.parse_args()
    if not args.capture.is_file():
        parser.error(f"capture does not exist: {args.capture}")
    if args.interval <= 0:
        parser.error("--interval must be positive")

    try:
        result = analyze(args.capture, args.interval)
    except (OSError, RuntimeError, ValueError) as error:
        parser.error(str(error))

    if args.csv:
        columns = (
            "timestamp", "time_utc", "records", "submits", "completes",
            "successful_completes", "errors", "data_bytes",
        )
        with args.csv.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=columns)
            writer.writeheader()
            writer.writerows(result["timeline"])
    if args.json:
        args.json.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")

    target = result["target"]
    print(
        f"target: bus {target['bus']} device {target['device']} "
        f"{target['endpoint']}"
    )
    print(
        f"records={result['records']:,} submits={result['submits']:,} "
        f"completes={result['completes']:,} "
        f"errors={sum(result['errors'].values()):,} "
        f"data={result['data_bytes']:,} bytes"
    )
    print(
        f"max successful-completion gap: {result['max_success_gap_s']:.6f} s"
    )
    if args.csv:
        print(f"timeline CSV: {args.csv}")
    if args.json:
        print(f"report JSON: {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
