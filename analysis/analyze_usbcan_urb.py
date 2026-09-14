#!/usr/bin/env python3
# Copyright (C) 2026 wentywenty
# SPDX-License-Identifier: GPL-3.0
"""Stream usbmon records from a pcap/pcapng file and summarize URB health."""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any


FIELDS = (
    "frame.number",
    "frame.time_epoch",
    "usb.bus_id",
    "usb.device_address",
    "usb.endpoint_address",
    "usb.transfer_type",
    "usb.urb_type",
    "usb.urb_id",
    "usb.urb_status",
    "usb.urb_len",
    "usb.data_len",
)


def _number(value: str, base: int = 10) -> int:
    return int(value, 0 if value.lower().startswith("0x") else base)


def _new_endpoint() -> dict[str, Any]:
    return {
        "records": 0,
        "submits": 0,
        "completes": 0,
        "errors": Counter(),
        "urb_lengths": Counter(),
        "data_lengths": Counter(),
        "data_bytes": 0,
        "urb_ids": set(),
        "pending": set(),
        "max_observed_pending": 0,
        "orphan_completions": 0,
        "first_timestamp": None,
        "last_timestamp": None,
        "last_success_timestamp": None,
        "last_error_timestamp": None,
        "successful_completes_after_last_error": 0,
    }


