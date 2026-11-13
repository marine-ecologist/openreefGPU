#!/usr/bin/env python3
"""Sparse-only current/reference profiling with periodic durable Drive reports."""
import argparse
import hashlib
import importlib.util
import json
import os
import shutil
import signal
import sqlite3
import struct
import subprocess
import sys
import tarfile
import threading
import time
import traceback
import uuid
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from worker import handler as worker

PROFILE_REVISION = 'source-id-r3'


def module(name, filename):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(filename))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def capture(command):
    try:
        process = subprocess.run(command, capture_output=True, text=True, timeout=20)
        return {'code': process.returncode, 'text': (process.stdout + process.stderr)[-30000:]}
    except (OSError, subprocess.TimeoutExpired) as error:
        return {'error': str(error)}


def digest(path, algorithm='sha256'):
    value = hashlib.new(algorithm)
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(4 * 1024**2), b''):
            value.update(block)
    return value.hexdigest()


def database_stats(path):
    with sqlite3.connect(f'file:{path}?mode=ro', uri=True) as database:
        return {'images': database.execute('SELECT COUNT(*) FROM images').fetchone()[0],
                'keypoints': database.execute('SELECT SUM(rows) FROM keypoints').fetchone()[0],
                'verifiedPairs': database.execute('SELECT COUNT(*) FROM two_view_geometries WHERE rows>0').fetchone()[0],
                'verifiedCorrespondences': database.execute('SELECT SUM(rows) FROM two_view_geometries').fetchone()[0]}


def point_stats(path):
    result = {'points': 0, 'observations': 0, 'maxTrackLength': 0, 'meanReprojectionError': None}
    if not path.is_file():
        return result
    error_sum = 0
    with path.open('rb') as stream:
        count = struct.unpack('<Q', stream.read(8))[0]
        for _ in range(count):
            record = struct.unpack('<QdddBBBdQ', stream.read(51))
            error_sum += record[-2]
            length = record[-1]
            result['observations'] += length
            result['maxTrackLength'] = max(result['maxTrackLength'], length)
            stream.seek(8 * length, 1)
    result['points'] = count
    result['meanReprojectionError'] = error_sum / count if count else None
    return result


def pin_source_folder(storage, path, folder_id, root_id):
    """Bind the current survey to its supplied Drive ID after validating its parent."""
    if storage.root_folder_id != root_id:
        raise RuntimeError('Saved Drive root differs from the supplied OpenReef root')
    metadata = storage.request(
        'GET', '/files/' + urllib.parse.quote(folder_id, safe='')
        + '?fields=id,name,mimeType,parents',
    )
    if (metadata.get('id') != folder_id
            or metadata.get('mimeType') != storage.folder_mime_type
            or root_id not in metadata.get('parents', [])
            or metadata.get('name') != path.strip('/').split('/')[-1]):
        raise RuntimeError('The supplied survey folder ID/name/parent does not match the configured source')
    original = storage._resolve_folder
    prefix = path.strip('/')

    def resolve(value):
        normalized = value.strip('/')
        if normalized == prefix:
            return folder_id
        if normalized.startswith(prefix + '/'):
            parent = folder_id
            for name in normalized[len(prefix) + 1:].split('/'):
                item = storage._find_child(parent, name, storage.folder_mime_type)
                if item is None:
                    raise RuntimeError(f'Google Drive folder not found: {name}')
                parent = item['id']
            return parent
        return original(value)

    storage._resolve_folder = resolve
    return metadata


def ensure_report_folder(storage, path):
    """Create each destination folder before using the worker's resolve-only writes."""
    parts = path.split('/')
    if any(not part or part in {'.', '..'} for part in parts):
        raise ValueError('Invalid diagnostic folder path')
    parent = storage.root_folder_id
    for name in parts:
        item = storage._find_child(parent, name, storage.folder_mime_type)
        if item is None:
            item = storage.request(
                'POST', '/files?fields=id,name,mimeType',
                data=json.dumps({'name': name, 'mimeType': storage.folder_mime_type,
                                 'parents': [parent]}).encode(),
                headers={'Content-Type': 'application/json; charset=UTF-8'},
            )
        if not isinstance(item, dict) or not item.get('id'):
            raise RuntimeError(f'Google Drive did not return a folder ID for {name}')
        parent = item['id']
    return parent


