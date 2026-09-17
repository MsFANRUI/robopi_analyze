#!/bin/bash
# Copyright (C) 2026 wentywenty
# SPDX-License-Identifier: GPL-3.0

set -euo pipefail
service=${1:-${BMS_SERVICE:-bms.service}}
exec journalctl -u "$service" -n 0 -f -o short-unix
