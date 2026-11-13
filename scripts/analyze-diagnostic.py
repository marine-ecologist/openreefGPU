#!/usr/bin/env python3
"""Summarize retained diagnostic results without running reconstruction."""
import argparse
import io
import json
import os
import re
import struct
import subprocess
import tarfile
from datetime import datetime
from pathlib import Path


def read_json(path):
    return json.loads(path.read_text()) if path.is_file() else None


def thread_ticks(raw):
    fields = raw.rsplit(') ', 1)[1].split()
    return int(fields[11]) + int(fields[12]), fields[19]


def thread_summary(path):
    previous = {}
    totals = {'sampledSeconds': 0, 'cpuSeconds': 0, 'mainCpuSeconds': 0,
              'intervals': 0, 'singleCoreIntervals': 0, 'peakSampledCores': 0}
    ticks_per_second = os.sysconf('SC_CLK_TCK')
    if not path.is_file():
        return {'available': False}
    with path.open() as stream:
        for line in stream:
            sample = json.loads(line)
            now = float(sample['elapsed'])
            for pid, process in sample.get('nativeThreads', {}).items():
                command = process.get('command', [])
                if len(command) < 2 or command[1] != 'mapper':
                    continue
                current = {tid: thread_ticks(stat) for tid, stat in process['threads'].items()}
                if pid in previous:
                    before, old = previous[pid]
                    seconds = now - before
                    if seconds > 0 and pid in current and pid in old:
                        if current[pid][1] == old[pid][1]:
                            delta = {tid: max(0, value[0] - old[tid][0])
                                     for tid, value in current.items()
                                     if tid in old and value[1] == old[tid][1]}
                            cpu = sum(delta.values()) / ticks_per_second
                            main = delta.get(pid, 0) / ticks_per_second
                            cores = cpu / seconds
                            totals['sampledSeconds'] += seconds
                            totals['cpuSeconds'] += cpu
                            totals['mainCpuSeconds'] += main
                            totals['intervals'] += 1
                            totals['singleCoreIntervals'] += int(0.75 <= cores <= 1.25)
                            totals['peakSampledCores'] = max(totals['peakSampledCores'], cores)
                previous[pid] = now, current
    seconds = totals['sampledSeconds']
    cpu = totals['cpuSeconds']
    totals['meanSampledCores'] = cpu / seconds if seconds else None
    totals['mainThreadCpuFraction'] = totals['mainCpuSeconds'] / cpu if cpu else None
    totals['note'] = ('Sampled surviving threads only; excludes CPU spent by threads that '
                      'start or exit between snapshots. Does not identify native functions.')
    return totals


def log_summary(path):
    if not path.is_file():
        return {'available': False}
    lines = path.read_text(errors='replace').splitlines()
    registrations, gaps, global_windows = [], [], []
    global_start = None
    last_registration = None
    solver = []
    last_timestamp = None
    for line in lines:
        stamp = re.match(r'^[IWEF](\d{8}) (\d{2}:\d{2}:\d{2}\.\d+)', line)
        when = datetime.strptime(''.join(stamp.groups()), '%Y%m%d%H:%M:%S.%f') if stamp else None
        if when:
            last_timestamp = when
        if re.search(r'ceres|caspar|CUDA|cuDSS|solver', line, re.I):
            solver.append(line[-1000:])
        if when and 'Global bundle adjustment' in line and global_start is None:
            global_start = when
        if when and 'Registering image #' in line:
            registered = re.search(r'num_reg_frames=(\d+)', line)
            record = {'timestamp': when.isoformat(), 'registeredBefore':
                      int(registered.group(1)) if registered else None}
            registrations.append(record)
            if last_registration:
                gaps.append({'seconds': (when - last_registration).total_seconds(),
                             'nextRegistration': record,
                             'includesGlobalWindow': global_start is not None})
            if global_start:
                global_windows.append((when - global_start).total_seconds())
                global_start = None
            last_registration = when
    if global_start and last_timestamp:
        global_windows.append((last_timestamp - global_start).total_seconds())
    return {'registrationAttempts': len(registrations),
            'lastRegistration': registrations[-1] if registrations else None,
            'retriangulationGlobalWindowSeconds': sum(global_windows),
            'globalWindowCount': len(global_windows),
            'largestRegistrationGaps': sorted(gaps, key=lambda x: x['seconds'], reverse=True)[:8],
            'solverEvidence': solver[:20] + solver[-10:],
            'logTail': lines[-30:],
            'note': 'Global windows include retriangulation and intervening work; not BA-only timings.'}


def models(root):
    results = []
    for model in sorted(root.glob('*')) if root.is_dir() else []:
        if not model.is_dir():
            continue
        record = {'model': model.name}
        for name, key in [('images.bin', 'registeredImages'), ('points3D.bin', 'points3D')]:
            path = model / name
            if path.is_file():
                with path.open('rb') as stream:
                    record[key] = struct.unpack('<Q', stream.read(8))[0]
        try:
            process = subprocess.run(['colmap', 'model_analyzer', '--path', str(model)],
                                     capture_output=True, text=True, timeout=30)
            record['analyzer'] = (process.stdout + process.stderr)[-12000:]
            record['analyzerReturnCode'] = process.returncode
        except (OSError, subprocess.TimeoutExpired) as error:
            record['analyzerError'] = str(error)
        results.append(record)
    return results


def summarize(root):
    if not root.is_dir():
        raise FileNotFoundError(f'Diagnostic results do not exist: {root}')
    report = {'source': str(root),
              'runtime': {key: os.environ.get(key) for key in (
                  'OPENREEF_VERSION', 'OPENREEF_GPU_VERSION', 'OPENREEF_BA_BACKEND',
                  'OPENREEF_CERES_USE_GPU', 'OPENREEF_MAX_CORES')},
              'hardware': read_json(root / 'hardware.json'),
              'staging': read_json(root / 'staging.json'), 'runs': {}}
    for name in ('baseline', 'sparse-8', 'sparse-32'):
        folder = root / name
        if not folder.is_dir():
            continue
        model_root = root / 'dataset/colmap/sparse' if name == 'baseline' else folder / 'model'
        report['runs'][name] = {'result': read_json(folder / 'result.json'),
                               'threads': thread_summary(folder / 'resources.jsonl'),
                               'log': log_summary(folder / 'pipeline.log'),
                               'models': models(model_root)}
    report['limitations'] = [
        'One ordered 8-thread/32-thread trial; cache effects and host contention are uncontrolled.',
        'Successful 200-image dense reconstruction does not exclude a scale-dependent full-run crash.',
        'Thread samples and stage logs cannot establish which native function consumed the CPU.',
    ]
    return report


def evidence_archive(root):
    """Retain raw logs/samples, excluding photographs and reconstruction intermediates."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w:gz') as archive:
        candidates = [root / name for name in ('hardware.json', 'staging.json', 'subset.json')]
        for name in ('baseline', 'sparse-8', 'sparse-32'):
            folder = root / name
            candidates += [folder / file for file in (
                'result.json', 'pipeline.log', 'resources.jsonl')]
            candidates += list(folder.glob('model-*.txt'))
        for path in candidates:
            if path.is_file() and not path.is_symlink():
                archive.add(path, arcname=str(path.relative_to(root)), recursive=False)
    return buffer.getvalue()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('results', type=Path)
    args = parser.parse_args()
    print(json.dumps(summarize(args.results), indent=2))


if __name__ == '__main__':
    main()