def stage_checkpoint(storage, root, cloud, label):
    """Persist completed reports; exclude source images and feature databases."""
    archive = root / 'reports.tar.gz'
    omitted = []
    with tarfile.open(archive, 'w:gz') as bundle:
        for path in sorted(root.rglob('*')):
            if not path.is_file() or path.is_symlink() or path == archive:
                continue
            if path.suffix not in {'.json', '.jsonl', '.log', '.txt', '.data'}:
                continue
            if path.stat().st_size > 32 * 1024**2:
                omitted.append(str(path.relative_to(root)))
                continue
            bundle.add(path, arcname=str(path.relative_to(root)), recursive=False)
    storage.put_file(f'{cloud}/reports.tar.gz', archive)
    storage.put_bytes(f'{cloud}/checkpoint.json', json.dumps({
        'stage': label, 'updatedAt': worker.utc_now(), 'omittedLargeFiles': omitted,
    }).encode(), 'application/json')
    print(f'Durable Drive checkpoint saved: {cloud} ({label})', flush=True)


def measured(command, folder, sampler, storage, cloud, root, profile=False):
    folder.mkdir(parents=True)
    (folder / 'command.json').write_text(json.dumps(command, indent=2))
    start = time.monotonic()
    before = {name: (Path('/sys/fs/cgroup') / name).read_text()
              for name in ('cpu.stat', 'memory.events') if (Path('/sys/fs/cgroup') / name).is_file()}
    latest = ['']
    profilers = []
    handles = []
    with (folder / 'pipeline.log').open('w') as log, (folder / 'resources.jsonl').open('w') as resources:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   text=True, bufsize=1, start_new_session=True)

        def drain():
            for line in process.stdout:
                log.write(line)
                log.flush()
                latest[0] = line.strip()
        reader = threading.Thread(target=drain, daemon=True)
        reader.start()
        if profile and shutil.which('perf'):
            for name, arguments in [
                ('perf-stat.log', ['stat', '-I', '1000', '-e',
                 'task-clock,cycles,instructions,context-switches,cpu-migrations,page-faults']),
                ('perf-record.log', ['record', '-F', '49', '-e', 'cpu-clock', '-g',
                 '-o', str(folder / 'perf.data')]),
            ]:
                output = (folder / name).open('w')
                handles.append(output)
                profilers.append(subprocess.Popen(['perf', *arguments, '-p', str(process.pid)],
                                                  stdout=output, stderr=subprocess.STDOUT))
        checkpoint_at, display_at = start, 0
        try:
            while process.poll() is None:
                now = time.monotonic()
                sample = {'elapsed': now - start, 'wallTime': time.time(),
                          'nativeThreads': sampler.native_threads()}
                for pid, native in sample['nativeThreads'].items():
                    try:
                        native['status'] = (Path('/proc') / pid / 'status').read_text()
                        native['io'] = (Path('/proc') / pid / 'io').read_text()
                        native['waitChannels'] = {task.name: (task / 'wchan').read_text()
                            for task in (Path('/proc') / pid / 'task').iterdir()}
                    except OSError:
                        pass
                resources.write(json.dumps(sample) + '\n')
                resources.flush()
                if now - display_at >= 15:
                    print(f'{folder.name}: {now-start:.0f}s — {latest[0][-180:]}', flush=True)
                    display_at = now
                if now - checkpoint_at >= 60:
                    # A small snapshot survives even if credits expire during this mapper run.
                    status = {'run': folder.name, 'elapsed': now-start,
                              'threadSample': sample,
                              'logTail': (folder / 'pipeline.log').read_text(errors='replace')[-150000:]}
                    try:
                        storage.put_bytes(f'{cloud}/live.json', json.dumps(status).encode(), 'application/json')
                    except Exception as error:
                        print(f'Live checkpoint upload failed: {error}', flush=True)
                    checkpoint_at = time.monotonic()
                try:
                    process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    pass
            finished = time.monotonic()
            reader.join(timeout=10)
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
            for profiler in profilers:
                if profiler.poll() is None:
                    profiler.terminate()
                try:
                    profiler.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    profiler.kill()
                    profiler.wait()
            for output in handles:
                output.close()
    after = {name: (Path('/sys/fs/cgroup') / name).read_text() for name in before}
    result = {'command': command, 'seconds': finished-start,
              'postprocessingSeconds': time.monotonic()-finished,
              'returnCode': process.returncode, 'cgroupBefore': before, 'cgroupAfter': after,
              'perfAvailable': bool(shutil.which('perf')),
              'profilerExitCodes': [profiler.returncode for profiler in profilers]}
    if (folder / 'perf.data').is_file():
        (folder / 'perf-symbols.txt').write_text(capture([
            'perf', 'report', '--stdio', '-i', str(folder / 'perf.data'),
            '--sort', 'symbol', '--percent-limit', '1',
        ]).get('text', 'Profiler report unavailable'))
    (folder / 'result.json').write_text(json.dumps(result, indent=2))
    print(json.dumps(result), flush=True)
    stage_checkpoint(storage, root, cloud, folder.name)
    if process.returncode:
        raise RuntimeError(f'{folder.name} failed with code {process.returncode}')


