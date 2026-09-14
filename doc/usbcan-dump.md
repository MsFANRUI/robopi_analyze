# USB-CAN 低干扰循环抓包

本文说明如何在 RK3588 运控设备上持续记录 USB-CAN 的原始 USB 通信，并在问题
发生后保存现场。方案以低运行时开销为目标：抓包期间不解析、不压缩，PCAP 文件
保存在内存文件系统 `/run`，并以固定容量循环覆盖。

默认保留 8 个约 64 MB 的文件，总内存上限约为 512 MB。设备重启或断电会清空
`/run`，因此复现问题后应立即执行 snapshot。

## 方案边界

- 记录的是 Linux `usbmon` 原始 USB URB，不是 `candump` 格式。
- 抓包进程只写传统 PCAP，便于使用包内的
  `bin/analyze_ethercan_pcap.py` 流式分析。安装 `robopi-analyze` 后也可直接运行
  `analyze-ethercan-pcap`。
- snapshot 会短暂停止抓包，复制结束后立即恢复。
- 抓包服务运行期间同时记录 HPM 的 `/dev/ttyS4` 串口日志。
- 默认不压缩 snapshot，避免在运控调试期间产生明显 CPU 负载。
- 抓取指定 USB Bus，避免记录键盘、存储设备等无关或敏感数据。

## 安装

`robopi-analyze` 会安装抓包、snapshot、分析命令及所需的 `tcpdump`、`usbutils`
依赖。服务默认不启用，必须在确认内存预算和 CPU 绑定后手动启用。

从源码单独调试时，安装依赖：

```bash
sudo apt-get update
sudo apt-get install -y tcpdump usbutils
```

可选的性能监控工具：

```bash
sudo apt-get install -y sysstat
```

`usbmon` 是 Linux 内核模块，不需要安装额外软件包：

```bash
sudo modprobe usbmon
sudo tcpdump -D | grep usbmon
```

## 确认 USB Bus

连接 USB-CAN 后执行：

```bash
lsusb
lsusb -t
```

例如设备显示为：

```text
Bus 003 Device 005: ID xxxx:yyyy EtherCANFD
```

对应的抓包接口是 `usbmon3`。Device 编号在重新插拔后可能变化，但连接在同一个
USB 控制器上时 Bus 通常不变。

先用前台抓包验证接口：

```bash
sudo tcpdump -i usbmon3 -s 0 -c 20 -w /tmp/usbcan-test.pcap
ls -lh /tmp/usbcan-test.pcap
```

如果没有数据，重新确认 Bus 编号以及 `usbmon` 模块是否已加载。

## 配置循环抓包

软件包提供 `/etc/default/usbcan-capture`：

```bash
sudo tee /etc/default/usbcan-capture >/dev/null <<'EOF'
USBMON_IFACE=auto
CAN_INTERFACE=can0
CAPTURE_DIR=/run/usbcan
FILE_SIZE_MB=64
FILE_COUNT=8
EOF
```

`USBMON_IFACE=auto` 会沿 `CAN_INTERFACE` 的 sysfs 父设备查找 USB `busnum`。如果
USB-CAN 尚未插入或 `can0` 尚未出现，服务会等待 5 秒后重试。也可以根据 `lsusb`
结果显式设置为 `usbmon3`。不要在生产设备上使用 `usbmon0`，因为它会抓取所有
USB Bus。

软件包同时安装 `usbcan-capture.service`。其等效配置如下，通常不需要手工创建：

```bash
sudo tee /etc/systemd/system/usbcan-capture.service >/dev/null <<'EOF'
[Unit]
Description=RoboPi USB-CAN usbmon flight recorder
Documentation=file:/usr/share/doc/robopi-analyze/usbcan-dump.md
After=local-fs.target systemd-modules-load.service
Wants=hpm-log-capture.service
StartLimitIntervalSec=0

[Service]
Type=simple
EnvironmentFile=/etc/default/usbcan-capture
RuntimeDirectory=usbcan
RuntimeDirectoryMode=0750
RuntimeDirectoryPreserve=yes
ExecStart=/opt/roboparty/bin/usbcan-capture
Restart=on-failure
RestartSec=5
KillSignal=SIGINT
TimeoutStopSec=30
Nice=15
IOSchedulingClass=idle
CPUSchedulingPolicy=batch
CPUAffinity=0 1
UMask=0027

[Install]
WantedBy=multi-user.target
EOF
```

`CPUAffinity=0 1` 将抓包限制在 RK3588 的两个小核上。部署前应检查实际 CPU
拓扑，并确认运控实时线程没有使用相同核心：

```bash
lscpu -e
```

如果系统已有明确的 CPU 隔离和绑核策略，应按该策略调整或删除
`CPUAffinity`。

