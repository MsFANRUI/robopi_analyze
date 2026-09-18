#!/bin/bash
# Copyright (C) 2026 wentywenty
# SPDX-License-Identifier: GPL-3.0
# Sole implementation of CAN logging: candump capture plus ASC conversion.
set -euo pipefail

interfaces=(can0 can1 can2 can3)
first=${1:-}
input=
output=
capture_pid=
interrupted=no

die() { echo "robopi-can-capture: $*" >&2; exit 1; }
command -v candump >/dev/null 2>&1 || die "candump not found; install can-utils"
command -v log2asc >/dev/null 2>&1 || die "log2asc not found; install can-utils"

if [[ -d $first ]]; then
    input=$first/can.log
    output=$first/can.asc
elif [[ -n $first ]]; then
    output=$first
    input=$(mktemp --tmpdir robopi-can-capture.XXXXXX.log)
else
    output=${SESSION_ROOT:-/home/robo/robopi-logs}/can-$(date +%Y%m%d-%H%M%S).asc
    input=$(mktemp --tmpdir robopi-can-capture.XXXXXX.log)
fi

mkdir -p "$(dirname "$output")"
[[ ! -e $output || -d $first ]] || die "output already exists: $output"

if [[ -s $input && -d $first ]]; then
    log2asc -I "$input" -O "$output" "${interfaces[@]}"
    exit 0
fi

finish() {
    set +e
    [[ -n ${capture_pid:-} ]] && kill -INT "$capture_pid" 2>/dev/null
    wait 2>/dev/null
    if [[ -s $input ]]; then
        log2asc -I "$input" -O "$output" "${interfaces[@]}"
        echo "ASC written to $output"
    fi
    [[ -d $first ]] || rm -f "$input"
}
trap 'interrupted=yes; finish; exit 0' INT TERM
trap '[[ -d $first ]] || rm -f "$input"' EXIT

# Listen on "any" instead of naming interfaces: EtherCANFD's can0-can3 often
# appear well after boot, and "any" binds current and future interfaces, so
# late arrivals are captured with no wait/retry logic needed. Frames from
# the native buses (can_top/can_hipnuc/can_bottom) end up in can.log too,
# but log2asc below only converts the `interfaces` list, so they are
# filtered out and the ASC output matches listening on can0-can3 alone.
echo "Capturing all CAN interfaces (any); press Ctrl-C to write ASC: $output"
set +e
candump -L -t a any > "$input" & capture_pid=$!
wait "$capture_pid"
capture_status=$?
set -e
if [[ $capture_status -ne 0 && $interrupted != yes && $capture_status -ne 130 && $capture_status -ne 143 ]]; then
    die "candump failed with status $capture_status"
fi
finish
