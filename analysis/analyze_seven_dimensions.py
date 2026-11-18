#!/usr/bin/env python3
# Copyright (C) 2026 wentywenty
# SPDX-License-Identifier: GPL-3.0
"""Align seven RoboPi diagnostic dimensions on one Unix timestamp axis."""

import argparse
from bisect import bisect_left
import csv
import datetime as dt
import json
import re
import mmap
import struct
from pathlib import Path


def event(rows, dimension, timestamp, kind, detail, **extra):
    if timestamp is not None:
        row = {"timestamp": timestamp, "time_local": dt.datetime.fromtimestamp(timestamp).isoformat(),
               "dimension": dimension, "kind": kind, "detail": detail}
        row.update(extra)
        rows.append(row)


def read_manifest(path):
    return json.loads(path.read_text(encoding="utf-8"))


# Only these count toward can-asc: can.log also carries native-bus frames
# (can_top/can_hipnuc/can_bottom) since capture listens on "any".
CAN_INTERFACES = ("can0", "can1", "can2", "can3")

# Split-mode outputs: dimension -> (file, columns). "session" events are not
# listed here on purpose — start/end live in summary.json, and only the
# optional merged timeline keeps them as rows.
DIMENSION_OUTPUTS = {
    "can-asc": ("can_frames.csv", ("timestamp", "time_local", "kind", "interface", "frame")),
    "bms": ("bms.csv", ("timestamp", "time_local", "kind", "voltage", "current", "soc", "power", "detail", "pack_power_w", "sample_interval_s")),
    "dmesg": ("dmesg.csv", ("timestamp", "time_local", "kind", "detail")),
    "hpm-uart": ("hpm_uart.csv", ("timestamp", "time_local", "kind", "detail")),
    "thermal": ("thermal.csv", ("timestamp", "time_local", "kind", "detail")),
    "inference-session": ("inference.csv", ("timestamp", "time_local", "kind", "detail")),
    "can-details": ("can_details.csv", ("timestamp", "time_local", "kind", "detail")),
    "usb-pcap": ("usb_pcap.csv", ("timestamp", "time_local", "kind", "file")),
}

MERGED_FIELDNAMES = ("timestamp", "time_local", "dimension", "kind", "detail")


def parse_can(path, rows, archive, bounds, merged):
    """Stream full-rate frames to CSV. Keep only gaps/coverage in memory by default."""
    scopes = (*CAN_INTERFACES, 'can_hipnuc', 'usb-any', 'motor-or-imu')
    coverage = {k: {'frames':0, 'first':None, 'last':None, 'backsteps':0} for k in scopes}
    frame_count = dropped = 0
    filename, cols = DIMENSION_OUTPUTS['can-asc']
    pattern = re.compile(r'\((\d+\.\d+)\)\s+(\S+)\s+(\S+)')
    lo, hi = bounds
    with (archive/filename).open('w', newline='', encoding='utf-8') as frames, \
            (archive/'can_gaps.csv').open('w', newline='', encoding='utf-8') as gaps:
        fw, gw = csv.writer(frames), csv.writer(gaps)
        fw.writerow(cols)
        gw.writerow(('scope','before_unix','after_unix','gap_ms','before_interface','after_interface'))
        if not path.exists():
            return coverage, frame_count, dropped
        previous_bus = {}
        with path.open(errors='replace') as src:
            for line in src:
                m = pattern.match(line)
                if not m or m[2] not in scopes[:5]:
                    continue
                stamp, bus, payload = float(m[1]), m[2], m[3]
                if lo is not None and hi is not None and not lo <= stamp <= hi:
                    dropped += int(bus in CAN_INTERFACES)
                    continue
                for group in ((bus, 'usb-any', 'motor-or-imu') if bus in CAN_INTERFACES else (bus,'motor-or-imu')):
                    state = coverage[group]
                    prev = state['last']
                    if prev is None:
                        state['first'] = stamp
                    elif stamp < prev:
                        state['backsteps'] += 1
                    elif stamp-prev >= .100:
                        gw.writerow((group, prev, stamp, round((stamp-prev)*1000,3),previous_bus[group],bus))
                    state['frames'] += 1
                    state['last'] = stamp
                    previous_bus[group] = bus
                if bus in CAN_INTERFACES:
                    frame_count += 1
                    fw.writerow((stamp,dt.datetime.fromtimestamp(stamp).isoformat(),'frame',bus,payload))
                    if merged:
                        event(rows,'can-asc',stamp,'frame',f'{bus} {payload}',interface=bus,frame=payload)
    return coverage, frame_count, dropped


