#!/bin/bash
# Copyright (C) 2026 wentywenty
# 依次关闭并重新开启 can0 到 can3，不修改现有 CAN 配置参数。
# SPDX-License-Identifier: GPL-3.0
set -euo pipefail

interfaces=(can0 can1 can2 can3)
sys_class_net=${SYS_CLASS_NET:-/sys/class/net}
ip_bin=${IP_BIN:-ip}

die() {
    echo "robopi-can-restart: $*" >&2
    exit 1
}

if [[ ${CAN_RESTART_ALLOW_NON_ROOT:-no} != yes && $EUID -ne 0 ]]; then
    die "run with sudo"
fi
command -v "$ip_bin" >/dev/null 2>&1 || die "ip command not found"

# Validate all four channels before changing any link state.
for interface in "${interfaces[@]}"; do
    [[ -d "$sys_class_net/$interface" ]] || die "interface not found: $interface"
    [[ $(cat "$sys_class_net/$interface/type" 2>/dev/null) == 280 ]] ||
        die "interface is not CAN: $interface"
done

for interface in "${interfaces[@]}"; do
    "$ip_bin" link set dev "$interface" down
done

for interface in "${interfaces[@]}"; do
    "$ip_bin" link set dev "$interface" up
done

echo "Restarted can0 can1 can2 can3 without changing CAN configuration."
