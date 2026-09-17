#!/bin/sh
# Copyright (C) 2026 wentywenty
# SPDX-License-Identifier: GPL-3.0
# Follow the kernel log: dmesg first replays the whole ring buffer (all
# history since boot), then follows new messages. The analyzer drops
# events outside the session window by default; use --keep-outside to
# recover the full history.
set -eu
exec dmesg --follow --time-format iso