def parse_unix_lines(path, rows, dimension):
    if not path.exists():
        return
    for line in path.read_text(errors="replace").splitlines():
        match = re.match(r"(?:[^ ]+\s+)?(\d+\.\d+)\s+(.*)", line)
        if match:
            event(rows, dimension, float(match.group(1)), "log", match.group(2))


BMS_LINE = re.compile(
    r'(\d+\.\d+)\s+\S+\s+\S+.*?\[BMS Data\] Voltage: (\d+(?:\.\d+)?)V'
    r' \| Current: ([+-]?\d+(?:\.\d+)?)A \| SoC: (\d+)% \| Power: (\S+)')


def parse_bms(path, rows):
    previous = None
    failed = False
    if not path.exists():
        return
    with path.open(errors='replace') as stream:
        for line in stream:
            m = BMS_LINE.match(line)
            if m:
                stamp, voltage, current = map(float, m.group(1,2,3))
                data = dict(voltage=voltage,current=current,soc=int(m[4]),power=m[5],
                            pack_power_w=round(voltage*current, 6),
                            sample_interval_s=round(stamp-previous,6) if previous is not None and stamp>=previous else None)
                if failed:
                    event(rows,'bms',stamp,'recovered','first valid sample after read failure')
                event(rows,'bms',stamp,'sample',json.dumps(data,separators=(',',':')),**data)
                previous, failed = stamp, False
            elif 'BMS Read Failure' in line or 'Port seems disconnected' in line:
                stamp = re.match(r'(\d+\.\d+)\s',line)
                if stamp:
                    event(rows,'bms',float(stamp[1]),'read_failure',line.rstrip())
                    failed = True


def parse_marked_snapshot(path, rows, dimension):
    if not path.exists():
        return
    timestamp = None
    details = []
    for line in path.read_text(errors="replace").splitlines():
        match = re.match(r"# captured_at_unix=(\d+(?:\.\d+)?)", line)
        if match:
            if timestamp is not None:
                event(rows, dimension, timestamp, "snapshot", "\\n".join(details))
            timestamp = float(match.group(1))
            details = []
        elif timestamp is not None:
            details.append(line)
    if timestamp is not None:
        event(rows, dimension, timestamp, "snapshot", "\\n".join(details))


def parse_dmesg(path, rows):
    if not path.exists():
        return
    for line in path.read_text(errors="replace").splitlines():
        match = re.match(r"(?:<\d+>)?\s*(\d{4}-\d{2}-\d{2}T\S+)\s+(.*)", line)
        if not match:
            continue
        value = match.group(1).replace("Z", "+00:00")
        try:
            timestamp = dt.datetime.fromisoformat(value).timestamp()
        except ValueError:
            continue
        event(rows, "dmesg", timestamp, "log", match.group(2))


def parse_inference(path, rows):
    if not path.exists():
        return
    for line in path.read_text(errors="replace").splitlines():
        match = re.search(r"\[(\d+(?:\.\d+)?)\]", line)
        if match:
            event(rows, "inference-session", float(match.group(1)), "log", line)
            continue
        match = re.search(r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d+(?:\.\d+)?)\]", line)
        if match:
            try:
                timestamp = dt.datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S.%f").timestamp()
            except ValueError:
                continue
            event(rows, "inference-session", timestamp, "log", line)


