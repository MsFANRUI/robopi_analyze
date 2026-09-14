#!/usr/bin/env python3
# Copyright (C) 2026 wentywenty
# SPDX-License-Identifier: GPL-3.0
"""Correlate gs_usb TX requests with device echo frames in usbmon captures."""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any, Iterator


FIELDS = (
    "frame.number",
    "frame.time_epoch",
    "usb.bus_id",
    "usb.device_address",
    "usb.endpoint_address",
    "usb.urb_type",
    "usb.urb_status",
    "usb.capdata",
)
RX_ECHO_ID = 0xFFFFFFFF


def number(value: str) -> int:
    return int(value, 0 if value.lower().startswith("0x") else 10)


def host_frames(payload: bytes) -> Iterator[tuple[int, int]]:
    """Yield (channel, echo_id) from packed classic or CAN-FD host frames."""
    offset = 0
    while offset + 12 <= len(payload):
        echo_id = int.from_bytes(payload[offset:offset + 4], "little")
        channel = payload[offset + 9]
        flags = payload[offset + 10]
        stride = 76 if flags & 0x02 else 20
        if offset + stride > len(payload):
            return
        yield channel, echo_id
        offset += stride


def new_device() -> dict[str, Any]:
    return {
        "in_records": 0,
        "tx_requests": 0,
        "echoes": 0,
        "device_rx_frames": 0,
        "orphan_echoes": 0,
        "duplicate_tx": 0,
        "outstanding": {},
        "max_outstanding": 0,
        "max_outstanding_by_channel": Counter(),
        "outstanding_by_channel": Counter(),
        "echo_latency_sum_us": 0.0,
        "echo_latency_count": 0,
        "echo_latency_max_us": 0.0,
        "last_timestamp": None,
    }


def analyze(path: Path) -> dict[str, Any]:
    command = [
        "tshark", "-n", "-r", str(path),
        "-Y", "usb.transfer_type == 3",
        "-T", "fields", "-E", "separator=/t", "-E", "quote=n",
    ]
    for field in FIELDS:
        command.extend(("-e", field))

    devices: dict[tuple[int, int], dict[str, Any]] = {}
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    assert process.stdout is not None
    for row in csv.reader(process.stdout, delimiter="\t"):
        if len(row) != len(FIELDS) or not all(row[:7]):
            continue
        frame = number(row[0])
        timestamp = float(row[1])
        bus = number(row[2])
        device = number(row[3])
        endpoint = number(row[4])
        urb_type = row[5].strip("'\"")
        status = number(row[6])
        payload = bytes.fromhex(row[7]) if row[7] else b""
        stats = devices.setdefault((bus, device), new_device())
        stats["last_timestamp"] = timestamp

        if endpoint & 0x80:
            stats["in_records"] += 1
        if not payload:
            continue

        for channel, echo_id in host_frames(payload):
            key = (channel, echo_id)
            if endpoint & 0x80:
                if urb_type not in ("C", "E") or status != 0:
                    continue
                if echo_id == RX_ECHO_ID:
                    stats["device_rx_frames"] += 1
                    continue
                stats["echoes"] += 1
                submitted = stats["outstanding"].pop(key, None)
                if submitted is None:
                    stats["orphan_echoes"] += 1
                    continue
                stats["outstanding_by_channel"][channel] -= 1
                latency_us = (timestamp - submitted[1]) * 1_000_000
                stats["echo_latency_sum_us"] += latency_us
                stats["echo_latency_count"] += 1
                stats["echo_latency_max_us"] = max(
                    stats["echo_latency_max_us"], latency_us
                )
            else:
                if urb_type != "S" or echo_id == RX_ECHO_ID:
                    continue
                stats["tx_requests"] += 1
                if key in stats["outstanding"]:
                    stats["duplicate_tx"] += 1
                else:
                    stats["outstanding_by_channel"][channel] += 1
                stats["outstanding"][key] = (frame, timestamp)
                stats["max_outstanding"] = max(
                    stats["max_outstanding"], len(stats["outstanding"])
                )
                stats["max_outstanding_by_channel"][channel] = max(
                    stats["max_outstanding_by_channel"][channel],
                    stats["outstanding_by_channel"][channel],
                )

    stderr = process.stderr.read() if process.stderr is not None else ""
    return_code = process.wait()
    if return_code:
        raise RuntimeError(f"tshark failed ({return_code}): {stderr.strip()}")
    if not devices:
        raise ValueError("no USB bulk records found")

    target_key, target = max(devices.items(), key=lambda item: item[1]["in_records"])
    pending = [
        {
            "channel": key[0],
            "echo_id": key[1],
            "submit_frame": submitted[0],
            "submit_timestamp": submitted[1],
            "age_at_last_record_s": target["last_timestamp"] - submitted[1],
        }
        for key, submitted in sorted(target["outstanding"].items())
    ]
    return {
        "capture": str(path),
        "target": {"bus": target_key[0], "device": target_key[1]},
        "tx_requests": target["tx_requests"],
        "echoes": target["echoes"],
        "device_rx_frames": target["device_rx_frames"],
        "orphan_echoes": target["orphan_echoes"],
        "duplicate_tx": target["duplicate_tx"],
        "outstanding_at_end": len(pending),
        "outstanding": pending,
        "max_outstanding": target["max_outstanding"],
        "max_outstanding_by_channel": {
            str(key): value
            for key, value in sorted(target["max_outstanding_by_channel"].items())
        },
        "outstanding_at_end_by_channel": {
            str(key): value
            for key, value in sorted(target["outstanding_by_channel"].items())
            if value
        },
        "echo_latency_us": {
            "mean": (
                target["echo_latency_sum_us"] / target["echo_latency_count"]
                if target["echo_latency_count"] else None
            ),
            "max": target["echo_latency_max_us"] if target["echo_latency_count"] else None,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture", type=Path)
    parser.add_argument("--json", type=Path, help="write the complete JSON report")
    args = parser.parse_args()
    if not args.capture.is_file():
        parser.error(f"capture does not exist: {args.capture}")
    try:
        result = analyze(args.capture)
    except (OSError, RuntimeError, ValueError) as error:
        parser.error(str(error))
    if args.json:
        args.json.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")

    target = result["target"]
    print(f"target: bus {target['bus']} device {target['device']}")
    print(
        f"TX={result['tx_requests']:,} echo={result['echoes']:,} "
        f"outstanding={result['outstanding_at_end']:,} "
        f"orphan={result['orphan_echoes']:,} duplicate={result['duplicate_tx']:,}"
    )
    print(
        f"max outstanding: total={result['max_outstanding']:,} "
        f"by-channel={result['max_outstanding_by_channel']}"
    )
    if args.json:
        print(f"report JSON: {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
