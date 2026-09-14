# 七维日志系统说明

## 1. 目的

七维日志系统用于一次性保存 RoboPi 运控故障现场，将 BMS/电源、CAN、内核、
HPM、USB-CAN、CAN ASC 和推理进程输出放入同一个会话目录，便于在开发机上复盘。

系统只负责采集和分析，不修改 CAN 波特率、CAN-FD 参数或设备配置。

## 2. 七个维度

1. **BMS/电源状态**：周期执行 `systemctl status bms.service`，记录电源监控服务状态。
2. **CAN 状态**：持续记录 CAN 接口状态、错误计数、收发统计和接口变化。
3. **内核日志**：持续保存 `dmesg`，用于定位 USB、驱动、网络和设备重置事件。
4. **HPM 串口**：从 `/dev/ttyS4` 采集 HPM 固件日志，默认 115200 8N1。
5. **USB-CAN 抓包**：保存 `usbmon` 原始 URB PCAP，用于分析 USB 传输和错误状态。
6. **CAN ASC**：同时采集 `can0` 到 `can3`，转换为 Vector ASC 格式，便于使用
   CANalyzer、CANoe、PCAN 等工具分析。
7. **推理进程输出**：读取 `screen` 会话 `inference_session` 的文本输出。

## 3. 开始采集

```bash
sudo robopi-sixd-capture /home/robo/robopi-logs/sixd-$(date +%Y%m%d-%H%M%S)
```

采集脚本会先创建全部预期文件。某个工具、设备或 screen 会话不存在时，对应文件
保持为空，其他维度仍继续运行。这样可以保证不同板卡上的会话目录结构一致。

默认 screen 会话名为 `inference_session`，也可以临时指定：

```bash
sudo INFERENCE_SCREEN_SESSION=my_inference \
  robopi-sixd-capture /home/robo/robopi-logs/sixd-$(date +%Y%m%d-%H%M%S)
```

结束采集时按 `Ctrl-C`。会话中的 `manifest.json` 记录开始和结束时间，采集文件
使用各自工具产生的时间戳，分析器据此合并时间线。

## 4. 在线查看

另一个终端可以查看服务、CAN 状态、抓包文件和七个维度的可用性：

```bash
sudo robopi-comm-status --can can3
```

`can3` 只是示例接口，可以替换为实际需要观察的 CAN 接口。

## 5. 离线分析

```bash
robopi-sixd-analyze /home/robo/robopi-logs/sixd-YYYYMMDD-HHMMSS
```

分析器会在会话目录内生成 `timeline.csv` 和 `summary.json`。空文件会被记录为
缺失或不可用来源，不会导致整个分析失败。

## 6. 一键导出

```bash
robopi-sixd-export /home/robo/robopi-logs/sixd-YYYYMMDD-HHMMSS
cd /home/robo/robopi-logs
sha256sum -c sixd-YYYYMMDD-HHMMSS.zip.sha256
```

导出命令生成会话 ZIP、ZIP 的 SHA-256 校验文件，以及压缩包内每个文件的
`files.sha256` 校验清单。

## 7. 目录约定

```text
bin/         可直接执行的运维、烧录和导出命令
capture/     采集流程脚本
analysis/    离线分析程序
dimensions/  七个维度的采集脚本
tests/       测试脚本、样例抓包和分析结果
doc/         使用说明和文件索引
etc/         默认配置、systemd 单元和固件
```
