#!/bin/sh
# Copyright (C) 2026 wentywenty
# SPDX-License-Identifier: GPL-3.0
# 持续跟踪 HPM ttyS4 日志服务，并输出带 Unix 时间戳的串口内容。
set -eu
service=${1:-hpm-log-capture.service}
exec journalctl -u "$service" -n 0 -f -o short-unix
