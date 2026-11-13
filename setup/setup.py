#!/usr/bin/env python3
"""macOS installer and cloud setup launcher; secrets never enter the source bundle."""
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.request
import uuid

HERE = Path(__file__).resolve().parent
BASE = Path.home() / 'Library/Application Support/OpenReefGPU'
APP = BASE / 'releases/0.6.5-gpu.8'
CONFIG = BASE / 'config'
API = APP / 'cloud/api'
ENV = CONFIG / 'drive.env'
NODE = BASE / 'node/bin/node'
REPO = 'marine-ecologist/openreefGPU'
URL = 'https://openreef-gpu-api.openreef-gpu.workers.dev'


def run(command, **kwargs):
    print('+ ' + shlex.join([str(x) for x in command]), flush=True)
    return subprocess.run([str(x) for x in command], check=True, **kwargs)


def download(url, target):
    with urllib.request.urlopen(url, timeout=120) as response, target.open('wb') as output:
        shutil.copyfileobj(response, output)


def unpack(archive, destination):
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive) as source:
        for item in source.getmembers():
            resolved = (destination / item.name).resolve()
            if not resolved.is_relative_to(destination.resolve()):
                raise RuntimeError('Archive has an invalid path')
            if item.issym() or item.islnk():
                if not (resolved.parent / item.linkname).resolve().is_relative_to(destination.resolve()):
                    raise RuntimeError('Archive has an external link')
        source.extractall(destination)


def node_setup():
    candidate = shutil.which('node')
    if candidate:
        version = subprocess.check_output([candidate, '-p', 'process.versions.node'], text=True).strip()
        if tuple(int(x) for x in version.split('.')) >= (22, 13, 0):
            return Path(candidate)
    if NODE.exists():
        return NODE
    arch = 'arm64' if platform.machine() == 'arm64' else 'x64'
    cache = BASE / 'downloads'
    cache.mkdir(parents=True, exist_ok=True)
    sums = cache / 'SHASUMS256.txt'
    download('https://nodejs.org/dist/latest-v22.x/SHASUMS256.txt', sums)
    match = next((line.split() for line in sums.read_text().splitlines()
                  if re.search(rf'node-v22\.\d+\.\d+-darwin-{arch}\.tar\.gz$', line)), None)
    if not match:
        raise RuntimeError('Could not find an official Node 22 archive for this Mac')
    digest, filename = match
    archive = cache / filename
    download('https://nodejs.org/dist/latest-v22.x/' + filename, archive)
    if hashlib.sha256(archive.read_bytes()).hexdigest() != digest:
        raise RuntimeError('Node archive checksum verification failed')
    unpack(archive, cache)
    shutil.move(str(cache / filename[:-7]), BASE / 'node')
    return NODE


def install():
    print('\nInstalling OpenReefGPU 0.6.5-gpu.8 for this Mac user. No administrator password needed.')
    CONFIG.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not (APP / '.setup-ready').exists():
        if APP.exists():
            shutil.move(str(APP), str(APP) + '-previous-' + str(int(time.time())))
        unpack(HERE / 'source.tar.gz', APP)
    node = node_setup()
    os.environ['PATH'] = str(node.parent) + os.pathsep + os.environ.get('PATH', '')
    for folder in (APP, API):
        run(['npm', 'ci', '--no-fund', '--no-audit'], cwd=folder)
    run(['node', 'scripts/check-versions.mjs'], cwd=APP)
    run(['npm', 'run', 'check'], cwd=API)
    (APP / '.setup-ready').write_text('0.6.5-gpu.8\n')
    # Reuse credentials without executing a downloaded shell file.
    candidates = [API / '.dev.vars',
        Path.home() / 'Downloads/openreefGPU-main/cloud/api/.dev.vars',
        Path.home() / 'Documents/ChatGPT/openreef/openreefGPU/cloud/api/.dev.vars']
    if not ENV.exists():
        for candidate in candidates:
            if candidate.is_file():
                values = read_env(candidate)
                if all(values.get(k) for k in drive_keys()):
                    write_env(ENV, {k: values[k] for k in drive_keys()})
                    print('Reused existing Google Drive authorization.')
                    break
    print('\nMac dependencies and complete source installed at:', APP)
    print('Next: connect Drive if needed; configure the RunPod image; then deploy Cloudflare.')


