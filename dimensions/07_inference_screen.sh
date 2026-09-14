#!/bin/bash
# Copyright (C) 2026 wentywenty
# SPDX-License-Identifier: GPL-3.0
# 读取 inference_session screen 会话的文本输出，不存在会话时创建空日志文件。
set -euo pipefail
session=${INFERENCE_SCREEN_SESSION:-inference_session}
output=${1:?log output required}
: > "$output"
if command -v screen >/dev/null 2>&1 && screen -list | grep -q "[.]$session"; then
    screen -S "$session" -X logfile "$output"
    screen -S "$session" -X log on
fi
