# roboparty-analyze

仓库名为 `roboparty_analyze`，Debian 包名为 `roboparty-analyze`。
1.0.11 起包名改为 `roboparty-analyze`；1.0.17 起不再构建旧名
`robopi-analyze` 过渡包，原有采集命令和 service 名保持兼容。

RoboPi HPM、BMS、电源和通信故障分析工具包，面向 Ubuntu 24.04 ARM64 RoboPi
系统。包内包含七维日志采集、USB-CAN 抓包、CAN ASC、HPM 串口和推理进程分析工具。

## eBPF 现场观测

安装包同时提供只读 eBPF 观测工具，用于补充 USB pcap、CAN 日志和 dmesg 无法看到的内核时序。入口包括：

```text
robopi-ebpf-can-state [IFACE] [SECONDS]
robopi-ebpf-monitor-sched TID [SECONDS] [THRESHOLD_US]
```

这些命令需要 root、运行内核 BTF/kprobe 和 bpftrace。它们不会发送 CAN、修改波特率或 IRQ affinity；`robopi-ebpf-can-state` 记录 `can_bus_off`/`can_restart` 入口，`robopi-ebpf-monitor-sched` 记录指定线程的 off-CPU/就绪等待。`rp-cpu0-trace.service`/`cpu0-flight.py` 提供 CPU0 故障窗口记录，`capture-response-window.py` 用于应用侧同窗观测。入口事件不等于硬件恢复完成，USB-CAN 业务验证仍需接口 UP、明确固件身份和独立台架。完整边界见安装目录的 `ebpf/README.md`。

## perf 性能采集

`robopi-perf-capture` 只观测已有负载，不启动/停止运控、不改亲和性/优先级、不发 CAN、不停止现有 BPF 探针。需要 root、`mpstat`/`pidstat`（sysstat）和 `perf`；自定义兼容 perf 用 `PERF_BIN`，vendored 工具链可用 `RP_PERF_VENDOR`。

```bash
sudo robopi-perf-capture sched 20          # 调度时间线、线程 CPU、频率/温度
sudo robopi-perf-capture stats 20          # mpstat/pidstat 统计
sudo robopi-perf-capture profile 20 --pid PID
sudo robopi-perf-capture analyze OUTPUT_DIRECTORY   # 停机后分析
```

采集时长 2–60 秒；输出默认 `/home/robo/perf-results`，可用 `--output` 或 `RP_PERF_OUTPUT` 覆盖。不要同时跑 `sched` 和 `profile`；现有 BPF 探针会增加开销，对比时要记录当时的探针状态。

解读时注意：`sched-timehist.txt` 的等待包含睡眠/阻塞，不等于锁等待；`hotspots.txt` 是 49 Hz 软件 `cpu-clock` 采样，缺少帧指针时栈不完整；`frequency.jsonl` 是 1 Hz 报告值；`metadata.json`、`record.stderr`、`events.txt` 要先确认没有 lost 事件。自定义 6.18 内核对 distro perf 的兼容性需实测，不能只看版本号。2026-10-22 已在 RK3588S/6.18.58 PREEMPT_RT 上做过 3 秒 sched/profile 和 2 秒 stats 验证，尚未做带载开销标定。

## 服务职责

- `hpm-log-capture.service` 是常驻 HPM 日志守护进程，持续读取 `/dev/ttyS4`。
- `hpm-sreset.service` 启动时先执行一次 HPM 正常启动复位，之后作为常驻监视器，
  找不到 HPM 时执行复位。
- `usbcan-capture.service` 安装后默认启用，系统下次启动时自动开始七维采集。
- `usbcan-debug-snapshot` 只用于额外的 USB 诊断快照，不能替代完整的 `seven-*` 会话。

以下是维护命令，不是每次复现的步骤。仅在服务已被手动停止、又想在本次开机恢复采集时执行：

```bash
sudo systemctl start usbcan-capture.service
```

查看状态：

```bash
sudo robopi-comm-status
```

结束采集：

```bash
sudo systemctl stop usbcan-capture.service
```

停止七维采集不会停止 HPM 日志守护进程。

## 七个维度

1. BMS/电源：周期记录 `bms.service` 状态。
2. CAN 状态：记录四路 CAN 的状态、错误计数和收发统计。
3. 内核日志：记录动态 `dmesg`，用于定位驱动和 USB 事件。
4. HPM 串口：记录 `/dev/ttyS4` 的 HPM 固件日志，默认 1500000 8N1，与当前 EtherCANFD 固件控制台一致。
5. USB-CAN：记录 `usbmon` 原始 USB URB PCAP。
6. CAN 报文：现场保存原始 `can.log`，需要 Vector ASC 时再离线转换。
7. 推理输出：记录 `inference_session` screen 会话文本。