def drive_keys():
    return ('GOOGLE_CLIENT_ID', 'GOOGLE_CLIENT_SECRET', 'GOOGLE_REFRESH_TOKEN', 'GOOGLE_DRIVE_ROOT_FOLDER_ID')


def read_env(path):
    values = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        value = value.strip()
        if value.startswith('"'):
            value = json.loads(value)
        elif value.startswith("'") and value.endswith("'"):
            value = value[1:-1]
        values[key.strip()] = value
    return values


def write_env(path, values):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, 'w') as output:
        output.write(''.join(key + '=' + json.dumps(value) + '\n' for key, value in values.items()))
    path.chmod(0o600)


def require_install():
    if not (APP / '.setup-ready').exists():
        install()
    node = node_setup()
    os.environ['PATH'] = str(node.parent) + os.pathsep + os.environ.get('PATH', '')


def authorize():
    require_install()
    path = input('Path to your downloaded Google OAuth client JSON (drag it here): ').strip()
    paths = shlex.split(path)
    if len(paths) != 1:
        raise RuntimeError('Enter one JSON file path')
    credential = Path(paths[0]).expanduser()
    if not credential.is_file():
        raise RuntimeError('OAuth JSON file not found')
    run(['node', API / 'scripts/google-drive-oauth.mjs', credential,
         '--folder', 'reefplot', '--existing', '--output-env', ENV], cwd=API)


def drive_settings():
    if not ENV.exists():
        authorize()
    values = read_env(ENV)
    if not all(values.get(key) for key in drive_keys()):
        raise RuntimeError('Drive configuration is incomplete; choose Connect Google Drive again')
    return {key: values[key] for key in drive_keys()}


def publish():
    require_install()
    if not shutil.which('git'):
        raise RuntimeError('Install Apple command line tools with xcode-select --install first')
    if not (HERE / 'repository.bundle').is_file():
        raise RuntimeError('Use the complete downloaded setup folder; repository.bundle is missing')
    checkout = BASE / 'publish-source'
    if not checkout.exists():
        run(['git', 'clone', HERE / 'repository.bundle', checkout])
        run(['git', 'remote', 'set-url', 'origin', 'https://github.com/' + REPO + '.git'], cwd=checkout)
    git = ['git']
    if shutil.which('gh'):
        status = subprocess.run(['gh', 'auth', 'status'], capture_output=True)
        if status.returncode:
            run(['gh', 'auth', 'login', '--hostname', 'github.com', '--git-protocol', 'https', '--web'])
        git += ['-c', 'credential.helper=', '-c', 'credential.helper=!gh auth git-credential']
    run(git + ['fetch', 'origin', 'main'], cwd=checkout)
    ahead = subprocess.run(['git', 'merge-base', '--is-ancestor', 'FETCH_HEAD', 'HEAD'], cwd=checkout)
    if ahead.returncode:
        raise RuntimeError('GitHub has changes outside this bundle. Source is retained at ' + str(checkout)
                           + '; merge those changes before pushing. Nothing was overwritten.')
    run(git + ['push', 'origin', 'HEAD:main'], cwd=checkout)
    print('Bundled source published. Build the matching gpu.8 image from the Actions page.')
    run(['open', 'https://github.com/' + REPO + '/actions/workflows/publish-worker.yml'])