class ReferenceUnavailable(RuntimeError):
    """The historical image set is missing or differs from the recorded benchmark."""


def reference_hashes(manifest):
    return {Path(filename.strip().lstrip('*')).name: checksum
            for checksum, filename in (line.split(maxsplit=1)
                                       for line in manifest.read_text().splitlines() if line.strip())}


def find_reference_source(storage, manifest, preferred='images', max_folders=20):
    """Search bounded metadata only; filenames nominate a candidate, SHA verifies it later."""
    expected = set(reference_hashes(manifest))
    queue = [(preferred, 1), ('', 0)]
    seen, examined = set(), []
    while queue and len(seen) < max_folders:
        source, depth = queue.pop(0)
        if source in seen:
            continue
        seen.add(source)
        try:
            items = storage.list_files(source)
        except RuntimeError as error:
            message = str(error)
            if ('Google Drive folder not found:' not in message
                    and 'Google Drive contains duplicate items named' not in message):
                raise
            examined.append({'folder': source, 'reason': message})
            continue
        examined.append({'folder': source, 'files': len(items)})
        if expected.issubset({item['name'] for item in items}):
            return source, {'state': 'candidate', 'source': source, 'examined': examined}
        if depth >= 2:
            continue
        parent = storage._resolve_folder(source)
        query = urllib.parse.urlencode({
            'q': f"'{worker.google_query_value(parent)}' in parents and trashed = false "
                 f"and mimeType = '{storage.folder_mime_type}'",
            'fields': 'files(id,name)', 'pageSize': '100',
        })
        folders = storage.request('GET', '/files?' + query).get('files', [])
        for item in sorted(folders, key=lambda item: item['name']):
            if item['name'].casefold() in {'jobs', 'diagnostics'}:
                continue
            path = '/'.join(filter(None, (source, item['name'])))
            if path not in seen:
                queue.append((path, depth + 1))
    return None, {'state': 'unavailable', 'examined': examined,
                  'reason': 'No historical filename set found within the bounded two-level search'}


def prepare_datasets(storage, root, args, sampler):
    """Missing optional reference data must not block profiling the current survey."""
    manifest = Path(__file__).resolve().parents[1] / 'benchmarks/reefplot-248.sha256'
    source, status = find_reference_source(storage, manifest, args.reference_source)
    datasets = [('current', args.source, 200, None, (4, 8))]
    if source is not None:
        try:
            stage_source(storage, source, root / 'reference', 248, sampler, manifest)
        except ReferenceUnavailable as error:
            status.update(state='unavailable', reason=str(error))
        else:
            status.update(state='verified', sha256Verified=True)
            datasets.append(('reference', source, 248, manifest, (8,)))
    if status['state'] != 'verified':
        print('Historical reference unavailable: ' + status['reason']
              + '. Continuing current-survey sparse profiling; historical comparison remains unverified.',
              flush=True)
    else:
        print('Historical reference SHA-256 verified at: ' + source, flush=True)
    (root / 'reference.json').write_text(json.dumps(status, indent=2))
    stage_source(storage, args.source, root / 'current', 200, sampler)
    return datasets


