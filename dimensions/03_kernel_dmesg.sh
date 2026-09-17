#!/bin/sh
# Copyright (C) 2026 wentywenty
# SPDX-License-Identifier: GPL-3.0

set -eu
exec journalctl -k -n 0 -f -o short-unix