def runpod_setup():
    require_install()
    values = drive_settings()
    values.update({'STORAGE_PROVIDER': 'google-drive', 'GOOGLE_DRIVE_JOB_ROOT': 'jobs',
        'OPENREEF_VERSION': '0.6.5', 'OPENREEF_GPU_VERSION': '0.6.5-gpu.8',
        'OPENREEF_BA_BACKEND': 'ceres', 'OPENREEF_CERES_USE_GPU': '1',
        'OPENREEF_MAX_CORES': '32'})
    write_env(CONFIG / 'runpod.env', values)
    print('\nRequired image: ghcr.io/marine-ecologist/openreef-gpu:0.6.5-gpu.8')
    print('Publish the bundled source using menu option 8 before building this image.')
    print('Publish the matching worker using the GitHub Actions image-build workflow.')
    run(['open', 'https://github.com/' + REPO + '/actions/workflows/publish-worker.yml'])
    run(['open', 'https://console.runpod.io/serverless'])
    print('RunPod: select the published image, 1 GPU, max workers 1, active workers 0,')
    print('100 GB container disk; attach your cache volume (at least 20 GB).')
    print('Private environment file:', CONFIG / 'runpod.env')
    if input('Copy these environment settings to your clipboard for RunPod? [y/N]: ').lower() == 'y':
        subprocess.run(['pbcopy'], input=''.join(f'{k}={v}\n' for k, v in values.items()),
                       text=True, check=True)
        print('RunPod settings copied. Paste into the endpoint environment configuration.')
    print('After the image and credentials are configured, choose Deploy Cloudflare.')


def deploy():
    require_install()
    values = drive_settings()
    if input('Is the endpoint configured with the published gpu.8 image and matching Drive settings? [y/N]: ').lower() != 'y':
        print('Configure RunPod first to avoid the previous job/image version mismatch.')
        return
    endpoint = input('RunPod endpoint ID [93jkogjuc9l6pu]: ').strip() or '93jkogjuc9l6pu'
    if not re.fullmatch(r'[A-Za-z0-9_-]+', endpoint):
        raise RuntimeError('Invalid endpoint ID')
    import getpass
    key = getpass.getpass('RunPod API key (hidden; leave blank to retain existing Cloudflare secret): ').strip()
    values['RUNPOD_ENDPOINT_ID'] = endpoint
    if key:
        values['RUNPOD_API_KEY'] = key
    wrangler = API / 'node_modules/.bin/wrangler'
    run([wrangler, 'login'], cwd=API)
    run([wrangler, 'deploy'], cwd=API)
    # Values go over stdin, never in process arguments or setup output.
    subprocess.run([str(wrangler), 'secret', 'bulk'], cwd=API,
                   input=json.dumps(values), text=True, check=True)
    run(['curl', '--fail', '--silent', '--show-error', URL + '/v1/health'])
    print('\nCloudflare deployed. Open the cloud app or start the local web app from the menu.')


def ssh_connection(text):
    parts = shlex.split(text)
    if parts and parts[0] == 'ssh':
        parts = parts[1:]
    options, host = [], None
    i = 0
    while i < len(parts):
        part = parts[i]
        if part in ('-p', '-i', '-o', '-J', '-F') and i + 1 < len(parts):
            value = os.path.expanduser(parts[i + 1]) if part in ('-i', '-F') else parts[i + 1]
            options.extend([part, value])
            i += 2
        elif not part.startswith('-') and host is None and re.fullmatch(r'[A-Za-z0-9_.@:-]+', part):
            host = part
            i += 1
        else:
            raise RuntimeError('Paste only the SSH connection command, without remote commands')
    if not host:
        raise RuntimeError('No SSH hostname supplied')
    return ['ssh'] + options + [host]


def diagnostic_source():
    """Use existing Python source without installing Node or web dependencies."""
    required = ('worker/handler.py', 'scripts/diagnose-runpod.py', 'versions.json',
                'scripts/analyze-diagnostic.py', 'scripts/profile-sparse.py',
                'benchmarks/reefplot-248.sha256')
    if all((HERE.parent / name).is_file() for name in required):
        return HERE.parent
    if (HERE / 'source.tar.gz').is_file():
        source = BASE / ('diagnostic-source-' + uuid.uuid4().hex[:12])
        unpack(HERE / 'source.tar.gz', source)
        if all((source / name).is_file() for name in required):
            return source
    if all((APP / name).is_file() for name in required):
        return APP
    raise RuntimeError('The complete diagnostic source is missing; use the full setup bundle')


