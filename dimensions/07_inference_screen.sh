#!/bin/bash
# Copyright (C) 2026 wentywenty
# SPDX-License-Identifier: GPL-3.0
# 读取 inference_session screen 会话的文本输出。
# screen 会话经常晚于采集启动,因此周期重试挂载,而不是启动时查一次就放弃;
# 超时后写明原因退出,不再静默。
set -euo pipefail
session=${INFERENCE_SCREEN_SESSION:-inference_session}
output=${1:?log output required}
interval=${INFERENCE_SCREEN_RETRY_INTERVAL:-30}
limit=${INFERENCE_SCREEN_WAIT_SECS:-900}

: > "$output"
command -v screen >/dev/null 2>&1 || {
    echo "robopi-inference-screen: screen not installed, giving up" >&2
    exit 0
}

waited=0
while :; do
    if screen -list 2>/dev/null | grep -q "[.]$session"; then
        screen -S "$session" -X logfile "$output"
        screen -S "$session" -X log on
        exit 0
    fi
    if (( waited >= limit )); then
        echo "robopi-inference-screen: screen session '$session' not found after ${waited}s, giving up" >&2
        exit 0
    fi
    sleep "$interval"
    waited=$((waited + interval))
done
