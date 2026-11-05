#!/usr/bin/env python3
"""Stage an identity-pinned consecutive Drive subset and retain diagnostic runs.

Run inside the deployed RunPod image, with its existing Drive environment:
python3 scripts/diagnose-runpod.py --output /tmp/openreef-diagnostic-200
No Drive files are modified. A failed run is intentionally retained locally.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import sqlite3
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from worker import handler as worker  # noqa: E402


def subset(items, count, start):
    """Keep overlap by selecting a consecutive block after content deduplication."""
    images = sorted((x for x in items if Path(x['name']).suffix.lower() in {'.jpg', '.jpeg', '.png'}),
                    key=lambda x: (x['name'].casefold(), x['id']))
    unique, seen, names = [], set(), set()
    for item in images:
        checksum = item.get('md5Checksum')
        identity = (checksum, int(item.get('size') or 0)) if checksum else ('id', item['id'])
        if identity in seen:
            continue
        seen.add(identity)
        name = worker.safe_file_name(item['name'])
        original = Path(name)
        suffix = 2
        while name.casefold() in names:
            name = f'{original.stem[:140]}__{suffix}{original.suffix}'
            suffix += 1
        names.add(name.casefold())
        unique.append({'name': name, 'size': int(item.get('size') or 0),
                       'sourceId': item['id'], 'sourceName': item['name'], 'sourceMd5': checksum or ''})
    selected = unique[start:start + count]
    if len(selected) != count:
        raise ValueError(f'Need {count} unique images from offset {start}; found {len(selected)}')
    return selected


def capture(command):
    try:
        return subprocess.run(command, capture_output=True, text=True, timeout=15).stdout[-16000:]
    except (OSError, subprocess.TimeoutExpired) as error:
        return str(error)


def native_threads():
    samples = {}
    for process in Path('/proc').glob('[0-9]*'):
        try:
            name = (process / 'comm').read_text().strip()
            if name.lower() not in {'colmap', 'densifypointclo', 'densifypointclou', 'reconstructmesh', 'texturemesh'}:
                continue
            samples[process.name] = {'name': name,
                'command': (process / 'cmdline').read_text().rstrip('\0').split('\0'),
                'threads': {
                task.name: (task / 'stat').read_text()
                for task in (process / 'task').iterdir()}}
        except (OSError, ValueError):
            continue
    return samples


def measure(command, folder, cwd=None):
    folder.mkdir(parents=True, exist_ok=True)
    start = time.monotonic()
    with (folder / 'pipeline.log').open('w') as log, (folder / 'resources.jsonl').open('w') as samples:
        log.write('Command: ' + json.dumps(command) + '\n')
        log.flush()
        process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, cwd=cwd)
        while process.poll() is None:
            samples.write(json.dumps({'elapsed': round(time.monotonic() - start, 2),
                'processes': capture(['ps', '-eo', 'pid,ppid,pcpu,rss,nlwp,args']),
                'nativeThreads': native_threads(),
                'gpu': capture(['nvidia-smi', '--query-compute-apps=pid,used_memory', '--format=csv,noheader'])}) + '\n')
            samples.flush()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                pass
    result = {'command': command, 'seconds': round(time.monotonic() - start, 3), 'returnCode': process.returncode}
    events = Path('/sys/fs/cgroup/memory.events')
    if events.is_file():
        result['memoryEventsAfter'] = events.read_text()
    (folder / 'result.json').write_text(json.dumps(result, indent=2))
    print(json.dumps(result), flush=True)
    return result


def mapper_command(log):
    for line in log.splitlines():
        line = line.strip()
        if line.startswith('$ '):
            parts = shlex.split(line[2:])
            if len(parts) > 1 and Path(parts[0]).name == 'colmap' and parts[1] == 'mapper':
                return parts
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', default='orbicella-rockbottom-03-11-26')
    parser.add_argument('--count', type=int, default=200)
    parser.add_argument('--start', type=int, default=0, help='Zero-based consecutive image offset')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--cores', type=int, default=32, help='Reproduce the original thread limit')
    parser.add_argument('--compare-sparse', action='store_true', help='Replay sparse mapping at 8 and 32 threads using the same database')
    args = parser.parse_args()
    if args.count < 2 or args.start < 0 or args.cores < 1:
        parser.error('count >= 2, start >= 0 and cores >= 1 required')
    root = args.output.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=False)
    os.environ['OPENREEF_MAX_CORES'] = str(args.cores)
    hardware = {'cpuCount': os.cpu_count(), 'platform': sys.platform,
                'affinity': sorted(os.sched_getaffinity(0)) if hasattr(os, 'sched_getaffinity') else None,
                'gpu': capture(['nvidia-smi', '-q']), 'disk': capture(['df', '-h', str(root), '/runpod-volume']),
                'versions': json.loads((Path(__file__).resolve().parents[1] / 'versions.json').read_text())}
    for name in ('cpu.max', 'cpu.stat', 'cpuset.cpus.effective', 'memory.max', 'memory.events'):
        path = Path('/sys/fs/cgroup') / name
        if path.is_file():
            hardware[name] = path.read_text()
    (root / 'hardware.json').write_text(json.dumps(hardware, indent=2))
    storage = worker.GoogleDriveClient()
    source = args.source
    items = storage.list_files(source)
    if not any(Path(x['name']).suffix.lower() in {'.jpg', '.jpeg', '.png'} for x in items):
        source += '/images'
        items = storage.list_files(source)
    files = subset(items, args.count, args.start)
    (root / 'subset.json').write_text(json.dumps({'source': source, 'files': files}, indent=2))
    dataset = root / 'dataset'
    began = time.monotonic()
    storage.download_folder(source, dataset / 'images', files)
    # Verify bytes, rather than trusting only a cached filename/size.
    for item in files:
        path = dataset / 'images' / item['name']
        if path.stat().st_size != item['size']:
            raise RuntimeError(f"Size mismatch: {item['name']}")
        if item['sourceMd5']:
            digest = hashlib.md5()  # noqa: S324 - Drive content checksum, not security
            with path.open('rb') as stream:
                for block in iter(lambda: stream.read(8 * 1024**2), b''):
                    digest.update(block)
            if digest.hexdigest() != item['sourceMd5']:
                raise RuntimeError(f"Checksum mismatch: {item['name']}")
    (root / 'staging.json').write_text(json.dumps({'seconds': time.monotonic() - began, 'images': len(files),
                                               'bytes': sum(x['size'] for x in files)}, indent=2))
    # Full output mode reproduces the failed original+compact dense workload.
    measure(worker.pipeline_command(dataset, full_project=True), root / 'baseline')
    native = mapper_command((root / 'baseline/pipeline.log').read_text(errors='replace'))
    if not native:
        for line in (root / 'baseline/resources.jsonl').read_text().splitlines():
            for sample in json.loads(line).get('nativeThreads', {}).values():
                command = sample.get('command', [])
                if len(command) > 1 and Path(command[0]).name == 'colmap' and command[1] == 'mapper':
                    native = command
                    break
            if native:
                break
    if args.compare_sparse and native:
        for cores in (8, 32):
            folder = root / f'sparse-{cores}'
            folder.mkdir()
            database = folder / 'database.db'
            with sqlite3.connect(str(dataset / 'colmap/database.db')) as source_db, sqlite3.connect(str(database)) as target_db:
                source_db.backup(target_db)
            command = list(native)
            output = folder / 'model'
            output.mkdir()
            for flag, value in (('--database_path', str(database)), ('--output_path', str(output)), ('--Mapper.num_threads', str(cores))):
                if flag in command:
                    command[command.index(flag) + 1] = value
                else:
                    command.extend([flag, value])
            measure(command, folder, cwd=dataset)
            for model in sorted(output.iterdir()):
                if model.is_dir():
                    (folder / f'model-{model.name}.txt').write_text(capture([native[0], 'model_analyzer', '--path', str(model)]))
    elif args.compare_sparse:
        print('No native mapper command captured; sparse comparisons skipped. Inspect baseline log.', flush=True)
    print(f'Diagnostics retained at {root}; no source Drive files changed.', flush=True)


if __name__ == '__main__':
    main()