def remote_bootstrap(remote, marker):
    """Receive short text lines under a PTY; never echo credential bytes."""
    return (
        "import base64,hashlib,io,json,os,pathlib,subprocess,sys,tarfile; "
        f"print('\\n{marker}_UPLOAD',flush=True); "
        "count=int(sys.stdin.readline()); "
        "lines=[]; "
        + "exec(" + repr(
            "for index in range(count):\n"
            " lines.append(sys.stdin.readline().strip())\n"
            f" print('{marker}_ACK:'+str(index),flush=True)"
        ) + "); "
        + "wire=''.join(lines); "
        "data=base64.b64decode(wire,validate=True); "
        f"root=pathlib.Path({remote!r}); root.mkdir(mode=0o700); "
        "os.chdir(root); "
        "archive=tarfile.open(fileobj=io.BytesIO(data),mode='r:gz'); "
        "members=archive.getmembers(); "
        "assert all(m.isfile() and not pathlib.PurePosixPath(m.name).is_absolute() "
        "and '..' not in pathlib.PurePosixPath(m.name).parts for m in members); "
        "archive.extractall(root); "
        "p=root/'drive.json'; os.environ.update(json.loads(p.read_text())); p.unlink(); "
        f"print('{marker}_RECEIVED:'+hashlib.sha256(data).hexdigest(),flush=True); "
        "print('Remote diagnostic runner starting. Results: '+str(root/'results'),flush=True); "
        "code=subprocess.call(['python3','-u','scripts/diagnose-runpod.py',"
        "'--count','200','--compare-sparse','--output','results']); "
        f"print('\\n{marker}_DONE:'+str(code),flush=True); sys.exit(code)"
    )


