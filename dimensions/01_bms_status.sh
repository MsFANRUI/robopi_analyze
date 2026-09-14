#!/bin/bash
# Copyright (C) 2026 wentywenty
# SPDX-License-Identifier: GPL-3.0
# 按固定间隔记录 bms.service 状态，作为七维电源/BMS 监控数据。
set -euo pipefail
out=${1:?output directory required}
interval=${2:-1}
mkdir -p "$out"
while :; do
    printf '# captured_at_unix=%s\n' "$(date +%s.%N)" >> "$out/bms-status.txt"
    systemctl status bms.service --no-pager >> "$out/bms-status.txt" 2>&1 || true
    sleep "$interval"
done