def _capture_metadata(path: Path) -> dict[str, Any]:
    process = subprocess.run(
        ["capinfos", "-Tm", str(path)],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    rows = list(csv.DictReader(process.stdout.splitlines()))
    if len(rows) != 1:
        raise ValueError(f"could not read capture metadata: {path}")
    row = rows[0]

    def epoch(field: str) -> float:
        value = dt.datetime.strptime(row[field], "%Y-%m-%d %H:%M:%S.%f")
        return value.astimezone().timestamp()

    return {
        "packets": int(row["Number of packets"]),
        "duration_s": float(row["Capture duration (seconds)"]),
        "first_timestamp": epoch("Start time"),
        "last_timestamp": epoch("End time"),
    }


def analyze_capture(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ValueError(f"capture does not exist: {path}")

    command = [
        "tshark", "-n", "-r", str(path),
        "-Y", "usb.transfer_type == 3",
        "-T", "fields", "-E", "separator=/t", "-E", "quote=n",
    ]
    for field in FIELDS:
        command.extend(("-e", field))

    endpoints: dict[tuple[int, int, int], dict[str, Any]] = {}
    errors: list[dict[str, Any]] = []
    waiting_errors: dict[tuple[int, int, int, str], list[int]] = {}
    first_timestamp = last_timestamp = None
    bulk_records = 0

    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    assert process.stdout is not None
    reader = csv.reader(process.stdout, delimiter="\t")
    for row in reader:
        if len(row) != len(FIELDS) or not all(row[index] for index in range(9)):
            continue
        frame = _number(row[0])
        timestamp = float(row[1])
        bus = _number(row[2])
        device = _number(row[3])
        endpoint = _number(row[4])
        urb_type = row[6].strip("'\"")
        urb_id = row[7]
        status = _number(row[8])
        urb_len = _number(row[9]) if row[9] else 0
        data_len = _number(row[10]) if row[10] else 0

        bulk_records += 1
        first_timestamp = timestamp if first_timestamp is None else first_timestamp
        last_timestamp = timestamp
        key = (bus, device, endpoint)
        stats = endpoints.setdefault(key, _new_endpoint())
        stats["records"] += 1
        stats["urb_ids"].add(urb_id)
        stats["first_timestamp"] = (
            timestamp if stats["first_timestamp"] is None else stats["first_timestamp"]
        )
        stats["last_timestamp"] = timestamp

        urb_key = (bus, device, endpoint, urb_id)
        if urb_type == "S":
            for error_index in waiting_errors.pop(urb_key, []):
                error = errors[error_index]
                error["resubmitted"] = True
                error["resubmit_frame"] = frame
                error["resubmit_delay_us"] = round(
                    (timestamp - error["timestamp"]) * 1_000_000, 3
                )
            stats["submits"] += 1
            stats["urb_lengths"][urb_len] += 1
            stats["pending"].add(urb_id)
            stats["max_observed_pending"] = max(
                stats["max_observed_pending"], len(stats["pending"])
            )
        elif urb_type in ("C", "E"):
            stats["completes"] += 1
            stats["data_lengths"][data_len] += 1
            stats["data_bytes"] += data_len
            if urb_id in stats["pending"]:
                stats["pending"].remove(urb_id)
            else:
                stats["orphan_completions"] += 1
            if status == 0:
                stats["last_success_timestamp"] = timestamp
                if stats["last_error_timestamp"] is not None:
                    stats["successful_completes_after_last_error"] += 1
            else:
                stats["errors"][status] += 1
                stats["last_error_timestamp"] = timestamp
                stats["successful_completes_after_last_error"] = 0
                errors.append({
                    "frame": frame,
                    "timestamp": timestamp,
                    "bus": bus,
                    "device": device,
                    "endpoint": f"0x{endpoint:02x}",
                    "urb_type": urb_type,
                    "urb_id": urb_id,
                    "status": status,
                    "urb_len": urb_len,
                    "data_len": data_len,
                    "resubmitted": False,
                    "resubmit_frame": None,
                    "resubmit_delay_us": None,
                })
                waiting_errors.setdefault(urb_key, []).append(len(errors) - 1)

    stdout_error = process.stderr.read() if process.stderr is not None else ""
    return_code = process.wait()
    if return_code:
        raise RuntimeError(f"tshark failed ({return_code}): {stdout_error.strip()}")

    metadata = _capture_metadata(path)
    serialized_endpoints = []
    for (bus, device, endpoint), stats in sorted(endpoints.items()):
        endpoint_errors = [
            error for error in errors
            if error["bus"] == bus
            and error["device"] == device
            and _number(error["endpoint"]) == endpoint
        ]
        duration = stats["last_timestamp"] - stats["first_timestamp"]
        serialized_endpoints.append({
            "bus": bus,
            "device": device,
            "endpoint": f"0x{endpoint:02x}",
            "direction": "IN" if endpoint & 0x80 else "OUT",
            "records": stats["records"],
            "submits": stats["submits"],
            "completes": stats["completes"],
            "successful_completes": stats["completes"] - sum(stats["errors"].values()),
            "errors": {str(k): v for k, v in sorted(stats["errors"].items())},
            "errors_resubmitted": sum(error["resubmitted"] for error in endpoint_errors),
            "errors_not_resubmitted": sum(not error["resubmitted"] for error in endpoint_errors),
            "successful_completes_after_last_error": stats["successful_completes_after_last_error"],
            "activity_after_last_error_s": (
                stats["last_timestamp"] - stats["last_error_timestamp"]
                if stats["last_error_timestamp"] is not None else None
            ),
            "urb_ids": len(stats["urb_ids"]),
            "max_observed_pending": stats["max_observed_pending"],
            "pending_at_capture_end": len(stats["pending"]),
            "orphan_completions": stats["orphan_completions"],
            "urb_lengths": {str(k): v for k, v in sorted(stats["urb_lengths"].items())},
            "data_lengths": {str(k): v for k, v in sorted(stats["data_lengths"].items())},
            "data_bytes": stats["data_bytes"],
            "first_timestamp": stats["first_timestamp"],
            "last_timestamp": stats["last_timestamp"],
            "duration_s": duration,
            "records_per_second": stats["records"] / duration if duration > 0 else 0,
            "silence_to_capture_end_s": (
                metadata["last_timestamp"] - stats["last_timestamp"]
            ),
        })

    target = None
    in_endpoints = [item for item in serialized_endpoints if item["direction"] == "IN"]
    if in_endpoints:
        target = max(in_endpoints, key=lambda item: item["records"])

    return {
        "capture": str(path),
        "file_size": path.stat().st_size,
        "packets": metadata["packets"],
        "bulk_records": bulk_records,
        "first_timestamp": metadata["first_timestamp"],
        "last_timestamp": metadata["last_timestamp"],
        "duration_s": metadata["duration_s"],
        "bulk_first_timestamp": first_timestamp,
        "bulk_last_timestamp": last_timestamp,
        "bulk_duration_s": (
            last_timestamp - first_timestamp
            if first_timestamp is not None and last_timestamp is not None else 0
        ),
        "target_bulk_in": (
            {"bus": target["bus"], "device": target["device"], "endpoint": target["endpoint"]}
            if target else None
        ),
        "endpoints": serialized_endpoints,
        "errors": errors,
    }


def write_error_csv(result: dict[str, Any], output: Path) -> None:
    columns = (
        "frame", "timestamp", "bus", "device", "endpoint", "urb_type",
        "urb_id", "status", "urb_len", "data_len", "resubmitted",
        "resubmit_frame", "resubmit_delay_us",
    )
    with output.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        writer.writerows(result["errors"])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture", type=Path)
    parser.add_argument("--json", type=Path, help="write the complete JSON report")
    parser.add_argument("--errors-csv", type=Path, help="write non-zero completion events")
    args = parser.parse_args()
    try:
        result = analyze_capture(args.capture)
        if args.json:
            args.json.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        if args.errors_csv:
            write_error_csv(result, args.errors_csv)
    except (OSError, RuntimeError, ValueError) as error:
        parser.error(str(error))

    target_key = result["target_bulk_in"]
    target = next(
        (item for item in result["endpoints"]
         if target_key and all(item[key] == target_key[key] for key in target_key)),
        None,
    )
    print(f"capture: {result['capture']}")
    print(
        f"packets: {result['packets']:,}; bulk records: {result['bulk_records']:,}; "
        f"duration: {result['duration_s']:.6f} s"
    )
    if target:
        print(
            f"target: bus {target['bus']} device {target['device']} {target['endpoint']} "
            f"S={target['submits']:,} C={target['completes']:,} "
            f"errors={sum(target['errors'].values()):,} "
            f"resubmitted={target['errors_resubmitted']:,} "
            f"retired={target['errors_not_resubmitted']:,} "
            f"end-silence={target['silence_to_capture_end_s']:.6f} s"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