def parse_can_details(path, rows):
    if not path.exists():
        return

    text = path.read_text(errors="replace")
    decoder = json.JSONDecoder()
    index = 0
    while index < len(text):
        while index < len(text) and text[index].isspace():
            index += 1
        if index >= len(text):
            break
        try:
            record, index = decoder.raw_decode(text, index)
        except json.JSONDecodeError:
            break
        try:
            timestamp = float(record.pop("captured_at_unix"))
        except (ValueError, TypeError, KeyError, AttributeError):
            continue
        event(rows, "can-details", timestamp, "sample", json.dumps(record, separators=(",", ":")))


PCAP_MAGIC = {bytes.fromhex(h): (endian, divisor) for h, endian, divisor in (
    ('d4c3b2a1','<',1e6),('a1b2c3d4','>',1e6),('4d3cb2a1','<',1e9),('a1b23c4d','>',1e9))}


def parse_usb(path, rows):
    result = dict(file=path.name, bytes=path.stat().st_size, packets=0, first=None, last=None, status='ok')
    if result['bytes'] < 24:
        result['status'] = 'empty' if not result['bytes'] else 'partial_global_header'
        return result
    with path.open('rb') as stream, mmap.mmap(stream.fileno(),0,access=mmap.ACCESS_READ) as data:
        fmt = PCAP_MAGIC.get(data[:4])
        if not fmt:
            result['status'] = 'unsupported_format'
            return result
        endian, divisor = fmt
        header = struct.Struct(endian+'IIII')
        offset = 24
        while offset < len(data):
            if len(data)-offset < 16:
                result['status'] = 'partial_record_header'
                break
            sec, frac, captured, original = header.unpack_from(data,offset)
            offset += 16
            if captured > len(data)-offset:
                result.update(status='partial_payload',expected=captured,available=len(data)-offset)
                break
            if frac >= divisor or captured > original:
                result['status'] = 'invalid_record'
                break
            stamp = sec + frac/divisor
            if not result['packets']:
                result['first'] = stamp
            result['last'] = stamp
            result['packets'] += 1
            offset += captured
    for key in ('first','last'):
        event(rows,'usb-pcap',result[key],key+'-packet',path.name,file=path.name)
    return result


