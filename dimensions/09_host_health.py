#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0
"""Session-scoped Linux observer; the only SysRq operation supported is a backtrace."""
import argparse
from collections import deque
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time

PROC = Path('/proc')
NET = Path('/sys/class/net')
USB = ('can0', 'can1', 'can2', 'can3')
IMU = 'can_hipnuc'
PERIOD = .050
SILENCE_S = .095
WINDOW_S = 5
TAIL_SAMPLES = 3
SYSRQ_COOLDOWN_S = 60
HZ = os.sysconf('SC_CLK_TCK') if hasattr(os, 'sysconf') else 100


def text(path):
    try:
        return path.read_text(errors='replace').strip()
    except OSError:
        return None


def command(*args):
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=2)
        return p.stdout.strip() if p.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def save(path, data):
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(data, separators=(',', ':')) + '\n')
    tmp.replace(path)


def mapping():
    devices, buses = {}, set()
    for name in (*USB, IMU):
        dev = NET / name / 'device'
        if dev.exists():
            dev = dev.resolve()
            devices[name] = str(dev)
            for part in dev.parts:
                if re.fullmatch(r'usb\d+', part):
                    buses.add(part)
    return devices, buses


def interrupts(buses, known=None):
    lines = (text(PROC / 'interrupts') or '').splitlines()
    cpus = lines[0].split() if lines else []
    selected = {}
    for line in lines[1:]:
        raw_key, _, rest = line.partition(':')
        key = raw_key.strip()
        if known is not None and key not in known:
            continue
        fields = rest.split()
        if not key.isdigit() or len(fields) <= len(cpus):
            continue
        label = ' '.join(fields[len(cpus):])
        if IMU in label or any(bus in label.replace(':',' ').split() for bus in buses):
            selected[key] = {'label': label, 'counts': list(map(int, fields[:len(cpus)]))}
    return cpus, selected


def sample(buses, known=None):
    cpus, irq = interrupts(buses, known)
    soft = {}
    for line in (text(PROC / 'softirqs') or '').splitlines():
        key, _, counts = line.partition(':')
        if key.strip() and counts.strip():
            soft[key.strip()] = list(map(int, counts.split()))
    net = {}
    for line in (text(PROC / 'net/dev') or '').splitlines():
        name, _, counts = line.partition(':')
        if name.strip() in (*USB, IMU):
            fields = counts.split()
            net[name.strip()] = [int(fields[1]), int(fields[9])]
    return dict(cpus=cpus, irq=irq, softirq=soft,
                softirq_total=[sum(col) for col in zip(*soft.values())], packets=net)


def cpu_stat():
    ticks, tasks = {}, {}
    for line in (text(PROC / 'stat') or '').splitlines():
        key, *fields = line.split()
        if re.fullmatch(r'cpu\d*', key):
            ticks[key] = tuple(map(int, fields[:8]))  # guest already included in user/nice
        elif key in ('procs_running', 'procs_blocked'):
            tasks[key] = int(fields[0])
    return ticks, tasks


def cpu_percent(old, new):
    if old is None or len(old) != len(new):
        return None
    d = [b-a for a, b in zip(old, new)]
    total = sum(d)
    if min(d) < 0 or total == 0 or len(d) < 8:
        return None
    def pct(value):
        return round(100 * value / total, 2)
    return dict(busy=pct(sum(d[:3])+d[5]+d[6]), user=pct(d[0]+d[1]), system=pct(d[2]),
                irq=pct(d[5]), softirq=pct(d[6]), iowait=pct(d[4]), steal=pct(d[7]))


def task(path):
    raw = text(path / 'stat')
    if not raw:
        return None
    name, _, suffix = raw.rpartition(') ')
    f = suffix.split()
    if len(f) < 40:
        return None
    return dict(pid=int(path.name), comm=name.partition('(')[2], state=f[0], ticks=int(f[11])+int(f[12]),
                start=int(f[19]), cpu=int(f[36]), rtprio=int(f[37]), policy=int(f[38]))


def pressure():
    return {k: text(PROC / 'pressure' / k) for k in ('cpu','io','memory')}


class Activity:
    """One cumulative counter, on one interface, in one direction."""
    def __init__(self):
        self.reset()

    def reset(self):
        self.prev = None
        self.hits = 0
        self.armed = False
        self.last = None
        self.silent = False

    def update(self, value, now, gap=False):
        transition = None
        if gap or value is None or (self.prev is not None and value < self.prev):
            if self.silent:
                transition = 'sampler_gap' if gap else ('missing' if value is None else 'counter_reset')
            self.reset()
        elif self.prev is not None and value > self.prev:
            if self.silent:
                transition = 'recovered'
                self.silent = False
            self.hits += 1
            self.armed |= self.hits >= 3
            self.last = now
        else:
            self.hits = 0
            if self.armed and now-self.last >= SILENCE_S:
                transition = 'silent'
                self.armed, self.silent = False, True
        self.prev = value
        return transition