def stage_source(storage, source, destination, count, sampler, reference=None):
    items = storage.list_files(source)
    if not any(Path(x['name']).suffix.lower() in {'.jpg', '.jpeg', '.png'} for x in items):
        source += '/images'
        items = storage.list_files(source)
    expected_sha = {}
    if reference:
        expected_sha = reference_hashes(reference)
        files = []
        for name in expected_sha:
            matches = [x for x in items if x['name'] == name]
            identities = {(x.get('md5Checksum'), x.get('size')) for x in matches}
            if not matches or len(identities) != 1:
                raise ReferenceUnavailable(f'Reference source missing or ambiguous: {name}')
            item = min(matches, key=lambda x: x['id'])
            files.append({'name': name, 'size': int(item['size']), 'sourceId': item['id'],
                          'sourceName': name, 'sourceMd5': item.get('md5Checksum', '')})
    else:
        files = sampler.subset(items, count, 0)
    destination.mkdir(parents=True)
    (destination / 'source.json').write_text(json.dumps({'source': source, 'files': files}, indent=2))
    images = destination / 'images'
    images.mkdir()
    selected = worker.select_expected_files(items, files)
    cache = worker.source_cache_folder(source)
    missing = []
    for item in selected:
        identity = item.get('_source_identity')
        cached = cache / f"{identity}-{item['name']}" if cache and identity else None
        target = images / item['name']
        if cached and not item.get('_disable_source_cache') and worker.cache_file_matches(
                cached, int(item.get('size') or 0)):
            target.symlink_to(cached)
        else:
            missing.append((item, target))
    print(f'{destination.name}: {len(selected)-len(missing)} cache hits, '
          f'{len(missing)} downloads to temporary disk; network cache is read-only', flush=True)
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(storage._download_file, item, target) for item, target in missing]
        for future in as_completed(futures):
            future.result()
    for item in files:
        path = destination / 'images' / item['name']
        if path.stat().st_size != item['size']:
            raise RuntimeError(f"Image size mismatch: {item['name']}")
        if item['sourceMd5'] and digest(path, 'md5') != item['sourceMd5']:
            raise RuntimeError(f"Image checksum mismatch: {item['name']}")
        if expected_sha and digest(path) != expected_sha[item['name']]:
            raise ReferenceUnavailable(f"Reference differs from original benchmark: {item['name']}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', default='orbicella-rockbottom-03-11-26')
    parser.add_argument('--reference-source', default='images')
    parser.add_argument('--source-folder-id', default='19pJux_ONjbFHmflWQPtJwL1ayYd4zczs')
    parser.add_argument('--root-folder-id', default='1EuO8J5TBR55UQOxTEU_X_fOXeZZVHkwr')
    parser.add_argument('--output', type=Path, default=Path('results'))
    args = parser.parse_args()
    print('Sparse profiler revision: ' + PROFILE_REVISION, flush=True)
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=False)
    sampler = module('sampler', 'diagnose-runpod.py')
    analysis = module('analysis', 'analyze-diagnostic.py')
    if sampler.native_threads():
        raise RuntimeError('Another native reconstruction is running; use an idle worker')
    storage = worker.GoogleDriveClient()
    cloud = 'diagnostics/sparse-profile-' + uuid.uuid4().hex
    print('Reports will be retained under reefplot/' + cloud, flush=True)
    state = {'startedAt': worker.utc_now(), 'cloudPath': cloud}
    destination_ready = False
    try:
        ensure_report_folder(storage, cloud)
        destination_ready = True
        state['sourceFolder'] = pin_source_folder(
            storage, args.source, args.source_folder_id, args.root_folder_id)
        hardware = {'cpu': capture(['lscpu']), 'gpu': capture(['nvidia-smi', '-q']),
                    'mapperOptions': capture(['colmap', 'mapper', '-h']),
                    'profilerCapability': capture(['perf', 'stat', '-e', 'cpu-clock', '--', 'sleep', '0.1']),
                    'nativeBuildRefs': {path.name: path.read_text() for path in
                        Path('/usr/local/share/openreef').glob('*REF') if path.is_file()},
                    'pipelineOptions': capture(['openreef-pipeline', '--help']),
                    'runtime': {key: os.environ.get(key) for key in (
                        'OPENREEF_VERSION', 'OPENREEF_GPU_VERSION', 'OPENREEF_MAX_CORES',
                        'OPENREEF_BA_BACKEND', 'OPENREEF_CERES_USE_GPU', 'OMP_NUM_THREADS',
                        'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS')},
                    'limits': {name: (Path('/sys/fs/cgroup') / name).read_text()
                               for name in ('cpu.max', 'cpu.stat', 'cpuset.cpus.effective',
                                            'memory.max', 'memory.events')
                               if (Path('/sys/fs/cgroup') / name).is_file()}}
        (root / 'hardware.json').write_text(json.dumps(hardware, indent=2))
        stage_checkpoint(storage, root, cloud, 'hardware')
        if hardware['profilerCapability'].get('code') != 0:
            print('Native perf sampling is unavailable; retaining the capability error and '
                  'CPU/thread/phase evidence instead.', flush=True)
        if '--stages' not in hardware['pipelineOptions'].get('text', ''):
            raise RuntimeError('Installed pipeline lacks explicit stage selection; refusing a full run')
        datasets = prepare_datasets(storage, root, args, sampler)
        state['referenceComparison'] = json.loads((root / 'reference.json').read_text())
        stage_checkpoint(storage, root, cloud, 'source-verification')
        for label, source, count, reference, limits in datasets:
            dataset = root / label
            command = worker.pipeline_command(dataset)
            command += ['--stages', 'features,matching']
            command[command.index('--cores') + 1] = '8'
            measured(command, root / (label+'-prepare'), sampler, storage, cloud, root)
            database = dataset / 'colmap/database.db'
            (dataset / 'database-stats.json').write_text(json.dumps(database_stats(database), indent=2))
            for cores in limits:
                folder = root / f'{label}-sparse-{cores}'
                model = folder / 'model'
                folder.mkdir()
                model.mkdir()
                copy = folder / 'database.db'
                with sqlite3.connect(str(database)) as original, sqlite3.connect(str(copy)) as target:
                    original.backup(target)
                seed_hash = digest(copy)
                command = ['colmap', 'mapper', '--database_path', str(copy),
                           '--image_path', str(dataset / 'images'), '--output_path', str(model),
                           '--Mapper.num_threads', str(cores), '--Mapper.ba_use_gpu', '1',
                           '--Mapper.ba_gpu_index', '-1']
                # measured expects a fresh logging folder, separate from database/model outputs.
                logging = folder / 'measurement'
                measured(command, logging, sampler, storage, cloud, root, profile=True)
                report = {'databaseSha256BeforeMapping': seed_hash,
                          'database': database_stats(database), 'models': analysis.models(model),
                          'pointStats': {p.name: point_stats(p / 'points3D.bin')
                                         for p in model.iterdir() if p.is_dir()},
                          'threads': analysis.thread_summary(logging / 'resources.jsonl'),
                          'log': analysis.log_summary(logging / 'pipeline.log')}
                (folder / 'analysis.json').write_text(json.dumps(report, indent=2))
                print(json.dumps({'run': folder.name, 'database': report['database'],
                    'registeredImageCounts': [item.get('registeredImages') for item in report['models']],
                    'pointStats': report['pointStats'], 'threads': report['threads'],
                    'globalWindowSeconds': report['log'].get('retriangulationGlobalWindowSeconds'),
                    'profilerSummary': (logging / 'perf-symbols.txt').read_text()[:12000]
                        if (logging / 'perf-symbols.txt').is_file() else 'Native profiler unavailable',
                }, indent=2), flush=True)
                stage_checkpoint(storage, root, cloud, folder.name+'-analyzed')
        state['state'] = 'completed'
    except Exception:
        state['state'] = 'failed'
        state['traceback'] = traceback.format_exc()
        raise
    finally:
        (root / 'status.json').write_text(json.dumps(state, indent=2))
        try:
            if destination_ready:
                stage_checkpoint(storage, root, cloud, state.get('state', 'interrupted'))
        except Exception as error:
            print(f'Final upload failed; local evidence remains at {root}: {error}', flush=True)


if __name__ == '__main__':
    main()
