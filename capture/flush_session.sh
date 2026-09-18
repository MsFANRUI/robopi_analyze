#!/bin/sh
# Copyright (C) 2026 wentywenty
# SPDX-License-Identifier: GPL-3.0
# Periodically flush can.asc and the ring pcap into the session directory so
# it stays usable mid-capture instead of only at teardown:
#
#   can.asc      — track the last converted byte offset in can.log, feed only
#                  the new bytes to log2asc and append. Cost stays constant
#                  regardless of file size.
#   usbcan.pcap* — each ring file is bounded and gets truncated by tcpdump
#                  on wraparound, so there is no meaningful "since session
#                  start" delta to accumulate. Whenever a ring file's mtime
#                  changes, its current full content replaces the session's
#                  copy (same as teardown's 05_usb_pcap.sh, just more often).
#
# Teardown still performs the authoritative full log2asc conversion and pcap
# copy, which overwrite these incremental results.
#
# Usage: flush_session.sh <session dir> <ring dir> [interval seconds]

set -eu

session=${1:?session directory required}
ring=${2:?ring directory required}
interval=${3:-10}
can_log=$session/can.log
can_asc=$session/can.asc
chunk=$session/.flush-chunk.log
chunk_asc=$session/.flush-chunk.asc
offset=0

log() { echo "robopi-session-flush: $*" >&2; }

log "flushing can.asc and pcap every ${interval}s into $session"

while :; do
    sleep "$interval"

    # ---- incremental can.asc conversion ----
    if [ -f "$can_log" ] && [ -s "$can_log" ]; then
        size=$(stat -c %s "$can_log")
        if [ "$size" -gt "$offset" ]; then
            tail -c +"$((offset + 1))" "$can_log" > "$chunk"
            # drop a possibly-incomplete trailing line, retry next tick
            last=$(tail -c 1 "$chunk" | od -An -tx1 | tr -d ' \n')
            if [ "$last" != "0a" ] && [ -s "$chunk" ]; then
                head -n -1 "$chunk" > "$chunk.part" && mv "$chunk.part" "$chunk"
            fi
            if [ -s "$chunk" ]; then
                if log2asc -I "$chunk" -O "$chunk_asc" can0 can1 can2 can3 >/dev/null 2>&1; then
                    if [ "$offset" -eq 0 ]; then
                        cat "$chunk_asc" >> "$can_asc"
                    else
                        tail -n +4 "$chunk_asc" >> "$can_asc"
                    fi
                    offset=$((offset + $(wc -c < "$chunk")))
                fi
            fi
            rm -f "$chunk" "$chunk_asc"
        fi
    fi

    # ---- ring pcap mirror ----
    for f in "$ring"/usbcan.pcap*; do
        [ -f "$f" ] || continue
        name=${f##*/}
        idx=${name#usbcan.pcap}
        case $idx in
            ''|*[!0-9]*) continue ;;
        esac
        mtime=$(stat -c %Y "$f" 2>/dev/null) || continue
        eval "last_mtime=\${ring_mtime_$idx:-0}"
        if [ "$mtime" != "$last_mtime" ]; then
            cp -f "$f" "$session/$name.tmp" 2>/dev/null && mv -f "$session/$name.tmp" "$session/$name"
            eval "ring_mtime_$idx=$mtime"
        fi
    done
done