没有 BMS 数据或可见的 screen 会话时，对应日志可能没有业务内容；这不等于 CAN、USB 或主机日志没采。关键抓包进程是否失败，看会话收尾后的 `manifest.json`。

HPM 日志配置位于 `/etc/default/hpm-log-capture`，服务读取 `HPM_LOG_BAUD`；
脚本未设置该变量时也使用 1500000。旧固件可显式覆盖，须以实际烧录版本为准。
若升级保留了本地修改过的配置文件，应检查该值，不能认为更新软件包已自动改动现场设置。
`ttyS4` 的 HPM 日志与 Windows 转发的 `ttyS2/COM47` Linux 控制台是两条不同链路。

## 七维采集

以下是手工调试入口；日常用开机自启的服务即可，不要在其运行时另起一套采集。手工入口：

```bash
sudo robopi-seven-capture /home/robo/robopi-logs/seven-$(date +%Y%m%d-%H%M%S)
```

也可以使用 systemd 入口：

```bash
sudo systemctl start usbcan-capture.service
```

默认 screen 会话名为 `inference_session`，临时指定其他会话：

```bash
sudo env INFERENCE_SCREEN_SESSION=my_inference \
  robopi-seven-capture /home/robo/robopi-logs/seven-$(date +%Y%m%d-%H%M%S)
```

按 `Ctrl-C` 结束命令入口的采集。会话目录包含：

```text
manifest.json
bms-status.txt
can-details.jsonl
dmesg-live.txt
hpm-uart-live.txt
usbcan.pcap*
can.log
inference-session.txt
usb-capture.txt
thermal.txt
host-environment.json
host-health.jsonl
stall/                  # 仅在观察到触发事件时出现
```

## USB-CAN 配置

七维采集直接向当前会话目录写入 `usbcan.pcap*`，每个分片约 64 MB，
封口分片保留至会话清理，不再每 10 秒镜像复制。`/run/usbcan/current-session`
指向最近会话，供快照工具定位。会话大小、轮转和总保留量由 session guard 管理；
`SESSION_MAX_SIZE_MB` 是软上限，磁盘写入量与可用空间仍需监测。
会话 PCAP 使用缓冲写入，不再逐包 flush；正常轮转和停止会刷新尾部，
突然断电可能丢失尚未刷出的尾部记录。

运行期间及停止时仅保存 CAN 原始 `can.log`，不自动转换 ASC。停止业务后按需执行：

```bash
robopi-can-capture --convert /home/robo/robopi-logs/seven-实际会话
```

该命令仅转换已有文件，不发送 CAN 报文。七维分析器直接读取 `can.log`，不依赖 ASC。
采集服务及其子进程限制在 CPU2/3，保留 Nice=15、batch 调度和 idle I/O。
主进程每秒检查关键采集进程；意外退出会结束本轮并返回失败，由 systemd 重启采集。
最终 manifest 保存 `candump_status`、`usb_capture_status`、`capture_failed` 和
`failed_recorder`；`usb-capture.txt` 保留 tcpdump 的统计和报错。状态字段为原始退出码，
受控停止可能记录 130/143，应结合 `capture_failed` 判断。
`recorder-status.tsv` 保存各采集进程的原始退出码及是否由本轮清理请求终止。

单独运行 `usbcan-capture` 时，循环 PCAP 默认写入 `/run/usbcan/usbcan.pcap*`，配置文件为
`/etc/default/usbcan-capture`：

```text
USBMON_IFACE=auto
CAN_INTERFACE=can3
CAPTURE_DIR=/run/usbcan
FILE_SIZE_MB=64
FILE_COUNT=8
```

默认保留 8 个、每个约 64 MB 的文件，达到数量后覆盖最旧文件。自动识别 USB Bus
异常时可以查看：

```bash
lsusb
lsusb -t
sudo tcpdump -D | grep usbmon
```

HPM 日志实时查看：

```bash
sudo journalctl -fu hpm-log-capture.service
```

## 紧急快照

仅当需要**额外的 USB 抓包与即时状态**时使用。此命令会暂停采集，ZIP 不包含完整主机历史或 `stall/` 故障窗口。日常复现不要执行；直接下载 `/home/robo/robopi-logs/` 中的原会话：