def process_table(previous, ns):
    rows, current = [], {}
    for p in PROC.glob('[0-9]*'):
        t = task(p)
        if not t:
            continue
        key = (t['pid'], t['start'])
        old = previous.get(key)
        t['cpu_pct_one_core'] = (round((t['ticks']-old[0])*1e11/(HZ*(ns-old[1])), 2)
                                 if old and ns > old[1] and t['ticks'] >= old[0] else None)
        current[key] = (t['ticks'], ns)
        rows.append(t)
    return dict(mono_ns=ns, timestamp=time.time(), processes=rows), current


def sysrq_backtrace():
    result = dict(timestamp=time.time(), mono_ns=time.monotonic_ns())
    try:
        with (PROC/'sysrq-trigger').open('wb', buffering=0) as f:
            f.write(b'l')
        result['status'] = 'requested'  # proof of output belongs in dmesg-live.txt
    except OSError as exc:
        result.update(status='error', error=str(exc))
    return result


class LinkStates:
    """Consume complete records from the existing ip snapshot, including multiline JSON."""
    def __init__(self, path):
        self.path, self.offset, self.pending, self.previous = path, 0, '', {}

    def update(self):
        try:
            with self.path.open('rb') as f:
                f.seek(self.offset)
                data = f.read(256*1024)
                self.offset = f.tell()
        except OSError:
            return []
        self.pending += data.decode(errors='replace')
        reasons = []
        while self.pending.strip():
            self.pending = self.pending.lstrip()
            try:
                record, end = json.JSONDecoder().raw_decode(self.pending)
            except ValueError:
                if len(self.pending) > 256*1024:
                    self.pending = ''
                break
            self.pending = self.pending[end:]
            states = {v['ifname']: v.get('linkinfo', {}).get('info_data', {}).get('state')
                      for v in record.get('interfaces', []) if v.get('ifname') in (*USB, IMU)}
            reasons.extend(k+':'+s for k,s in states.items() if s in ('ERROR-PASSIVE','BUS-OFF')
                           and self.previous.get(k) != s)
            self.previous = states
        return reasons


def snapshot(irq, nodes):
    tasks = []
    candidates = (p for leader in PROC.glob('[0-9]*') for p in (leader/'task').glob('[0-9]*'))
    for path in candidates:
        t = task(path)
        if not t:
            continue
        if (t['state'] == 'D' or int(path.parent.parent.name) in nodes or t['comm'].startswith('ksoftirqd/') or
                any(t['comm'].startswith('irq-'+i+'-') for i in irq)):
            t.update(wchan=text(path/'wchan'), stack=text(path/'stack'))
            tasks.append(t)
            if len(tasks) >= 32:
                break
    return dict(tasks=tasks, pressure=pressure(), loadavg=text(PROC/'loadavg'),
                interrupts=text(PROC/'interrupts'), softirqs=text(PROC/'softirqs'))


class StallWindow:
    """One trigger batch; other interfaces can open independent, overlapping windows."""
    def __init__(self, dest, reasons, directions, row, history, proc_history):
        self.dest, self.worker = dest, None
        self.deadline = row['mono_ns'] + WINDOW_S*10**9
        self.remaining = set(directions)
        self.tail = TAIL_SAMPLES
        self.next_save = row['mono_ns'] + 10**9
        self.record = dict(reasons=reasons, trigger=row['timestamp'], mono_ns=row['mono_ns'],
                           before=list(history), after=[], post_complete=False, window_end=None,
                           directions=directions, threads_file=None, snapshot_status=None)
        proc_file = dest.with_suffix('.processes.json')
        save(proc_file, {'tables':list(proc_history), 'scope':'all readable process leaders'})
        self.record['processes_file'] = proc_file.name

    def advance(self, row, transitions):
        r = self.record
        ns = row['mono_ns']
        if not r['window_end']:
            r['after'].append(row)
            ended = self.remaining.intersection(transitions)
            for key in ended:
                r['directions'][key].update(outcome=transitions[key], end_mono_ns=ns,
                                            end_timestamp=row['timestamp'])
            self.remaining -= ended
            if not self.remaining and r['directions'] and not ended:
                self.tail -= 1
            if self.tail == 0 or ns >= self.deadline:
                r['window_end'] = 'tail_complete' if self.tail == 0 else 'time_limit'
                for key in self.remaining:
                    r['directions'][key]['outcome'] = 'not_recovered_in_window'
                r['post_complete'] = True
            if ended or r['window_end'] or ns >= self.next_save:
                save(self.dest, r)
                self.next_save = ns + 10**9
        if self.worker and self.worker.poll() is None and ns > self.deadline + 3*10**9:
            self.worker.kill()
        if r['window_end'] and (not self.worker or self.worker.poll() is not None):
            self.finish()
            return True
        return False

    def finish(self):
        if self.worker:
            try:
                self.worker.wait(timeout=1)
            except subprocess.TimeoutExpired:
                self.worker.kill()
                self.worker.wait()
            self.record['snapshot_status'] = self.worker.returncode
        if not self.record['window_end']:
            self.record['window_end'] = 'session_end'
            for key in self.remaining:
                self.record['directions'][key]['outcome'] = 'session_end'
        save(self.dest, self.record)