def host_summary(session, output):
    path = session/'host-health.jsonl'
    result = {'present':path.is_file(), 'counts':{}, 'malformed_rows':0}
    if not path.is_file():
        return result
    windows = []
    last_clock = None
    affinity = {}
    with path.open(errors='replace') as src, (output/'host_health.csv').open('w', newline='',encoding='utf-8') as dst, \
            (output/'cpu_load.csv').open('w',newline='',encoding='utf-8') as cpu_file:
        writer, cpu_writer = csv.writer(dst), csv.writer(cpu_file)
        writer.writerow(('timestamp','mono_ns','kind','data_json'))
        cpu_writer.writerow(('timestamp','interval_s','cpu','busy_pct','user_pct','system_pct','irq_pct','softirq_pct',
                             'iowait_pct','load1','load5','load15','running','blocked','clock_continuous'))
        for line in src:
            try:
                row = json.loads(line)
                kind, stamp, mono = row['kind'],row['timestamp'],row['mono_ns']
                result['counts'][kind] = result['counts'].get(kind,0)+1
                if kind != 'irq_net':
                    writer.writerow((stamp,mono,kind,line.strip()))
                if kind == 'topology':
                    affinity = row.get('irq_affinity',{})
                if kind != 'cpu_load':
                    continue
                interval = row.get('interval_s')
                continuous = bool(interval and last_clock and abs((stamp-last_clock[0])-(mono-last_clock[1])/1e9)<.1)
                previous = last_clock
                last_clock = (stamp,mono)
                load = row.get('loadavg') or [None,None,None]
                cpu = row.get('cpu',{})
                for name, usage in cpu.items():
                    u = usage or {}
                    cpu_writer.writerow((stamp,interval,name,*(u.get(k) for k in ('busy','user','system','irq','softirq','iowait')),
                                         *load,row['tasks'].get('procs_running'),row['tasks'].get('procs_blocked'),continuous))
                if continuous and previous[0] < stamp:
                    per_core = {k:v.get('busy') for k,v in cpu.items() if v and k!='cpu'}
                    windows.append((stamp,previous[0],(cpu.get('cpu') or {}).get('busy'),per_core,affinity))
            except (KeyError,ValueError,TypeError):
                result['malformed_rows'] += 1
    # Join only overlapping, clock-continuous sample intervals, not nearest points across outages.
    windows.sort(key=lambda w:w[0])
    ends = [w[0] for w in windows]
    longest = max((w[0]-w[1] for w in windows),default=0)
    with (output/'can_gaps.csv').open(newline='') as src, (output/'can_gap_cpu.csv').open('w',newline='') as dst:
        writer = csv.writer(dst)
        writer.writerow(('scope','gap_start','gap_end','sample_start','sample_end','busy_pct','per_core_busy_json','irq_affinity_json'))
        for gap in csv.DictReader(src):
            begin,end = float(gap['before_unix']),float(gap['after_unix'])
            index=bisect_left(ends,begin)
            matched = False
            while index<len(windows) and ends[index] <= end+longest:
                stop,start,busy,cores,irq=windows[index]
                if start < end and stop > begin:
                    writer.writerow((gap['scope'],begin,end,start,stop,busy,json.dumps(cores),json.dumps(irq)))
                    matched=True
                index+=1
            if not matched:
                writer.writerow((gap['scope'],begin,end,None,None,None,None,None))
    return result


