#!/bin/sh
# Copyright (C) 2026 wentywenty
# SPDX-License-Identifier: GPL-3.0
# 清理过期的七维会话目录。
#
# 规则:
#   1. 超过 SESSION_RETENTION_DAYS(默认 7 天)的会话删除;
#   2. 保留最近 SESSION_RETENTION_COUNT(默认 10)个,更旧的删除;
#   3. 每个根目录下最新的一个会话永不删除——它可能是正在写的当前会话。
#
# 清理范围默认覆盖三处:新的主阵地 /home/robo/robopi-logs,旧位置
# /var/log/robopi(47MB zram,最容易写满),以及它的持久镜像
# /var/log.hdd/robopi(只清 zram 的话,重启后会被 armbian-ramlog 同步回来)。
#
# 由 robopi-seven-capture 在每次启动、建新会话之前调用;每删一个目录都往
# stderr(进入 journal)写一行,便于审计。--dry-run 只打印不删除。

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

    # 收集会话目录和排序时间:优先读 manifest 的 started_at_unix(最准),
    # 读不到(磁盘满时 manifest 可能是 0 字节)降级用目录 mtime。
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

    # 按时间从新到旧排序;第 1 行(最新)受保护,其余逐条套用两条规则。
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
