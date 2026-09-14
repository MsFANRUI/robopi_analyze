#!/bin/sh
# Copyright (C) 2026 wentywenty
# SPDX-License-Identifier: GPL-3.0
# 持续跟踪内核日志，为七维会话提供 USB、驱动和设备重置事件。
set -eu
exec dmesg --follow --time-format iso