def run_pty_session(command, payload, remote, timeout=120, on_line=None):
    """Use an interactive shell, including proxies that ignore SSH exec requests."""
    import base64
    import errno
    import pty
    import select
    import signal

    marker = 'OPENREEF_' + uuid.uuid4().hex
    bootstrap = remote_bootstrap(remote, marker)
    if len(('python3 -u -c ' + shlex.quote(bootstrap)).encode()) > 3500:
        raise RuntimeError('PTY bootstrap exceeds the safe terminal line length')
    child, terminal = pty.fork()
    if child == 0:
        os.execvp(command[0], command)
    pending = b''
    deadline = time.monotonic() + timeout
    state = 'shell'
    received = False
    result = None
    display = b''
    lines = []
    sent = 0

    def send(data):
        while data:
            written = os.write(terminal, data)
            data = data[written:]

    try:
        while True:
            if state != 'running' and time.monotonic() > deadline:
                raise RuntimeError(f'SSH handshake timed out at {state}; diagnostic was not confirmed')
            readable, _, _ = select.select([terminal], [], [], 1)
            if not readable:
                continue
            try:
                chunk = os.read(terminal, 16384)
            except OSError as error:
                if error.errno == errno.EIO:
                    break
                raise
            if not chunk:
                break
            # Never send payload until remote stty -echo succeeds and Python acknowledges it.
            display += chunk
            while b'\n' in display:
                line, display = display.split(b'\n', 1)
                consumed = on_line(line.decode('utf-8', errors='replace').strip()) if on_line else False
                if not consumed and marker.encode() + b'_ACK:' not in line:
                    sys.stdout.buffer.write(line + b'\n')
                    sys.stdout.buffer.flush()
            pending = (pending + chunk)[-65536:]
            clean = re.sub(rb'\x1b\[[0-?]*[ -/]*[@-~]', b'', pending).replace(b'\r', b'')
            if state == 'shell' and re.search(rb'(?:^|\n)[^\n]{0,300}[#$>] ?$', clean):
                send(("stty -echo && printf '\\n" + marker + "_READY\\n'\n").encode())
                state = 'echo-off'
                pending = b''
                deadline = time.monotonic() + timeout
            elif state == 'echo-off' and re.search(
                rb'(?:^|\n)' + marker.encode() + rb'_READY\n', clean
            ):
                send(('python3 -u -c ' + shlex.quote(bootstrap) + '\n').encode())
                state = 'python'
                pending = b''
                deadline = time.monotonic() + timeout
            elif state == 'python' and re.search(
                rb'(?:^|\n)' + marker.encode() + rb'_UPLOAD\n', clean
            ):
                wire = base64.b64encode(payload)
                # Short lines + acknowledgments avoid both local and remote TTY overruns.
                lines = [wire[i:i + 128] for i in range(0, len(wire), 128)]
                send((str(len(lines)) + '\n').encode())
                send(lines[0] + b'\n')
                state = 'transfer'
                pending = b''
                deadline = time.monotonic() + timeout
            elif state == 'transfer':
                acknowledgment = (marker + '_ACK:' + str(sent)).encode()
                if re.search(rb'(?:^|\n)' + acknowledgment + rb'\n', clean):
                    sent += 1
                    if sent < len(lines):
                        send(lines[sent] + b'\n')
                        pending = b''
                    else:
                        state = 'uploaded'
                    deadline = time.monotonic() + timeout
            if state in ('uploaded', 'running'):
                expected = marker.encode() + b'_RECEIVED:' + hashlib.sha256(payload).hexdigest().encode()
                if not received and re.search(rb'(?:^|\n)' + expected + rb'\n', clean):
                    received = True
                    state = 'running'
                done = re.search(rb'(?:^|\n)' + marker.encode() + rb'_DONE:(-?\d+)\n', clean)
                if done:
                    result = int(done.group(1))
                    send(b'exit\n')
                    break
        if not received:
            raise RuntimeError('Worker did not confirm the helper upload; diagnostic was not started')
        if result is None:
            raise RuntimeError('SSH disconnected before completion; inspect the retained remote results')
        if result:
            raise RuntimeError(f'Remote diagnostic exited with code {result}; results: {remote}/results')
        return result
    finally:
        if display and marker.encode() + b'_ACK:' not in display:
            sys.stdout.buffer.write(display)
            sys.stdout.buffer.flush()
        os.close(terminal)
        try:
            os.kill(child, signal.SIGTERM)
        except ProcessLookupError:
            pass
        os.waitpid(child, 0)


