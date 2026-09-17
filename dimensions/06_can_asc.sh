#!/bin/bash
# Copyright (C) 2026 wentywenty
# SPDX-License-Identifier: GPL-3.0
# 负责四路 CAN 的 candump 采集和 ASC 转换，是 CAN 日志的唯一实现入口。
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

# 用 any 监听全部 CAN 接口:EtherCANFD 提供的 can0~can3 常在开机后一段时间才
# 出现,而 any 绑定"当前和将来的全部接口",晚出现的接口也能立即收到,
# 因此不需要任何等待或重试逻辑。原生三路(can_top/can_hipnuc/can_bottom)
# 的帧会一并写入 can.log,但收尾时 log2asc 只按 interfaces 列表转换,
# 原生三路被自然过滤,ASC 内容与只监听 can0~can3 时一致。
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