def environment():
    return dict(kernel=os.uname().release, cmdline=text(PROC/'cmdline'),
                boot_id=text(PROC/'sys/kernel/random/boot_id'), cpu_affinity=sorted(os.sched_getaffinity(0)),
                timezone=time.tzname, ntp_synchronized=command('timedatectl','show','-p','NTPSynchronized','--value'),
                packages=command('dpkg-query','-W','roboparty-analyze','roboparty-bms','roboparty-inference'),
                clocks={'timestamp':'Unix seconds (CLOCK_REALTIME)', 'mono_ns':'CLOCK_MONOTONIC',
                        'dmesg-live.txt':'ISO 8601 with offset', 'inference-session.txt':'application output'},
                packets_order=['rx','tx'], fast_period_s=PERIOD, silence_threshold_s=SILENCE_S, load_period_s=1,
                settings={k:os.environ.get(k) for k in ('SESSION_MAX_DURATION_SECS','SESSION_MAX_SIZE_MB',
                       'SESSION_RETENTION_DAYS','SESSION_MAX_TOTAL_MB','FILE_SIZE_MB','FILE_COUNT',
                       'HOST_HEALTH_SYSRQ_L')}, process_history_tables=4, stall_window_cap_s=WINDOW_S,
                sysrq_l_enabled=os.environ.get('HOST_HEALTH_SYSRQ_L','yes')=='yes')


