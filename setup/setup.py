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


def diagnose():
    require_install()
    credentials = drive_settings()
    connection = ssh_connection(input('Paste the SSH connection command for an idle OpenReef RunPod worker: '))
    remote = '/tmp/openreef-diagnostic-' + uuid.uuid4().hex[:12]
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w:gz') as archive:
        for name in ('worker/__init__.py', 'worker/handler.py', 'scripts/diagnose-runpod.py', 'versions.json'):
            path = APP / name
            if path.is_file():
                archive.add(path, arcname=name)
        content = json.dumps(credentials).encode()
        info = tarfile.TarInfo('drive.json')
        info.size, info.mode = len(content), 0o600
        archive.addfile(info, io.BytesIO(content))
    print('Uploading the complete diagnostic helper to a separate temporary worker directory.')
    # Direct SSH supports this; some RunPod SSH proxy connections only support an interactive shell.
    command = 'umask 077; mkdir -p ' + shlex.quote(remote) + '; tar -xzf - -C ' + shlex.quote(remote)
    subprocess.run(connection + [command], input=buffer.getvalue(), check=True)
    bootstrap = (
        "import json,os,pathlib; p=pathlib.Path('drive.json'); "
        "os.environ.update(json.loads(p.read_text())); p.unlink(); "
        "os.execvp('python3',['python3','scripts/diagnose-runpod.py',"
        "'--count','200','--compare-sparse','--output','results'])"
    )
    command = 'cd ' + shlex.quote(remote) + ' && python3 -c ' + shlex.quote(bootstrap)
    print('Starting the 200-image GPU test. The running worker incurs normal RunPod charges.')
    print('Remote results directory:', remote + '/results')
    run(connection + [command])


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
