#!/bin/bash
# Copyright (C) 2026 wentywenty
# SPDX-License-Identifier: GPL-3.0
# 按固定间隔记录所有 CAN 接口的状态、统计数据和错误计数。
set -euo pipefail
output=${1:?output file required}
interval=${2:-1}
while :; do
    printf '{"captured_at_unix":%s,"interfaces":' "$(date +%s.%N)" >> "$output"
    ip -j -details -statistics link show type can >> "$output" 2>/dev/null || printf '[]' >> "$output"
    printf '}\n' >> "$output"
    sleep "$interval"
done
