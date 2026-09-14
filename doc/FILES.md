# RoboPi 日志工具文件索引

本仓库提供 RoboPi 的 HPM、USB-CAN、CAN 和推理进程诊断工具。运行命令位于
`bin/`，采集流程位于 `capture/`，分析程序位于 `analysis/`，七个数据维度的
采集脚本位于 `dimensions/`，测试数据位于 `tests/`。

## 七个日志维度

| 文件 | 维度 | 输出文件 |
|---|---|---|
| `dimensions/01_bms_status.sh` | BMS/电源服务状态 | `bms-status.txt` |
| `dimensions/02_can_details.sh` | CAN 状态和统计 | `can-details.jsonl` |
| `dimensions/03_kernel_dmesg.sh` | 内核动态日志 | `dmesg-live.txt` |
| `dimensions/04_hpm_uart.sh` | HPM `ttyS4` 串口日志 | `hpm-uart-live.txt` |
| `dimensions/05_usb_pcap.sh` | USB-CAN 原始抓包 | `usbcan.pcap*` |
| `dimensions/06_can_asc.sh` | 四路 CAN ASC 日志 | `can.asc` |
| `dimensions/07_inference_screen.sh` | `inference_session` 输出 | `inference-session.txt` |

## 采集和分析

| 文件 | 作用 |
|---|---|
| `capture/capture_seven_dimensions.sh` | 启动七维同步采集 |
| `capture/capture_usbcan_ring.sh` | 启动 USB-CAN 循环抓包服务 |
| `capture/capture_hpm_uart.sh` | 独立读取 HPM 原始串口，供 systemd 服务使用 |
| `dimensions/06_can_asc.sh` | 唯一的四路 CAN 采集和 ASC 转换实现 |
| `bin/export_seven_dimensions.sh` | 分析、打包并生成 SHA-256 校验文件 |
| `bin/save_usbcan_snapshot.sh` | 保存 USB-CAN 故障快照 |
| `bin/restart_can_interfaces.sh` | 重启四路 CAN，不修改 CAN 配置 |
| `analysis/analyze_seven_dimensions.py` | 合并七维日志时间线 |
| `analysis/analyze_usbcan_urb.py` | 分析 USB URB 状态和错误 |
| `analysis/analyze_usbcan_timeline.py` | 按时间窗口统计 USB 流量 |
| `analysis/analyze_usbcan_echo.py` | 检查 CAN TX echo context |
| `analysis/compare_usbcan_captures.py` | 对比两份 USB-CAN 抓包 |
| `analysis/extract_usbcan_error_windows.py` | 截取 USB 错误时间窗口 |
| `analysis/check_usbcan_snapshot.sh` | 检查快照文件是否完整 |
| `bin/show_communication_dashboard.py` | 显示在线通信状态面板 |

## HPM 工具

| 文件 | 作用 |
|---|---|
| `bin/flash_hpm.sh` | 擦除并烧录 HPM 固件 |
| `bin/hpmtool.py` | HPM USB BootROM 工具 |
| `etc/firmware/` | 随包提供的 HPM/EtherCANFD 固件 |

安装后的稳定命令包括 `robopi-sixd-capture`、`robopi-sixd-analyze`、
`robopi-sixd-export`、`robopi-comm-status`、`usbcan-capture`、
`usbcan-debug-snapshot`、`hpm-log-capture`、`robopi-can-capture` 和
`robopi-can-restart`。
