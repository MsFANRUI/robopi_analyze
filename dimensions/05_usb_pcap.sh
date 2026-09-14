#!/bin/bash
# Copyright (C) 2026 wentywenty
# SPDX-License-Identifier: GPL-3.0
# 将 USB-CAN 循环抓包文件复制到七维会话目录，设备不可用时保持空目录结构。
set -euo pipefail
capture_dir=${1:-/run/usbcan}
output=${2:?output directory required}
mkdir -p "$output"
cp -a "$capture_dir"/usbcan.pcap* "$output/" 2>/dev/null || true