```bash
sudo usbcan-debug-snapshot
sync
```

默认生成以下内容：

```text
/home/robo/usbcan-snapshots/YYYYMMDD-HHMMSS/
/home/robo/usbcan-snapshots/YYYYMMDD-HHMMSS.zip
/home/robo/usbcan-snapshots/YYYYMMDD-HHMMSS.zip.sha256
```

快照会暂停七维采集服务，复制 PCAP 和当前状态后恢复服务；HPM 守护进程始终保持
运行。自定义输出目录：

```bash
sudo env USBCAN_SNAPSHOT_DIR=/mnt/logs usbcan-debug-snapshot
```

校验压缩包：

```bash
cd /home/robo/usbcan-snapshots
sha256sum -c YYYYMMDD-HHMMSS.zip.sha256
unzip -l YYYYMMDD-HHMMSS.zip | head
```

## 分析与导出

先下载已结束的整个 `seven-*` 会话。在 Windows 电脑上的用法见[使用教程](TUTORIAL.md)；在装有本工具的 Linux 电脑上，下面以已经下载的会话为例，报告另存，不修改原件：

```bash
robopi-seven-analyze ./seven-YYYYMMDD-HHMMSS --out ./report
```

需要旧的合并时间轴时加 `--merged`，额外生成 `timeline.csv`：

```bash
robopi-seven-analyze --merged ./seven-YYYYMMDD-HHMMSS --out ./report
```

可选：需要 ZIP 和 SHA-256 时，以下命令会先将分析结果写入输入目录，再打包。请对会话的工作副本执行，不要对正在写入的会话或唯一原件执行：

```bash
robopi-seven-export ./seven-YYYYMMDD-HHMMSS
```

只分析 USB-CAN PCAP：

```bash
analyze-ethercan-pcap ./seven-YYYYMMDD-HHMMSS --top 50
```

第一次分析建议不要指定 Bus、Device 或 Endpoint，先根据报告确认设备位置，再按需
添加 `--bus`、`--device` 或 `--endpoint` 过滤条件。

## 文件索引

### 七个维度

| 文件 | 维度 | 输出文件 |
|---|---|---|
| `dimensions/01_bms_status.sh` | BMS/电源状态 | `bms-status.txt` |
| `dimensions/02_can_details.sh` | CAN 状态和统计 | `can-details.jsonl` |
| `dimensions/03_kernel_dmesg.sh` | 内核动态日志 | `dmesg-live.txt` |
| `dimensions/04_hpm_uart.sh` | HPM `ttyS4` 日志 | `hpm-uart-live.txt` |
| `dimensions/05_usb_pcap.sh` | USB-CAN 原始抓包 | `usbcan.pcap*` |
| `dimensions/06_can_asc.sh` | CAN 原始记录；显式离线转换 ASC | `can.log`，按需生成 `can.asc` |
| `dimensions/07_inference_screen.sh` | inference screen 输出 | `inference-session.txt` |
| `dimensions/09_host_health.py` | 主机观测（可选） | `host-health.jsonl`、`host-environment.json`、`stall/` |

### 采集和分析

| 文件 | 作用 |
|---|---|
| `capture/capture_seven_dimensions.sh` | 七维同步采集编排 |
| `capture/capture_usbcan_ring.sh` | USB-CAN 循环抓包实现 |
| `capture/capture_hpm_uart.sh` | HPM 原始串口读取实现 |
| `bin/save_usbcan_snapshot.sh` | 保存额外的 USB 抓包和即时状态、ZIP 和 SHA-256；不替代原会话 |
| `bin/export_seven_dimensions.sh` | 分析、打包七维会话 |
| `bin/restart_can_interfaces.sh` | 重启四路 CAN，不修改 CAN 配置 |
| `bin/guard_sessions.py` | 每 20 秒清理旧会话，活动会话超过容量限制时请求轮转 |
| `bin/show_communication_dashboard.py` | 在线通信状态面板 |
| `analysis/analyze_seven_dimensions.py` | 七维日志按维度拆分为 CSV（`--merged` 出合并时间轴） |
| `analysis/analyze_usbcan_urb.py` | 分析 USB URB 状态和错误 |
| `analysis/analyze_usbcan_timeline.py` | 按时间窗口统计 USB 流量 |
| `analysis/analyze_usbcan_echo.py` | 检查 CAN TX echo context |
| `analysis/compare_usbcan_captures.py` | 对比 USB-CAN 抓包 |
| `analysis/extract_usbcan_error_windows.py` | 截取 USB 错误时间窗口 |
| `analysis/check_usbcan_snapshot.sh` | 检查快照文件完整性 |

