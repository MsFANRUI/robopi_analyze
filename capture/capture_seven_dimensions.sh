#!/bin/bash
# Copyright (C) 2026 wentywenty
# 编排七个维度采集脚本，负责会话生命周期、服务状态和 manifest，不实现具体采集逻辑。
# SPDX-License-Identifier: GPL-3.0
set -euo pipefail

interfaces=(can0 can1 can2 can3)

session_root=${SESSION_ROOT:-/home/robo/robopi-logs}
output=${1:-$session_root/seven-$(date +%Y%m%d-%H%M%S)}
hpm_service=${HPM_LOG_CAPTURE_SERVICE:-hpm-log-capture.service}
bms_service=${BMS_SERVICE:-bms.service}
capture_dir=${USBCAN_CAPTURE_DIR:-/run/usbcan}
interval=${CAN_DETAILS_INTERVAL:-1}
screen_session=${INFERENCE_SCREEN_SESSION:-inference_session}
# 机身标识:主控序列号取自设备树(裸字节,须去掉 \0,并只保留 JSON 安全字符),
# 读不到时降级用 machine-id;boot_id 标识本次开机,可与 journalctl --list-boots 对齐。
board_serial=$(cat /proc/device-tree/serial-number 2>/dev/null | tr -d '\0' | tr -cd 'a-zA-Z0-9-' || true)
[[ -n $board_serial ]] || board_serial=$(tr -cd 'a-zA-Z0-9-' < /etc/machine-id 2>/dev/null || true)
boot_id=$(tr -cd 'a-zA-Z0-9-' < /proc/sys/kernel/random/boot_id 2>/dev/null || true)
capture_pid=
dimension_pids=()
ended=no

die() { echo "robopi-seven-capture: $*" >&2; exit 1; }
now() { date +%s.%6N; }

if [[ ${SEVEN_ALLOW_NON_ROOT:-no} != yes && $EUID -ne 0 ]]; then die "run with sudo"; fi
command -v systemctl >/dev/null 2>&1 || die "systemctl not found"
[[ $interval =~ ^[0-9]+([.][0-9]+)?$ && $interval != 0 ]] || die "CAN_DETAILS_INTERVAL must be positive"
[[ ! -e $output ]] || die "session directory already exists: $output"
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
dimension_dir=${SEVEN_DIMENSIONS_DIR:-$script_dir/../dimensions}
if [[ ! -x $dimension_dir/01_bms_status.sh && -x /opt/roboparty/lib/robopi-analyze/dimensions/01_bms_status.sh ]]; then
    dimension_dir=/opt/roboparty/lib/robopi-analyze/dimensions
fi
[[ -x $dimension_dir/01_bms_status.sh ]] || die "dimension scripts not found: $dimension_dir"
ring_capture=${USBCAN_RING_CAPTURE_COMMAND:-$script_dir/capture_usbcan_ring.sh}
if [[ ! -x $ring_capture && -x /opt/roboparty/bin/usbcan-capture ]]; then
    ring_capture=/opt/roboparty/bin/usbcan-capture
fi
[[ -x $ring_capture ]] || die "USB-CAN ring capture command not found: $ring_capture"

# 启动时先清理过期会话(超龄/超量),再检查空间,最后才建会话目录。
cleanup_command=${SESSION_CLEANUP_COMMAND:-$script_dir/cleanup_sessions.sh}
if [[ ! -x $cleanup_command && -x /opt/roboparty/bin/robopi-session-cleanup ]]; then
    cleanup_command=/opt/roboparty/bin/robopi-session-cleanup
fi
if [[ -x $cleanup_command ]]; then
    "$cleanup_command" || echo "robopi-seven-capture: session cleanup failed, continuing" >&2
fi

session_parent=$(dirname "$output")
mkdir -p "$session_parent"
# 空间预检:目标分区至少要装得下 ring 上限加余量。宁可在这里明确失败,
# 也不让维度写一半变成静默的 0 字节文件。
need_kb=$(( ${USBCAN_FILE_SIZE_MB:-64} * ${USBCAN_FILE_COUNT:-8} + 512 ))
have_kb=$(df -Pk "$session_parent" 2>/dev/null | awk 'NR==2 {print $4}' || true)
[[ -n $have_kb && $have_kb -ge $need_kb ]] || \
    die "insufficient space in $session_parent: need ${need_kb} KiB, available ${have_kb:-unknown} KiB"

mkdir -p "$output"
start_unix=$(now)
printf '{\n  "started_at_unix": %s,\n  "boot_id": "%s",\n  "board_serial": "%s",\n  "interfaces": ["can0", "can1", "can2", "can3"],\n  "capture_dir": "%s",\n  "capture_service": "usbcan-capture.service",\n  "hpm_service": "%s",\n  "bms_service": "%s",\n  "inference_screen_session": "%s"\n}\n' \
    "$start_unix" "$boot_id" "$board_serial" "$capture_dir" "$hpm_service" "$bms_service" "$screen_session" > "$output/manifest.json"

# 先创建固定会话结构；没有对应硬件或工具时，维度文件保持为空。
: > "$output/bms-status.txt"
: > "$output/can-details.jsonl"
: > "$output/dmesg-live.txt"
: > "$output/hpm-uart-live.txt"
: > "$output/can.log"
: > "$output/can.asc"
: > "$output/inference-session.txt"

systemctl start "$hpm_service" || true

CAPTURE_DIR="$capture_dir" "$ring_capture" & capture_pid=$!
"$dimension_dir/01_bms_status.sh" > "$output/bms-status.txt" 2>&1 & dimension_pids+=("$!")
"$dimension_dir/02_can_details.sh" "$output/can-details.jsonl" "$interval" & dimension_pids+=("$!")
"$dimension_dir/03_kernel_dmesg.sh" > "$output/dmesg-live.txt" 2>&1 & dimension_pids+=("$!")
"$dimension_dir/04_hpm_uart.sh" "$hpm_service" > "$output/hpm-uart-live.txt" 2>&1 & dimension_pids+=("$!")
"$dimension_dir/06_can_asc.sh" "$output" & dimension_pids+=("$!")
INFERENCE_SCREEN_SESSION="$screen_session" "$dimension_dir/07_inference_screen.sh" "$output/inference-session.txt" & dimension_pids+=("$!")

echo "Seven-dimensional capture started: $output"
echo "Capturing seven dimensions; press Ctrl-C to finish."
finish() {
    [[ $ended == yes ]] && return
    ended=yes
    set +e
    [[ -n $capture_pid ]] && kill -INT "$capture_pid" 2>/dev/null
    for pid in "${dimension_pids[@]}"; do kill "$pid" 2>/dev/null; done
    wait 2>/dev/null
    screen -S "$screen_session" -X log off 2>/dev/null || true
    "$dimension_dir/05_usb_pcap.sh" "$capture_dir" "$output"
    end_unix=$(now)
    sed -i '$d' "$output/manifest.json"
    printf ',  "ended_at_unix": %s,\n  "candump_status": %s,\n  "session_started_capture": true\n}\n' \
        "$end_unix" "0" >> "$output/manifest.json"
    echo "Seven-dimensional capture written to $output"
}
trap finish EXIT INT TERM


wait
