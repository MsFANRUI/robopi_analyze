# robopi-analyze

RoboPi HPM、BMS、电源和通信故障分析工具包，面向 Ubuntu 24.04 ARM64
RoboPi 系统。包内包含七维日志采集、USB-CAN 抓包、CAN ASC、HPM 串口和
HPM 固件维护工具。

## 快速使用

启动七维日志采集：

```bash
sudo robopi-sixd-capture /home/robo/robopi-logs/session
```

结束采集后分析并导出：

```bash
robopi-sixd-analyze /home/robo/robopi-logs/session
robopi-sixd-export /home/robo/robopi-logs/session
cd /home/robo/robopi-logs
sha256sum -c session.zip.sha256
```

在线查看通信和七维状态：

```bash
sudo robopi-comm-status --can can3
```

紧急下电前优先保存现场：

```bash
sudo USBCAN_SNAPSHOT_DIR=/home/robo/usbcan-snapshots \
  usbcan-debug-snapshot
sync
```

## 七维日志

七个维度为：

1. `bms.service` BMS/电源服务状态
2. CAN 接口状态和统计
3. 动态内核日志
4. HPM `ttyS4` 串口日志
5. USB-CAN 原始 PCAP
6. 四路 CAN ASC 日志
7. `inference_session` screen 输出

详细说明见 [`doc/seven-dimension-logging.md`](doc/seven-dimension-logging.md)。

## USB-CAN 抓包

循环抓包服务默认不启用，复现问题时手动启动：

```bash
sudo systemctl start usbcan-capture.service
sudo usbcan-debug-snapshot
sudo systemctl stop usbcan-capture.service
```

抓包、快照、PCAP 合并和离线分析说明见
[`doc/usbcan-dump.md`](doc/usbcan-dump.md)。

## 文件索引

脚本职责和安装后的命令见 [`doc/FILES.md`](doc/FILES.md)。

源码目录职责：

```text
bin/         可直接执行的运维、烧录和导出命令
capture/     七维会话和 USB-CAN 服务编排脚本
dimensions/  七个独立日志采集维度
analysis/    离线分析程序
tests/       测试脚本、样例抓包和分析结果
doc/         使用说明和文件索引
etc/         默认配置、systemd 单元和固件
```
