#!/usr/bin/env python3
# Copyright (C) 2026 wentywenty
# SPDX-License-Identifier: GPL-3.0
"""Extract small pcapng windows around usbmon errors listed in a CSV file."""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import subprocess
from pathlib import Path


def editcap_time(timestamp: float) -> str:
    return (
        dt.datetime.fromtimestamp(timestamp, dt.timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture", type=Path)
    parser.add_argument("errors_csv", type=Path)
    parser.add_argument("--before", type=float, default=2.0)
    parser.add_argument("--after", type=float, default=5.0)
    parser.add_argument("--limit", type=int, help="extract only the first N errors")
    parser.add_argument("--output-dir", type=Path, default=Path("error-windows"))
    args = parser.parse_args()

    if not args.capture.is_file():
        parser.error(f"capture does not exist: {args.capture}")
    if not args.errors_csv.is_file():
        parser.error(f"errors CSV does not exist: {args.errors_csv}")
    if args.before < 0 or args.after < 0:
        parser.error("--before and --after must be non-negative")
    if args.limit is not None and args.limit <= 0:
        parser.error("--limit must be positive")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    index_path = args.output_dir / "windows.csv"
    columns = (
        "index", "source_frame", "status", "timestamp", "window_start",
        "window_end", "output",
    )

    with args.errors_csv.open(newline="", encoding="utf-8") as source:
        errors = list(csv.DictReader(source))
    if errors and not {"frame", "timestamp", "status"} <= set(errors[0]):
        parser.error("errors CSV must contain frame, timestamp and status columns")
    if args.limit is not None:
        errors = errors[:args.limit]

    rows = []
    for index, error in enumerate(errors, 1):
        try:
            timestamp = float(error["timestamp"])
        except (KeyError, ValueError) as exc:
            parser.error(f"invalid timestamp in CSV row {index}: {exc}")
        start = timestamp - args.before
        end = timestamp + args.after
        frame = error.get("frame", "unknown")
        status = error.get("status", "unknown")
        output = args.output_dir / (
            f"error-{index:03d}-frame-{frame}-status-{status}.pcapng"
        )
        subprocess.run(
            [
                "editcap", "-A", editcap_time(start), "-B", editcap_time(end),
                str(args.capture), str(output),
            ],
            check=True,
        )
        rows.append({
            "index": index,
            "source_frame": frame,
            "status": status,
            "timestamp": f"{timestamp:.6f}",
            "window_start": f"{start:.6f}",
            "window_end": f"{end:.6f}",
            "output": output.name,
        })
        print(f"{output}: {args.before:g}s before, {args.after:g}s after")

    with index_path.open("w", newline="", encoding="utf-8") as destination:
        writer = csv.DictWriter(destination, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Extracted {len(rows)} window(s); index: {index_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
