#!/bin/bash
# Copyright (C) 2026 wentywenty
# 编排七个维度采集脚本，负责会话生命周期、服务状态和 manifest，不实现具体采集逻辑。
# SPDX-License-Identifier: GPL-3.0
set -euo pipefail

interfaces=(can0 can1 can2 can3)
output=${1:-/var/log/robopi/seven-$(date +%Y%m%d-%H%M%S)}
hpm_service=${HPM_LOG_CAPTURE_SERVICE:-hpm-log-capture.service}
bms_service=${BMS_SERVICE:-bms.service}
capture_dir=${USBCAN_CAPTURE_DIR:-/run/usbcan}
interval=${CAN_DETAILS_INTERVAL:-1}
screen_session=${INFERENCE_SCREEN_SESSION:-inference_session}
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

mkdir -p "$output"
start_unix=$(now)
printf '{\n  "started_at_unix": %s,\n  "interfaces": ["can0", "can1", "can2", "can3"],\n  "capture_dir": "%s",\n  "capture_service": "usbcan-capture.service",\n  "hpm_service": "%s",\n  "bms_service": "%s",\n  "inference_screen_session": "%s"\n}\n' \
    "$start_unix" "$capture_dir" "$hpm_service" "$bms_service" "$screen_session" > "$output/manifest.json"

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
"$dimension_dir/01_bms_status.sh" "$output" "$interval" & dimension_pids+=("$!")
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
    "$dimension_dir/05_usb_pcap.sh" "$capture_dir" "$output"
    end_unix=$(now)
    sed -i '$d' "$output/manifest.json"
    printf ',  "ended_at_unix": %s,\n  "candump_status": %s,\n  "session_started_capture": true\n}\n' \
        "$end_unix" "0" >> "$output/manifest.json"
    echo "Seven-dimensional capture written to $output"
}
trap finish EXIT INT TERM

# 必须阻塞在这里。脚本执行到文件末尾会立即退出并触发 EXIT trap 调用 finish()，
# 把刚启动的七个维度进程全部杀掉，只剩 ring 抓包的 tcpdump 存活。
wait
