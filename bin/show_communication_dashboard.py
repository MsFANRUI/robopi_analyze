#!/usr/bin/env python3
# Copyright (C) 2026 wentywenty
# SPDX-License-Identifier: GPL-3.0
"""Live terminal dashboard for the RoboPi seven-dimensional diagnostics."""

import argparse
import datetime as dt
import glob
import json
import os
import shutil
import subprocess
import sys
import time


SERVICES = (
    ("USB capture", "usbcan-capture.service"),
    ("HPM UART", "hpm-log-capture.service"),
)

DIMENSIONS = (
    ("BMS service", "bms-status.txt"),
    ("CAN details", None),
    ("dmesg", "dmesg-live.txt"),
    ("HPM ttyS4", "hpm-uart-live.txt"),
    ("USB pcap", "usbcan.pcap*"),
    ("CAN ASC", "can.asc"),
    ("inference screen", "inference-session.txt"),
)


def run(command):
    try:
        result = subprocess.run(
            command, check=False, capture_output=True, text=True, timeout=2
        )
    except (OSError, subprocess.TimeoutExpired):
        return "", 1
    return result.stdout.strip(), result.returncode


def service_states():
    states = []
    for label, service in SERVICES:
        output, status = run(["systemctl", "is-active", service])
        states.append((label, output or ("unknown" if status else "active")))
    return states


def can_status(interface):
    output, status = run(
        ["ip", "-j", "-details", "-statistics", "link", "show", "dev", interface]
    )
    if status or not output:
        return None
    try:
        link = json.loads(output)[0]
    except (IndexError, KeyError, TypeError, json.JSONDecodeError):
        return None

    info = link.get("linkinfo", {}).get("info_data", {})
    stats = link.get("stats64") or link.get("stats") or {}
    return {
        "operstate": link.get("operstate", "UNKNOWN"),
        "can_state": info.get("state", "UNKNOWN"),
        "rx": stats.get("rx", {}),
        "tx": stats.get("tx", {}),
    }


def rate(current, previous, elapsed, key):
    if not previous or elapsed <= 0:
        return "--"
    value = (current.get(key, 0) - previous.get(key, 0)) / elapsed
    return f"{max(0.0, value):.1f}/s"


def capture_status(directory):
    files = [path for path in glob.glob(os.path.join(directory, "usbcan.pcap*")) if os.path.isfile(path)]
    if not files:
        return 0, 0, None
    return len(files), sum(os.path.getsize(path) for path in files), max(os.path.getmtime(path) for path in files)


def human_bytes(value):
    for unit in ("B", "KiB", "MiB", "GiB"):
        if value < 1024 or unit == "GiB":
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GiB"


def hpm_log(lines):
    output, _ = run(
        [
            "journalctl", "-u", "hpm-log-capture.service", "-n", str(lines),
            "--no-pager", "-o", "cat",
        ]
    )
    return output.splitlines()[-lines:] if output else ["(no HPM UART log available)"]


def service_text(states):
    return "  ".join(f"{label}: {state.upper()}" for label, state in states)


def dimension_states(args, can):
    states = []
    sessions = sorted(glob.glob("/var/log/robopi/sixd-*"), key=os.path.getmtime, reverse=True)
    latest = sessions[0] if sessions else None
    for label, pattern in DIMENSIONS:
        if label == "CAN details":
            states.append((label, "LIVE" if can is not None else "MISSING"))
        elif label == "USB pcap":
            count, _, _ = capture_status(args.capture_dir)
            states.append((label, "READY" if count else "EMPTY"))
        elif latest and pattern:
            path = os.path.join(latest, pattern)
            states.append((label, "READY" if os.path.exists(path) and os.path.getsize(path) else "EMPTY"))
        else:
            states.append((label, "EMPTY"))
    return states


def render(args, current, previous, elapsed):
    width = max(72, min(shutil.get_terminal_size((100, 30)).columns, 140))
    line = "=" * width
    now = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    file_count, total_size, modified = capture_status(args.capture_dir)

    print(line)
    print(f" RoboPi Communication Status  {now}"[:width])
    print(line)
    print(service_text(service_states())[:width])
    print(" | ".join(f"{label}: {state}" for label, state in dimension_states(args, current))[:width])
    print("-" * width)

    if current is None:
        print(f"CAN {args.can}: unavailable")
    else:
        rx = current["rx"]
        tx = current["tx"]
        previous_rx = previous["rx"] if previous else None
        previous_tx = previous["tx"] if previous else None
        print(
            f"CAN {args.can}: link={current['operstate']}  "
            f"controller={current['can_state']}"
        )
        print(
            f"RX packets={rx.get('packets', 0):>12} ({rate(rx, previous_rx, elapsed, 'packets'):>10})  "
            f"bytes={human_bytes(rx.get('bytes', 0)):>11}  "
            f"errors={rx.get('errors', 0):>6}  dropped={rx.get('dropped', 0):>6}"
        )
        print(
            f"TX packets={tx.get('packets', 0):>12} ({rate(tx, previous_tx, elapsed, 'packets'):>10})  "
            f"bytes={human_bytes(tx.get('bytes', 0)):>11}  "
            f"errors={tx.get('errors', 0):>6}  dropped={tx.get('dropped', 0):>6}"
        )

    age = "--" if modified is None else f"{max(0, time.time() - modified):.1f}s ago"
    print("-" * width)
    print(
        f"Capture: {args.capture_dir}  files={file_count}  "
        f"size={human_bytes(total_size)}  newest={age}"[:width]
    )
    print("-" * width)
    print("Recent HPM UART log:")
    for entry in hpm_log(args.log_lines):
        print(f"  {entry}"[:width])
    print(line)
    print("Ctrl-C to exit | ERROR-ACTIVE and increasing RX/TX rates indicate traffic")


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--can", default="can3", help="SocketCAN interface (default: can3)")
    parser.add_argument("--capture-dir", default="/run/usbcan", help="ring capture directory")
    parser.add_argument("--interval", type=float, default=1.0, help="refresh interval in seconds")
    parser.add_argument("--log-lines", type=int, default=6, help="recent HPM journal lines")
    parser.add_argument("--once", action="store_true", help="render once without clearing")
    args = parser.parse_args()
    if args.interval <= 0 or args.log_lines < 1:
        parser.error("--interval must be positive and --log-lines must be at least 1")
    return args


def main():
    args = parse_args()
    previous = None
    previous_time = time.monotonic()
    try:
        while True:
            current_time = time.monotonic()
            current = can_status(args.can)
            if not args.once:
                print("\033[2J\033[H", end="")
            render(args, current, previous, current_time - previous_time)
            if args.once:
                return 0
            previous = current
            previous_time = current_time
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print()
        return 0


if __name__ == "__main__":
    sys.exit(main())
