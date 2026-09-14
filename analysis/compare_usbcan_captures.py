#!/usr/bin/env python3
# Copyright (C) 2026 wentywenty
# SPDX-License-Identifier: GPL-3.0
"""Compare the busiest USB bulk-IN endpoint in two usbmon captures."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from analyze_usbcan_urb import analyze_capture, write_error_csv


def target_endpoint(result: dict[str, Any]) -> dict[str, Any]:
    key = result["target_bulk_in"]
    if not key:
        raise ValueError(f"no bulk-IN endpoint in {result['capture']}")
    return next(
        endpoint for endpoint in result["endpoints"]
        if all(endpoint[field] == key[field] for field in key)
    )


def comparison_markdown(results: list[dict[str, Any]]) -> str:
    endpoints = [target_endpoint(result) for result in results]
    labels = [Path(result["capture"]).name for result in results]
    lines = [
        "# USB-CAN capture comparison",
        "",
        "| Metric | " + " | ".join(labels) + " |",
        "|---|" + "|".join("---:" for _ in labels) + "|",
    ]

    def row(label: str, values: list[Any]) -> None:
        lines.append(f"| {label} | " + " | ".join(str(value) for value in values) + " |")

    row("Capture duration (s)", [f"{item['duration_s']:.6f}" for item in results])
    row("All packets", [f"{item['packets']:,}" for item in results])
    row("Target", [f"bus {item['bus']} dev {item['device']} {item['endpoint']}" for item in endpoints])
    row("Bulk-IN submits", [f"{item['submits']:,}" for item in endpoints])
    row("Bulk-IN completes", [f"{item['completes']:,}" for item in endpoints])
    row("Successful completes", [f"{item['successful_completes']:,}" for item in endpoints])
    row("Completion errors", [f"{sum(item['errors'].values()):,}" for item in endpoints])
    row("Error statuses", [json.dumps(item["errors"], sort_keys=True) for item in endpoints])
    row("Errors resubmitted", [f"{item['errors_resubmitted']:,}" for item in endpoints])
    row("Errors not resubmitted", [f"{item['errors_not_resubmitted']:,}" for item in endpoints])
    row("Successful completes after last error", [
        f"{item['successful_completes_after_last_error']:,}" for item in endpoints
    ])
    row("Activity after last error (s)", [
        (f"{item['activity_after_last_error_s']:.6f}"
         if item["activity_after_last_error_s"] is not None else "n/a")
        for item in endpoints
    ])
    row("Observed URB IDs", [f"{item['urb_ids']:,}" for item in endpoints])
    row("Maximum observed pending", [f"{item['max_observed_pending']:,}" for item in endpoints])
    row("Pending at capture end", [f"{item['pending_at_capture_end']:,}" for item in endpoints])
    row("Submit buffer lengths", [json.dumps(item["urb_lengths"], sort_keys=True) for item in endpoints])
    row("Records/s", [f"{item['records_per_second']:.2f}" for item in endpoints])
    row("Silence before capture end (s)", [f"{item['silence_to_capture_end_s']:.6f}" for item in endpoints])

    lines.extend(("", "## Interpretation", ""))
    before, after = endpoints
    before_errors = sum(before["errors"].values())
    after_errors = sum(after["errors"].values())
    if before_errors and before["errors_not_resubmitted"] == before_errors:
        lines.append(
            f"- `{labels[0]}` has {before_errors} failed bulk-IN completions and none of "
            "those URB IDs is submitted again in the observed capture."
        )
    if after_errors:
        lines.append(
            f"- `{labels[1]}` has {after_errors} failed bulk-IN completions; "
            f"{after['errors_resubmitted']} are followed by a submit of the same URB ID."
        )
    else:
        lines.append(f"- `{labels[1]}` has no failed bulk-IN completions.")
    if "512" in after["urb_lengths"]:
        lines.append(f"- `{labels[1]}` uses a 512-byte RX URB buffer.")
    else:
        lines.append(
            f"- `{labels[1]}` still requests {', '.join(after['urb_lengths'])}-byte RX URBs; "
            "a 512-byte buffer floor is not visible in this capture."
        )
    if after_errors and after["errors_resubmitted"] == after_errors:
        lines.append(
            "- The second capture directly exercises the recovery branch: bulk-IN traffic "
            f"continues after every observed error, including "
            f"{after['successful_completes_after_last_error']:,} successful completions "
            "after the final error."
        )
    elif not after_errors:
        lines.append(
            "- No error in the second capture means the resubmit branch was not exercised."
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("before", type=Path)
    parser.add_argument("after", type=Path)
    parser.add_argument("--output-dir", type=Path, default=Path("usbmon-results"))
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    results = []
    for capture in (args.before, args.after):
        result = analyze_capture(capture)
        stem = capture.stem
        (args.output_dir / f"{stem}.summary.json").write_text(
            json.dumps(result, indent=2) + "\n", encoding="utf-8"
        )
        write_error_csv(result, args.output_dir / f"{stem}.errors.csv")
        results.append(result)

    report = comparison_markdown(results)
    report_path = args.output_dir / "comparison.md"
    report_path.write_text(report, encoding="utf-8")
    print(report, end="")
    print(f"Reports: {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
