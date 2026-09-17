#!/bin/bash
# Copyright (C) 2026 wentywenty
# SPDX-License-Identifier: GPL-3.0
# Sample every kernel thermal zone (CPU cores, GPU, NPU, package, center).
# Reads /sys/class/thermal directly instead of lm-sensors: sensors' virtual
# chips are backed by the same thermal zones anyway.
# Output: <unix timestamp> <zone name> <celsius>, one line per zone per tick.
set -euo pipefail
output=${1:?output file required}
interval=${2:-${THERMAL_INTERVAL:-5}}
while :; do
    ts=$(date +%s.%6N)
    for z in /sys/class/thermal/thermal_zone*; do
        [ -r "$z/temp" ] || continue
        name=$(cat "$z/type" 2>/dev/null) || continue
        milli=$(cat "$z/temp" 2>/dev/null) || continue
        awk -v t="$ts" -v n="$name" -v m="$milli" \
            'BEGIN { printf "%s %s %.3f\n", t, n, m / 1000 }' >> "$output"
    done
    sleep "$interval"
done
