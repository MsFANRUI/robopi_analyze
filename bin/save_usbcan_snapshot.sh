#!/bin/sh
# Copyright (C) 2026 wentywenty
# SPDX-License-Identifier: GPL-3.0
# 暂停 USB-CAN 循环抓包，保存 PCAP、USB/CAN/HPM/内核日志后恢复服务状态。
set -eu

service=${USBCAN_CAPTURE_SERVICE:-usbcan-capture.service}
hpm_log_service=${HPM_LOG_CAPTURE_SERVICE:-hpm-log-capture.service}
source_dir=${USBCAN_CAPTURE_DIR:-/run/usbcan}
snapshot_root=${USBCAN_SNAPSHOT_DIR:-/var/lib/robopi/usbcan-snapshots}
lock_file=${USBCAN_SNAPSHOT_LOCK:-/run/lock/usbcan-debug-snapshot.lock}
timestamp=$(date +%Y%m%d-%H%M%S)
destination=$snapshot_root/$timestamp
was_active=no
destination_created=no
snapshot_complete=no

if [ "${USBCAN_ALLOW_NON_ROOT:-no}" != yes ] && [ "$(id -u)" -ne 0 ]; then
    echo "usbcan-debug-snapshot: run as root" >&2
    exit 1
fi

exec 9>"$lock_file"
flock -n 9 || {
    echo "usbcan-debug-snapshot: another snapshot is running" >&2
    exit 1
}

cleanup()
{
    if [ "$destination_created" = yes ] && [ "$snapshot_complete" != yes ]; then
        rm -rf -- "$destination"
    fi
    if [ "$was_active" = yes ]; then
        systemctl start "$service" || true
    fi
}
trap cleanup EXIT
trap 'exit 1' HUP INT TERM

[ -d "$source_dir" ] || {
    echo "usbcan-debug-snapshot: capture directory not found: $source_dir" >&2
    exit 1
}
set -- "$source_dir"/usbcan.pcap*
[ -e "$1" ] || {
    echo "usbcan-debug-snapshot: no usbcan.pcap files in $source_dir" >&2
    exit 1
}

[ -d "$snapshot_root" ] || install -d -m 0750 "$snapshot_root"
[ ! -e "$destination" ] || {
    echo "usbcan-debug-snapshot: destination already exists: $destination" >&2
    exit 1
}

# Refuse the snapshot before pausing capture if the persistent filesystem
# cannot hold all current ring files plus a small allowance for diagnostics.
source_kb=$(du -sk "$source_dir"/usbcan.pcap* | awk '{ total += $1 } END { print total + 0 }')
available_kb=$(df -Pk "$snapshot_root" | awk 'NR == 2 { print $4 }')
required_kb=$((source_kb + 1024))
[ -n "$available_kb" ] && [ "$available_kb" -ge "$required_kb" ] || {
    echo "usbcan-debug-snapshot: insufficient space in $snapshot_root" >&2
    echo "usbcan-debug-snapshot: need ${required_kb} KiB, available ${available_kb:-unknown} KiB" >&2
    exit 1
}

if systemctl is-active --quiet "$service"; then
    was_active=yes
    systemctl stop "$service"
fi

install -d -m 0750 "$destination"
destination_created=yes
cp -a "$source_dir"/usbcan.pcap* "$destination"/

lsusb > "$destination/lsusb.txt" 2>&1 || true
lsusb -t > "$destination/lsusb-tree.txt" 2>&1 || true
ip -details -statistics link show type can \
    > "$destination/can-interfaces.txt" 2>&1 || true
journalctl -u "$service" -b --no-pager \
    > "$destination/usbcan-capture-journal.txt" 2>&1 || true
journalctl -u "$hpm_log_service" -b --no-pager \
    > "$destination/hpm-uart-journal.txt" 2>&1 || true
dmesg --ctime > "$destination/dmesg.txt" 2>&1 || true

snapshot_complete=yes
if [ "$was_active" = yes ]; then
    systemctl start "$service"
fi
was_active=no
trap - EXIT HUP INT TERM

echo "$destination"
