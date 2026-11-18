#!/bin/bash
# Copyright (C) 2026 wentywenty
# SPDX-License-Identifier: GPL-3.0
# Follow the exact screen session identity, including after a late start or restart.
set -euo pipefail
session=${INFERENCE_SCREEN_SESSION:-inference_session}
output=${1:?log output required}
interval=${INFERENCE_SCREEN_RETRY_INTERVAL:-30}
state_file=${2:-}
attached=
state=
report() {
    [[ $state != "$1" ]] || return 0
    state=$1
    if [[ -n $state_file ]]; then
        printf '%s\n' "$state" > "$state_file.tmp" && mv -f -- "$state_file.tmp" "$state_file"
    fi
}
cleanup() {
    if [[ -n $attached ]]; then screen -S "$attached" -X log off >/dev/null 2>&1 || true; fi
}
trap 'exit 0' TERM INT
trap cleanup EXIT
touch -- "$output"
if ! command -v screen >/dev/null 2>&1; then report screen_missing; exit 1; fi
while :; do
    listing=$(LC_ALL=C screen -list 2>/dev/null || true)
    id=$(awk -v name="$session" '$1 ~ /^[0-9]+[.]/ && substr($1,index($1,".")+1)==name && $0 !~ /Dead/ {print $1}' <<< "$listing")
    if [[ -z $id ]]; then
        attached=
        report no_visible_session
    elif [[ $id == *$'\n'* ]]; then
        report ambiguous_session
    elif [[ $id != "$attached" ]]; then
        cleanup
        attached=
        if screen -S "$id" -X logfile "$output" && screen -S "$id" -X log on; then
            attached=$id
            report "attached:$id"
        else
            report attach_failed
        fi
    fi
    sleep "$interval" & sleeper=$!
    trap 'kill "$sleeper" 2>/dev/null || true; exit 0' TERM INT
    wait "$sleeper" || true
    trap 'exit 0' TERM INT
done