### HPM 工具

| 文件 | 作用 |
|---|---|
| `bin/flash_hpm.sh` | 擦除并烧录 HPM 固件 |
| `bin/hpmtool.py` | HPM USB BootROM 工具 |
| `firmware/` | 随包提供的 HPM/EtherCANFD 固件 |

安装后的稳定命令包括 `robopi-seven-capture`、`robopi-seven-analyze`、
`robopi-seven-export`、`robopi-comm-status`、`usbcan-capture`、
`usbcan-debug-snapshot`、`hpm-log-capture`、`robopi-can-capture`、
`robopi-can-restart`、`analyze-ethercan-pcap`、`analyze-response-trace`、
`check-usbcan-snapshot` 和 `robopi-perf-capture`。HPM 自动复位由
`hpm-sreset.service` 在后台处理。

`bin/flash_hpm.sh` 在烧录前停止 `hpm-sreset.service`，烧录完成后重新启动它，
避免 HPM 进入 USB BootROM 时被监视器复位；可用 `HPM_RESET_SERVICE` 覆盖服务名。

### 通信停顿与主机负载

每个会话新增 `host-health.jsonl` 和 `host-environment.json`。前者约每 50 ms 记录目标硬中断、软中断和 CAN 接口收发计数，每秒记录整机和各核 CPU 占用、1/5/15 分钟负载、PSI、运控与采集进程占用。进程占用以单核为 100%；首样本为 null，不是空闲。实际采样间隔也入日志。运控 PID/连接状态只在变化时写，原始输出仍只进 `inference-session.txt`。screen 观察其运行账户可见的精确会话名，晚启动或重建会重连；不可见不代表业务退出。

can0～can3 和 can_hipnuc 每个接口的 RX/TX 独立判断；同方向连续三个采样区间有进展后，采样时发现距上次计数增长已满 95 ms，才会触发 `can0_rx_silent` 等事件（按 50 ms 采样，通常约 100 ms 时记下）。冷启动空闲不告警。事件是 Linux 接口计数证据，不指认应用、单台电机或电气故障。采集器晚醒记为 `sampler_gap`；接口消失或计数回退也不冒充恢复。进入 ERROR-PASSIVE/BUS-OFF 可独立触发。

`stall/<编号>.json` 保存含触发点的最近 10 条快样本，并跟随停顿方向直到恢复后再采 3 条，最多约 5 秒（超时不代表恢复）。无方向的 CAN 状态事件采约 5 秒。后续首次进展或观测中断也在 `host-health.jsonl` 记 `direction_end`，可按 `file` 找回原事件。`*.processes.json` 留下最近最多 4 张约一秒一次的全进程表，包含可读 PID、启动标识、调度策略、实时优先级和窗口 CPU 占用；首样本无占用基线。软中断快样本含全部内核类别和逐核总计（次数，不是耗时）。

`*.threads.json` 是触发后的筛选线程快照（每会话最多每 10 秒一次）。默认对故障额外请求诊断 SysRq **小写 l**，每会话最多每 60 秒一次；在 `sysrq_l` 查看请求时间与结果，再对照 `dmesg-live.txt`，写入成功并不保证每核都有栈。`HOST_HEALTH_SYSRQ_L=no` 只关闭打印内核栈，不抑制其他证据。不同方向和链路状态的事件不受这两项冷却限制。所有输出仍服从原会话时长、大小和保留策略。`HOST_HEALTH_ENABLED=no` 可关闭整个观察器。

离线运行 `robopi-seven-analyze SESSION --out REPORT_DIR` 可保留原件。检查 `bms.csv` 的带符号总电流/功率、`can_gaps.csv` 的相邻帧空窗（默认 ≥100 ms）、`cpu_load.csv`（各核占用）、`can_gap_cpu.csv`（空窗与占用区间的交集）、`host_health.csv` 及 `summary.json` 中各口覆盖、PCAP 记录完整性与预计收尾状态。USB 四口组表示组内任意一口的最后/下一帧，并不证明四口之前都有业务；结合各口覆盖判断。一秒平均 CPU 占用不能独立证明亚秒停顿的根因。

源码目录职责：

```text
bin/         可直接执行的运维、烧录和导出命令
capture/     七维采集流程脚本
dimensions/  七个独立日志采集维度
analysis/    离线分析程序
README.md     项目说明和使用文档
etc/         默认配置、systemd 单元和固件
```