加载并启动服务：

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now usbcan-capture.service
```

检查状态和循环文件：

```bash
systemctl status usbcan-capture.service --no-pager
sudo journalctl -u usbcan-capture.service -b --no-pager
sudo ls -lh /run/usbcan/
```

`tcpdump -C` 使用十进制 MB。达到 `FILE_COUNT` 后会覆盖最旧文件，因此磁盘或
内存使用量不会持续增长。

## HPM 串口日志

`ttyS4` 是 HPM 固件日志口。启动 `usbcan-capture.service` 时会同时启动
`hpm-log-capture.service`，默认以 115200 波特率、8N1、无流控读取
`/dev/ttyS4`，并写入 systemd journal。串口和波特率可在
`/etc/default/hpm-log-capture` 中修改。

实时查看 HPM 日志：

```bash
sudo journalctl -fu hpm-log-capture.service
```

该串口应保持为 HPM 专用日志口，不能同时由 serial getty 或其他程序读取。

## 保存故障现场

软件包安装 `/usr/bin/usbcan-debug-snapshot`。命令使用文件锁阻止并发 snapshot，
暂停抓包后只复制 `usbcan.pcap*`，同时保存 USB 拓扑、CAN 接口、抓包服务日志、
HPM 串口日志和内核日志，并在成功或异常退出时恢复原本处于运行状态的抓包服务。

故障复现后立即执行：

```bash
sudo usbcan-debug-snapshot
```

snapshot 默认写入 `/var/lib/robopi/usbcan-snapshots`，避免部分 Armbian 系统中容量
很小的 `/var/log` zram。复制前会检查目标文件系统剩余空间；空间不足时不会暂停抓包。
复制中途失败时会删除本次不完整目录，并恢复原本运行的抓包服务。

命令会输出 snapshot 目录，例如：

```text
/var/lib/robopi/usbcan-snapshots/20260911-103000
```

其中 `hpm-uart-journal.txt` 是本次开机以来的 HPM 串口日志，便于与 pcap 时间戳
对照分析。

复制约 512 MB 数据时抓包会暂停数秒，具体时间取决于存储介质。脚本使用 trap
保证复制或诊断命令失败时仍会尝试恢复服务。执行后应确认：

```bash
systemctl is-active usbcan-capture.service
sudo du -sh /var/lib/robopi/usbcan-snapshots/*
```

## 空闲时压缩

确认运控任务结束后再低优先级压缩。以下命令保留原目录，压缩包检查无误后再手动
删除源目录：

```bash
snapshot=/var/lib/robopi/usbcan-snapshots/20260911-103000
sudo nice -n 19 ionice -c 3 \
  tar -C "$(dirname "$snapshot")" \
  -czf "${snapshot}.tar.gz" "$(basename "$snapshot")"
```

检查压缩包：

```bash
sudo tar -tzf "${snapshot}.tar.gz" | head
```

不要在实时运控期间压缩。`gzip` 通常会持续占用一个 CPU 核心，并产生额外读写。

## 离线分析

分析器可以直接读取 snapshot 目录。它会查找目录内的 `usbcan.pcap*`，读取各文件的
首包时间，并按实际抓包时间排列循环文件。假设抓包时设备为 Bus 3、Device 5：

```bash
analyze-ethercan-pcap /path/to/snapshot \
  --bus 3 --device 5 --top 50
```

在源码树中运行时使用：

```bash
python3 bin/analyze_ethercan_pcap.py /path/to/snapshot \
  --bus 3 --device 5 --top 50
```

导出整个 snapshot 的逐帧 CSV。CSV 中的 `source_file` 字段标识帧来自哪个循环
文件：

```bash
analyze-ethercan-pcap /path/to/snapshot \
  --bus 3 --device 5 \
  --csv usbcan-frames.csv
```

如果已确认 Bulk OUT 和 Bulk IN 端点分别为 `0x01` 和 `0x81`，可以进一步过滤：

```bash
# 主机发送到 USB-CAN
analyze-ethercan-pcap /path/to/snapshot \
  --bus 3 --device 5 --endpoint 0x01 --urb-type S

# USB-CAN 返回给主机
analyze-ethercan-pcap /path/to/snapshot \
  --bus 3 --device 5 --endpoint 0x81 --urb-type C
```

第一次分析不要指定 Device 或端点。报告的 `USB 位置` 会列出检测到的 Bus、
Device、Endpoint 和 URB 类型，确认实际位置后再添加过滤条件。USB-CAN 在抓包期间
重插时 Device 编号可能变化，此时只指定 Bus 和端点，或者将不同 Device 分别分析。

## 性能检查

查看抓包进程平均 CPU 占用：

```bash
pid=$(pgrep -x tcpdump)
pidstat -p "$pid" 1
```

查看每个 CPU 核心和存储设备：

```bash
mpstat -P ALL 1
iostat -xz 1
```

正式启用前，应分别在不抓包和抓包状态下测量运控周期抖动。平均 CPU 占用较低并不
代表没有实时调度抖动。如果发现 USB 丢包，可先检查 systemd 日志中的 tcpdump
统计，再考虑增大 `-B` 缓冲区；不要优先提高抓包进程调度优先级。

## 停用与清理

停止并取消开机启动：

```bash
sudo systemctl disable --now usbcan-capture.service
```

设备重启时 `/run/usbcan` 会自动清空。持久 snapshot 需要明确确认后手动删除：

```bash
sudo ls -lh /var/lib/robopi/usbcan-snapshots/
```

USB 抓包可能包含同一 Bus 上其他设备的数据。向外发送 snapshot 前，应确认抓取范围
并按项目的数据管理要求处理。