def drop_outside_window(rows, manifest):
    started = manifest.get("started_at_unix")
    ended = manifest.get("ended_at_unix")
    if started is None or ended is None:
        return 0
    kept = [row for row in rows if started <= row["timestamp"] <= ended]
    dropped = len(rows) - len(kept)
    rows[:] = kept
    return dropped


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("session", type=Path)
    parser.add_argument("--out", type=Path, help="write generated reports outside the input session")
    parser.add_argument("--merged", action="store_true",
                        help="also write the legacy merged timeline.csv")
    parser.add_argument("--keep-outside", action="store_true",
                        help="keep events stamped outside the session window")
    args = parser.parse_args()
    if not args.session.is_dir():
        parser.error(f"session does not exist: {args.session}")
    manifest = read_manifest(args.session / "manifest.json")
    rows = []
    event(rows, "session", manifest.get("started_at_unix"), "start", "session")
    event(rows, "bms", manifest.get("started_at_unix"), "status", "bms.service")
    output = args.out or args.session
    output.mkdir(parents=True, exist_ok=True)
    bounds = (None,None) if args.keep_outside else (manifest.get('started_at_unix'),manifest.get('ended_at_unix'))
    coverage, frames, dropped_frames = parse_can(args.session/'can.log',rows,output,bounds,args.merged)
    ended = manifest.get('ended_at_unix')
    has_can_log = (args.session / 'can.log').is_file()
    for scope in coverage.values():
        scope['observation'] = ('missing_file' if not has_can_log else
                                'frames_seen' if scope['frames'] else 'no_frames_seen')
        last = scope['last']
        scope['until_session_end_s'] = round(ended-last, 6) if ended is not None and last is not None and ended >= last else None
    # bms-status.txt has two generations of format: the old systemctl status
    # snapshot and the new journalctl data line. Each parser only matches
    # its own format, so both can run unconditionally.
    parse_marked_snapshot(args.session / "bms-status.txt", rows, "bms")
    parse_bms(args.session / "bms-status.txt", rows)
    parse_dmesg(args.session / "dmesg-live.txt", rows)
    parse_unix_lines(args.session / "dmesg-live.txt", rows, "dmesg")
    parse_unix_lines(args.session / "hpm-uart-live.txt", rows, "hpm-uart")
    parse_unix_lines(args.session / "thermal.txt", rows, "thermal")
    parse_inference(args.session / "inference-session.txt", rows)
    parse_can_details(args.session / "can-details.jsonl", rows)
    paths = [p for p in args.session.glob('usbcan.pcap*') if re.fullmatch(r'usbcan[.]pcap[0-9]*',p.name)]
    def segment_number(p):
        return int(p.name.removeprefix('usbcan.pcap') or 0)
    pcaps = [parse_usb(p,rows) for p in sorted(paths,key=segment_number)]
    ids = sorted(set(map(segment_number,paths)))
    missing_ranges = [(a+1,b-1) for a,b in zip([-1]+ids,ids) if b-a>1]
    capture_log = args.session/'usb-capture.txt'
    stats = re.findall(r'(\d+) packets dropped by kernel',capture_log.read_text(errors='replace')) if capture_log.is_file() else []
    event(rows, "session", manifest.get("ended_at_unix"), "end", "session")
    dropped = 0 if args.keep_outside else drop_outside_window(rows, manifest)
    rows.sort(key=lambda row: row["timestamp"])

    by_dimension = {}
    for row in rows:
        by_dimension.setdefault(row["dimension"], []).append(row)

    outputs = {'can-asc':{'file':'can_frames.csv','rows':frames}} if frames else {}
    for dimension, (filename, fieldnames) in DIMENSION_OUTPUTS.items():
        if dimension == "can-asc":
            continue
        dimension_rows = by_dimension.get(dimension)
        if not dimension_rows:
            continue
        with (output / filename).open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(dimension_rows)
        outputs[dimension] = {"file": filename, "rows": len(dimension_rows)}

    summary = {
        "session": str(args.session),
        "started_at_unix": manifest.get("started_at_unix"),
        "ended_at_unix": manifest.get("ended_at_unix"),
        "event_count": len(rows) + (0 if args.merged else frames),
        "can_coverage":coverage,
        "can_gap_report":"can_gaps.csv",
        "usb_segments":pcaps,
        "usb_missing_segment_ranges":missing_ranges,
        "usb_dropped_by_kernel":int(stats[-1]) if stats else None,
        "host_health":host_summary(args.session,output),
        "dropped_outside_window": dropped + dropped_frames,
        # Carry capture completeness through analysis; a hash-valid export
        # does not prove the recorders ran without errors.
        "capture": {
            "capture_failed": manifest.get("capture_failed"),
            "candump_status": manifest.get("candump_status"),
            "usb_capture_status": manifest.get("usb_capture_status"),
            "failed_recorder": manifest.get("failed_recorder"),
            "host_health_exit_status":manifest.get("host_health_exit_status"),
            "host_health_degraded":manifest.get("host_health_degraded"),
            "asc_mode": manifest.get("asc_mode"),
            "session_complete": manifest.get("ended_at_unix") is not None,
        },
        "events_by_dimension": {dimension: sum(row["dimension"] == dimension for row in rows)
                                 for dimension in sorted({row["dimension"] for row in rows})},
        "outputs": outputs,
    }
    if args.merged:
        timeline = output / "timeline.csv"
        with timeline.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=MERGED_FIELDNAMES, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
        summary["timeline"] = str(timeline)
    if frames:
        summary["events_by_dimension"]["can-asc"] = frames
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    for dimension, info in sorted(outputs.items()):
        print(f"{info['file']}: {info['rows']} rows")
    if args.merged:
        print(f"timeline: {timeline}")
    print(f"events: {summary['event_count']}")
    print(json.dumps(summary["events_by_dimension"], sort_keys=True))


if __name__ == "__main__":
    main()
