#!/bin/sh
# Copyright (C) 2026 wentywenty
# SPDX-License-Identifier: GPL-3.0
# Clean up expired seven-dimension session directories.
#
# Rules:
#   1. Remove sessions older than SESSION_RETENTION_DAYS (default 7);
#   2. Keep the most recent SESSION_RETENTION_COUNT (default 10), remove
#      anything older;
#   3. The newest session per root is never removed — it may still be
#      the one being written.
#
# Default scope covers three roots: the current primary location
# /home/robo/robopi-logs, the old /var/log/robopi (47MB zram, easiest
# to fill up), and its persistent mirror /var/log.hdd/robopi (cleaning
# only the zram copy would have armbian-ramlog sync the old ones back
# after a reboot).
#
# Called by robopi-seven-capture before each new session; every removal
# is logged to stderr (journal) for audit. --dry-run prints without deleting.

set -eu

RETENTION_DAYS=${SESSION_RETENTION_DAYS:-7}
RETENTION_COUNT=${SESSION_RETENTION_COUNT:-10}
ROOTS=${SESSION_CLEANUP_ROOTS:-/home/robo/robopi-logs /var/log/robopi /var/log.hdd/robopi}
DRY_RUN=no
[ "${1:-}" = "--dry-run" ] && DRY_RUN=yes

log() { echo "robopi-session-cleanup: $*" >&2; }

remove()
{
    if [ "$DRY_RUN" = yes ]; then
        log "dry-run: would remove $1"
    else
        rm -rf -- "$1"
        log "removed $1"
    fi
}

for root in $ROOTS; do
    [ -d "$root" ] || continue

    # Collect each session's timestamp: prefer manifest's started_at_unix,
    # fall back to directory mtime (manifest can be 0 bytes when the disk
    # was full).
    list=$(mktemp)
    for dir in "$root"/seven-*; do
        [ -d "$dir" ] || continue
        stamp=$(sed -n 's/^  "started_at_unix": \([0-9][0-9.]*\),$/\1/p' \
                "$dir/manifest.json" 2>/dev/null | head -n 1)
        stamp=${stamp%%.*}
        [ -n "$stamp" ] || stamp=$(stat -c %Y "$dir" 2>/dev/null || echo 0)
        echo "$stamp $dir" >> "$list"
    done

    if [ ! -s "$list" ]; then
        rm -f "$list"
        continue
    fi

    # Sort newest first; row 1 is protected, the rest go through the two rules.
    sort -rn "$list" > "$list.sorted"
    cutoff=$(( $(date +%s) - RETENTION_DAYS * 86400 ))
    index=0
    while read -r stamp dir; do
        index=$((index + 1))
        [ "$index" -gt 1 ] || continue
        if [ "$stamp" -lt "$cutoff" ] || [ "$index" -gt "$RETENTION_COUNT" ]; then
            remove "$dir"
        fi
    done < "$list.sorted"

    rm -f "$list" "$list.sorted"
done
