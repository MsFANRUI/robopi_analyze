#!/bin/bash
# Copyright (C) 2026 wentywenty
# SPDX-License-Identifier: GPL-3.0
# Follow the bms.service journal: bms_daemon logs one complete data line
# per second, avoiding the truncation and polling overhead of repeatedly
# running `systemctl status`.
set -euo pipefail
service=${1:-${BMS_SERVICE:-bms.service}}
exec journalctl -u "$service" -n 0 -f -o short-unix