def run(out, source, seconds=None):
    running = True
    def stop(*_):
        nonlocal running
        running = False
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, stop)
    meta = environment()
    latest_map = None
    known_irq = None
    meta_path = out/'host-environment.json'
    start = time.monotonic()
    next_fast = next_slow = next_mapping = start
    prev_ns = prev_state = last_snap = last_sysrq = last_slow_ns = None
    previous_ticks, previous_proc, node_pids = {}, {}, set()
    history, proc_history, windows = deque(maxlen=10), deque(maxlen=4), []
    episode = {}
    devices, buses = mapping()
    gates = {(name, i):Activity() for name in (*USB,IMU) for i in (0,1)}
    links = LinkStates(out/'can-details.jsonl')
    seq = 0
    meta['start'] = [time.time_ns(), time.monotonic_ns()]
    with (out/'host-health.jsonl').open('x', buffering=65536) as log:
        def emit(kind, data):
            row = dict(kind=kind, seq=seq, timestamp=time.time(), mono_ns=time.monotonic_ns(), **data)
            log.write(json.dumps(row, separators=(',', ':'))+'\n')
            return row
        while running and (seconds is None or time.monotonic()-start < seconds):
            now = time.monotonic()
            if now < next_fast:
                time.sleep(min(PERIOD, next_fast-now))
                continue
            skipped = int((now-next_fast)/PERIOD)
            next_fast += (skipped+1)*PERIOD
            ns = time.monotonic_ns()
            elapsed = (ns-prev_ns)/1e9 if prev_ns is not None else None
            prev_ns = ns
            if now >= next_mapping:
                devices, buses = mapping()
                next_mapping = now + 5
                known_irq = None
            fast = sample(buses, known_irq)
            known_irq = set(fast['irq'])
            labels = {k:v['label'] for k,v in fast['irq'].items()}
            topo = (devices,labels)
            if topo != latest_map:
                latest_map = topo
                meta.update(interfaces=devices, irq_labels=labels, irq_affinity={k:text(PROC/'irq'/k/'effective_affinity_list') for k in labels})
                save(meta_path, meta)
                emit('topology', dict(interfaces=devices, irq_labels=labels, irq_affinity=meta['irq_affinity']))
            row = emit('irq_net', dict(**fast, interval_s=elapsed, skipped=skipped,
                                       read_us=(time.monotonic_ns()-ns)//1000))
            history.append(row)
            reasons, directions, transitions = [], {}, {}
            gap = elapsed is not None and elapsed >= 2*PERIOD
            if gap:
                emit('sampler_gap', {'interval_s':elapsed})
            for (name, i), gate in gates.items():
                key = name + ('_rx','_tx')[i]
                value = fast['packets'][name][i] if name in fast['packets'] else None
                change = gate.update(value, now, gap)
                if change == 'silent':
                    reasons.append(key+'_silent')
                    directions[key] = dict(last_progress_mono_ns=round(gate.last*1e9),
                                           counter=value, outcome='pending')
                elif change:
                    transitions[key] = change
                    emit('direction_end', dict(direction=key, outcome=change, counter=value,
                                               file=episode.pop(key, None)))
            for window in windows[:]:
                if window.advance(row, transitions):
                    windows.remove(window)
            if now >= next_slow:
                reasons += links.update()
                ticks, fields = cpu_stat()
                table, previous_proc = process_table(previous_proc, time.monotonic_ns())
                proc_history.append(table)
                tracked = [t for t in table['processes'] if t['pid']==os.getpid() or t['comm']=='inference_node']
                node_ids = sorted((t['pid'],t['start']) for t in tracked if t['pid'] != os.getpid())
                node_pids = {p for p,_ in node_ids}
                state = (node_ids, text(source) if source is not None else None)
                if state != prev_state:
                    emit('inference_state', dict(nodes=node_ids, output_source=state[1] or 'unknown'))
                    prev_state = state
                load = text(PROC/'loadavg')
                emit('cpu_load', dict(cpu={k:cpu_percent(previous_ticks.get(k),v) for k,v in ticks.items()},
                                      loadavg=list(map(float,load.split()[:3])) if load else None,
                                      tasks=fields, processes=tracked, psi=pressure(),
                                      interval_s=(ns-last_slow_ns)/1e9 if last_slow_ns is not None else None))
                last_slow_ns, previous_ticks = ns, ticks
                next_slow = now+1
                log.flush()
            if reasons:
                dest = out/'stall'/f'{ns}.json'
                dest.parent.mkdir(exist_ok=True)
                w = StallWindow(dest, reasons, directions, row, history, proc_history)
                issue_l = meta['sysrq_l_enabled'] and (last_sysrq is None or now-last_sysrq >= SYSRQ_COOLDOWN_S)
                w.record['sysrq_l'] = 'disabled' if not meta['sysrq_l_enabled'] else 'cooldown'
                if issue_l or last_snap is None or now-last_snap >= 10:
                    last_snap = now
                    details = dest.with_suffix('.threads.json')
                    w.record['threads_file'] = details.name
                    args = [sys.executable, '-B', __file__, str(out), '--snapshot', str(details),
                            json.dumps(list(labels)), json.dumps(list(node_pids))]
                    if issue_l:
                        last_sysrq = now
                        args.append('--sysrq-l')
                        w.record['sysrq_l'] = 'see_threads_file'
                    try:
                        w.worker = subprocess.Popen(args)
                    except OSError as exc:
                        w.record['worker_error'] = str(exc)
                else:
                    w.record['thread_snapshot'] = 'cooldown'
                save(dest, w.record)
                windows.append(w)
                episode.update({k:'stall/'+dest.name for k in directions})
                emit('stall', dict(reasons=reasons, file='stall/'+dest.name))
                log.flush()
            seq += 1
        for window in windows:
            window.finish()
        emit('observer_stop', {})
    meta['end'] = [time.time_ns(),time.monotonic_ns()]
    save(meta_path,meta)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('session', type=Path)
    parser.add_argument('--source-state', type=Path)
    parser.add_argument('--seconds', type=float, help='bounded standalone observation')
    parser.add_argument('--snapshot', nargs=3, metavar=('FILE','IRQS','PIDS'), help=argparse.SUPPRESS)
    parser.add_argument('--sysrq-l', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.snapshot:
        path, irqs, pids = args.snapshot
        wall, mono = time.time(), time.monotonic_ns()
        request = sysrq_backtrace() if args.sysrq_l else {'status':'not_requested'}
        save(Path(path), dict(timestamp=wall, mono_ns=mono, sysrq_l=request,
                              **snapshot(json.loads(irqs), json.loads(pids))))
    else:
        run(args.session, args.source_state, args.seconds)