def diagnose(connection_text=None, sparse_only=False, benchmark=False, resume_profile=None):
    sparse_only = sparse_only or benchmark
    source = diagnostic_source()
    credentials = drive_settings()
    if sparse_only:
        profiler = source / ('scripts/benchmark-sparse.py' if benchmark else 'scripts/profile-sparse.py')
        print('Sparse profiler source:', profiler, flush=True)
        print('Source SHA256:', hashlib.sha256(profiler.read_bytes()).hexdigest()[:16], flush=True)
    connection = ssh_connection(connection_text or input(
        'Paste the SSH connection command for an idle OpenReef RunPod worker: '
    ))
    remote = '/tmp/openreef-diagnostic-' + uuid.uuid4().hex[:12]
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w:gz') as archive:
        names = ['worker/__init__.py', 'worker/handler.py', 'scripts/diagnose-runpod.py', 'versions.json']
        if sparse_only:
            names += ['scripts/profile-sparse.py', 'scripts/analyze-diagnostic.py',
                      'benchmarks/reefplot-248.sha256']
        if benchmark:
            names += ['scripts/benchmark-sparse.py']
        for name in names:
            if sparse_only and name in {'scripts/diagnose-runpod.py', 'scripts/profile-sparse.py',
                                        'scripts/benchmark-sparse.py'}:
                continue
            path = source / name
            if path.is_file():
                archive.add(path, arcname=name)
        if sparse_only:
            # Rename the sampler and update the profile's module loader in the uploaded copy.
            sampler = (source / 'scripts/diagnose-runpod.py').read_bytes()
            profile = (source / 'scripts/profile-sparse.py').read_text().replace(
                "module('sampler', 'diagnose-runpod.py')", "module('sampler', 'diagnostic-sampler.py')")
            wrapper = b"import runpy,sys; sys.argv=['profile-sparse.py']; runpy.run_path('scripts/profile-sparse.py',run_name='__main__')\n"
            if benchmark:
                benchmark_source = (source / 'scripts/benchmark-sparse.py').read_text().replace(
                    "load('diagnostic_sampler', 'diagnose-runpod.py')",
                    "load('diagnostic_sampler', 'diagnostic-sampler.py')")
                data = benchmark_source.encode()
                info = tarfile.TarInfo('scripts/benchmark-sparse.py')
                info.size, info.mode = len(data), 0o600
                archive.addfile(info, io.BytesIO(data))
                arguments = ['benchmark-sparse.py']
                if resume_profile:
                    arguments += ['--resume-from', resume_profile]
                wrapper = ("import runpy,sys; sys.argv=" + repr(arguments)
                           + "; runpy.run_path('scripts/benchmark-sparse.py',run_name='__main__')\n").encode()
            for name, data in [('scripts/diagnostic-sampler.py', sampler),
                               ('scripts/profile-sparse.py', profile.encode()),
                               ('scripts/diagnose-runpod.py', wrapper)]:
                info = tarfile.TarInfo(name)
                info.size, info.mode = len(data), 0o600
                archive.addfile(info, io.BytesIO(data))
        content = json.dumps(credentials).encode()
        info = tarfile.TarInfo('drive.json')
        info.size, info.mode = len(content), 0o600
        archive.addfile(info, io.BytesIO(content))
    print('Connecting through an interactive SSH terminal; verifying the remote upload.', flush=True)
    print('The diagnostic will use your active GPU worker at its normal RunPod rate.', flush=True)
    command = ['ssh', '-tt', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=20'] + connection[1:]
    run_pty_session(command, buffer.getvalue(), remote)


def collect_results(connection_text, results_path):
    """Read retained results over the SSH proxy; never launch reconstruction."""
    import base64
    import gzip

    source = diagnostic_source()
    analyzer = source / 'scripts/analyze-diagnostic.py'
    if not analyzer.is_file():
        raise RuntimeError('The result analyzer is missing from this source checkout')
    remote = '/tmp/openreef-collect-' + uuid.uuid4().hex[:12]
    target = Path(results_path)
    if not target.is_absolute() or '..' in target.parts:
        raise RuntimeError('Use the absolute results path printed by the diagnostic')
    wrapper = (
        "import base64,gzip,hashlib,importlib.util,json,pathlib\n"
        "spec=importlib.util.spec_from_file_location('analyzer','scripts/analyze-diagnostic.py')\n"
        "module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)\n"
        "print('Reading retained sparse logs and models; reconstruction is not being rerun.',flush=True)\n"
        f"report=module.summarize(pathlib.Path({str(target)!r}))\n"
        "data=gzip.compress(json.dumps(report).encode())\n"
        "wire=base64.b64encode(data).decode()\n"
        "for index in range(0,len(wire),1024):\n"
        " print('OPENREEF_REPORT:'+wire[index:index+1024],flush=True)\n"
        "print('OPENREEF_REPORT_SHA256:'+hashlib.sha256(data).hexdigest(),flush=True)\n"
        f"evidence=module.evidence_archive(pathlib.Path({str(target)!r}))\n"
        "wire=base64.b64encode(evidence).decode()\n"
        "for index in range(0,len(wire),1024):\n"
        " print('OPENREEF_EVIDENCE:'+wire[index:index+1024],flush=True)\n"
        "print('OPENREEF_EVIDENCE_SHA256:'+hashlib.sha256(evidence).hexdigest(),flush=True)\n"
    )
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w:gz') as archive:
        archive.add(analyzer, arcname='scripts/analyze-diagnostic.py')
        for name, content in [('scripts/diagnose-runpod.py', wrapper.encode()), ('drive.json', b'{}')]:
            info = tarfile.TarInfo(name)
            info.size, info.mode = len(content), 0o600
            archive.addfile(info, io.BytesIO(content))
    chunks, checksums, evidence_chunks, evidence_checksums = [], [], [], []

    def receive(line):
        if line.startswith('OPENREEF_EVIDENCE:'):
            evidence_chunks.append(line.split(':', 1)[1])
            if sum(len(part) for part in evidence_chunks) > 128 * 1024**2:
                raise RuntimeError('Diagnostic evidence exceeds the transfer limit')
            return True
        if line.startswith('OPENREEF_EVIDENCE_SHA256:'):
            evidence_checksums.append(line.split(':', 1)[1])
            return True
        if line.startswith('OPENREEF_REPORT:'):
            chunks.append(line.split(':', 1)[1])
            if sum(len(part) for part in chunks) > 8 * 1024**2:
                raise RuntimeError('Diagnostic report exceeds the transfer limit')
            return True
        if line.startswith('OPENREEF_REPORT_SHA256:'):
            checksums.append(line.split(':', 1)[1])
            return True
        return False

    connection = ssh_connection(connection_text or input('Paste the worker SSH command: '))
    command = ['ssh', '-tt', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=20'] + connection[1:]
    run_pty_session(command, buffer.getvalue(), remote, on_line=receive)
    data = base64.b64decode(''.join(chunks), validate=True)
    if len(checksums) != 1 or hashlib.sha256(data).hexdigest() != checksums[0]:
        raise RuntimeError('Incomplete or invalid diagnostic report transfer')
    evidence = base64.b64decode(''.join(evidence_chunks), validate=True)
    if len(evidence_checksums) != 1 or hashlib.sha256(evidence).hexdigest() != evidence_checksums[0]:
        raise RuntimeError('Incomplete or invalid raw-evidence transfer; worker data remains intact')
    report = json.loads(gzip.decompress(data))
    folder = BASE / 'diagnostic-reports'
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    destination = folder / ('sparse-' + str(int(time.time())) + '.json')
    destination.write_text(json.dumps(report, indent=2))
    destination.chmod(0o600)
    archive = destination.with_suffix('.tar.gz')
    archive.write_bytes(evidence)
    archive.chmod(0o600)
    print('Diagnostic report saved on this Mac:', destination)
    print('Raw logs and thread samples saved:', archive)



def diagnostic_drive_client():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        'openreef_diagnostic_storage', diagnostic_source() / 'worker/handler.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    credentials = drive_settings()
    previous = {key: os.environ.get(key) for key in credentials}
    try:
        os.environ.update(credentials)
        return module.GoogleDriveClient()
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def collect_drive(cloud):
    """Retrieve completed profiling evidence from Drive without a RunPod worker."""
    if not re.fullmatch(r'diagnostics/sparse-profile-[0-9a-f]{32}', cloud):
        raise RuntimeError('Use the diagnostics/sparse-profile-<id> path printed by the profiler')
    storage = diagnostic_drive_client()
    item = storage._resolve_file(cloud + '/reports.tar.gz')
    metadata = storage.request('GET', '/files/' + item['id'] + '?fields=id,name,size,md5Checksum')
    folder = BASE / 'diagnostic-reports' / (cloud.split('/')[-1] + '-' + uuid.uuid4().hex[:8])
    folder.mkdir(parents=True, mode=0o700)
    archive = folder / 'reports.tar.gz'
    storage._download_file(metadata, archive)
    if archive.stat().st_size != int(metadata['size']):
        raise RuntimeError('Incomplete Drive report download')
    checksum = hashlib.md5()  # Drive content integrity, not a security signature.
    with archive.open('rb') as stream:
        for block in iter(lambda: stream.read(4 * 1024**2), b''):
            checksum.update(block)
    if metadata.get('md5Checksum') and checksum.hexdigest() != metadata['md5Checksum']:
        raise RuntimeError('Drive report checksum mismatch')
    unpack(archive, folder / 'raw')
    summary = {'cloudPath': cloud, 'downloadedAt': time.time(), 'runs': {}}
    for analysis in sorted((folder / 'raw').glob('*-sparse-*/analysis.json')):
        model = json.loads(analysis.read_text())
        result = analysis.parent / 'measurement/result.json'
        summary['runs'][analysis.parent.name] = {
            'result': json.loads(result.read_text()) if result.is_file() else None,
            'analysis': model,
        }
    (folder / 'summary.json').write_text(json.dumps(summary, indent=2))
    print('Drive report and raw evidence downloaded to:', folder)
    print('No RunPod worker was used.')
    return folder


def website():
    require_install()
    environment = dict(os.environ, NEXT_PUBLIC_OPENREEF_API_URL=URL)
    print('Open http://localhost:3000 after the server starts. Press Ctrl+C to stop.')
    subprocess.Popen(['open', 'http://localhost:3000'])
    run(['npm', 'run', 'dev', '--', '--host', '127.0.0.1', '--port', '3000'], cwd=APP, env=environment)


def main():
    if sys.platform != 'darwin':
        raise RuntimeError('Run this launcher on macOS. The diagnostic itself runs on RunPod Linux.')
    if sys.version_info < (3, 10):
        raise RuntimeError('Python 3.10 or newer is required')
    os.umask(0o077)
    if len(sys.argv) > 1:
        import argparse
        parser = argparse.ArgumentParser()
        parser.add_argument('--diagnose', action='store_true')
        parser.add_argument('--benchmark-1k-from', metavar='DIAGNOSTIC_PATH', help='Run two passing Ceres variants at 1,000 images using prior checks')
        parser.add_argument('--benchmark-sparse', action='store_true', help='Three variants: correctness at 200 then 1,000 images; retain all stages')
        parser.add_argument('--profile-sparse', action='store_true', help='Sparse-only current/reference profiling with Drive checkpoints')
        parser.add_argument('--collect', metavar='REMOTE_RESULTS', help='Collect an existing run; no reconstruction')
        parser.add_argument('--collect-drive', metavar='DIAGNOSTIC_PATH', help='Download durable reports without a GPU worker')
        parser.add_argument('--ssh', help='Complete SSH connection command')
        args = parser.parse_args()
        if args.collect_drive:
            collect_drive(args.collect_drive)
        elif args.collect:
            collect_results(args.ssh, args.collect)
        elif args.benchmark_1k_from:
            diagnose(args.ssh, benchmark=True, resume_profile=args.benchmark_1k_from)
        elif args.benchmark_sparse:
            diagnose(args.ssh, benchmark=True)
        elif args.profile_sparse:
            diagnose(args.ssh, sparse_only=True)
        elif args.diagnose:
            diagnose(args.ssh)
        else:
            parser.error('Use --diagnose or --collect to run without the setup menu')
        return
    print('OpenReefGPU setup — Mac browser client + Google Drive + RunPod')
    actions = {'1': install, '2': authorize, '3': runpod_setup, '4': deploy,
               '5': website, '6': diagnose,
               '7': lambda: run(['open', 'https://marine-ecologist.github.io/openreefGPU/']),
               '8': publish}
    while True:
        print('\n1 Install / repair the complete Mac setup\n2 Connect Google Drive\n'
              '3 Configure RunPod / worker image\n4 Deploy Cloudflare API\n'
              '5 Start local browser app\n6 Run the 200-image diagnostic over SSH\n'
              '7 Open existing cloud app\n8 Publish bundled source to GitHub\n0 Exit')
        choice = input('Choose [1]: ').strip() or '1'
        if choice == '0':
            return
        try:
            if choice not in actions:
                print('Choose a number from the menu.')
                continue
            actions[choice]()
        except KeyboardInterrupt:
            print('\nStopped this action. Returning to the menu.')
        except Exception as error:
            print('\nAction could not complete:', error)
            print('Your existing deployments and Drive originals have not been removed.')


if __name__ == '__main__':
    main()
