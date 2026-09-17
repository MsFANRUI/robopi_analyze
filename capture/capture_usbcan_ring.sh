#!/bin/sh
# Copyright (C) 2026 wentywenty
# SPDX-License-Identifier: GPL-3.0
# 使用 usbmon 和 tcpdump 持续保存 USB-CAN PCAP 循环文件，并联动 HPM 日志服务。
set -eu

USBMON_IFACE=${USBMON_IFACE:-auto}
CAN_INTERFACE=${CAN_INTERFACE:-can0}
CAPTURE_DIR=${CAPTURE_DIR:-/run/usbcan}
FILE_SIZE_MB=${FILE_SIZE_MB:-64}
FILE_COUNT=${FILE_COUNT:-8}

SYS_CLASS_NET=${USBCAN_SYS_CLASS_NET:-/sys/class/net}
MODPROBE_BIN=${USBCAN_MODPROBE_BIN:-/usr/sbin/modprobe}
TCPDUMP_BIN=${USBCAN_TCPDUMP_BIN:-/usr/bin/tcpdump}

die()
{
    echo "usbcan-capture: $*" >&2
    exit 1
}

is_positive_integer()
{
    case $1 in
        ''|*[!0-9]*|0) return 1 ;;
        *) return 0 ;;
    esac
}

resolve_usbmon_interface()
{
    if [ "$USBMON_IFACE" != auto ]; then
        case $USBMON_IFACE in usbmon*) bus_suffix=${USBMON_IFACE#usbmon} ;; *) bus_suffix=invalid ;; esac
        case $bus_suffix in ''|*[!0-9]*) die "USBMON_IFACE must be auto or usbmonN" ;; esac
        printf '%s\n' "$USBMON_IFACE"
        return
    fi

    device_path=$(readlink -f "$SYS_CLASS_NET/$CAN_INTERFACE/device" 2>/dev/null || true)
    [ -n "$device_path" ] || die "$CAN_INTERFACE has no device path; is USB-CAN connected?"

    while [ "$device_path" != / ]; do
        if [ -r "$device_path/busnum" ]; then
            busnum=$(sed 's/^0*//' "$device_path/busnum")
            [ -n "$busnum" ] || busnum=0
            case $busnum in
                *[!0-9]*) die "invalid USB busnum: $busnum" ;;
            esac
            printf 'usbmon%s\n' "$busnum"
            return
        fi
        device_path=${device_path%/*}
        [ -n "$device_path" ] || device_path=/
    done

    die "$CAN_INTERFACE is not backed by a USB device"
}

is_positive_integer "$FILE_SIZE_MB" || die "FILE_SIZE_MB must be a positive integer"
is_positive_integer "$FILE_COUNT" || die "FILE_COUNT must be a positive integer"
if [ "${USBCAN_ALLOW_NON_RUN_CAPTURE_DIR:-no}" != yes ]; then
    case $CAPTURE_DIR in
        /run/*) ;;
        *) die "CAPTURE_DIR must be below /run" ;;
    esac
fi

# CAN_INTERFACE 由 EtherCANFD 提供,常在开机后一段时间才注册;解析 usbmon
# 依赖它的 sysfs 路径,所以要等它出现,而不是查一次就放弃。
wait_for_interface()
{
    limit=${USBCAN_CAN_WAIT_SECS:-120}
    step=2
    waited=0
    while [ ! -e "$SYS_CLASS_NET/$CAN_INTERFACE" ]; do
        if [ "$waited" -eq 0 ]; then
            echo "usbcan-capture: waiting for $CAN_INTERFACE to appear (up to ${limit}s)" >&2
        fi
        if [ "$waited" -ge "$limit" ]; then
            return 1
        fi
        sleep "$step"
        waited=$((waited + step))
    done
    return 0
}

if ! wait_for_interface; then
    die "$CAN_INTERFACE did not appear within ${USBCAN_CAN_WAIT_SECS:-120}s; is USB-CAN connected?"
fi

[ -x "$MODPROBE_BIN" ] || die "modprobe not found: $MODPROBE_BIN"
[ -x "$TCPDUMP_BIN" ] || die "tcpdump not found; install the tcpdump package"

"$MODPROBE_BIN" usbmon
capture_interface=$(resolve_usbmon_interface)
mkdir -p "$CAPTURE_DIR"

echo "usbcan-capture: recording $capture_interface to $CAPTURE_DIR (${FILE_COUNT} x ${FILE_SIZE_MB} MB)"
exec "$TCPDUMP_BIN" -Z root -i "$capture_interface" -s 0 -B 16384 -U \
    -C "$FILE_SIZE_MB" -W "$FILE_COUNT" -w "$CAPTURE_DIR/usbcan.pcap"
