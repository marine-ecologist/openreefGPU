#!/usr/bin/env node
import { execFileSync } from 'node:child_process';
import { appendFileSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const json = (root, name) => JSON.parse(readFileSync(resolve(root, name), 'utf8'));
const stable = /^(?:v)?(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$/;
export function compareVersions(a, b) {
  const left = a.replace(/^v/, '').split('.').map(Number);
  const right = b.replace(/^v/, '').split('.').map(Number);
  for (let i = 0; i < 3; i++) if (left[i] !== right[i]) return left[i] - right[i];
  return 0;
}
export function latestStableTag(output) {
  const tags = output.split('\n').map((line) => line.match(/\trefs\/tags\/(v\d+\.\d+\.\d+)$/)?.[1])
    .filter((tag) => tag && stable.test(tag));
  return tags.sort((a, b) => compareVersions(b, a))[0];
}

export function updateVersionFiles(root, release) {
  if (!stable.test(release.version) || release.version.startsWith('v') || !/^[a-f0-9]{40}$/.test(release.commit)) {
    throw new Error('A stable OpenReef version and full commit SHA are required');
  }
  const versions = json(root, 'versions.json');
  const difference = compareVersions(release.version, versions.openreef.version);
  if (difference < 0) throw new Error('Refusing to downgrade OpenReef');
  if (difference === 0) {
    if (release.commit !== versions.openreef.commit) throw new Error('Pinned upstream tag moved; review the changed commit manually');
    return { changed: false, openreef: release.version, gpu: versions.openreefGPU.version };
  }
  const gpu = `${release.version}-gpu.1`;
  const previous = versions.openreefGPU.version;
  versions.openreef = { ...versions.openreef, version: release.version, ref: `v${release.version}`, commit: release.commit };
  versions.openreefGPU.version = gpu;
  const writes = new Map();
  const stageJson = (name, value) => writes.set(name, `${JSON.stringify(value, null, 2)}\n`);
  stageJson('versions.json', versions);
  for (const name of ['package.json', 'cloud/api/package.json']) {
    const value = json(root, name);
    if (value.version !== previous) throw new Error(`${name} is out of sync; run check:versions first`);
    value.version = gpu;
    stageJson(name, value);
  }
  for (const name of ['package-lock.json', 'cloud/api/package-lock.json']) {
    const value = json(root, name);
    if (value.version !== previous || value.packages?.['']?.version !== previous) throw new Error(`${name} has an inconsistent root version`);
    value.version = gpu;
    value.packages[''].version = gpu;
    stageJson(name, value);
  }
  let docker = readFileSync(resolve(root, 'worker/Dockerfile'), 'utf8');
  for (const [key, value] of Object.entries({ OPENREEF_VERSION: release.version, OPENREEF_REF: release.commit, OPENREEF_GPU_VERSION: gpu })) {
    const pattern = new RegExp(`^ARG ${key}=.+$`, 'm');
    if (!pattern.test(docker)) throw new Error(`Missing Docker ARG ${key}`);
    docker = docker.replace(pattern, `ARG ${key}=${value}`);
  }
  writes.set('worker/Dockerfile', docker);
  let config = readFileSync(resolve(root, 'cloud/api/wrangler.jsonc'), 'utf8');
  for (const [key, value] of Object.entries({ OPENREEF_VERSION: release.version, OPENREEF_GPU_VERSION: gpu })) {
    const pattern = new RegExp(`("${key}"\\s*:\\s*)"[^"]+"`);
    if (!pattern.test(config)) throw new Error(`Missing Cloudflare variable ${key}`);
    config = config.replace(pattern, `$1"${value}"`);
  }
  writes.set('cloud/api/wrangler.jsonc', config);
  const page = readFileSync(resolve(root, 'app/page.tsx'), 'utf8');
  if (!page.includes(`OpenReefGPU v${previous}`)) throw new Error('Could not locate sidebar version');
  writes.set('app/page.tsx', page.replace(`OpenReefGPU v${previous}`, `OpenReefGPU v${gpu}`));
  // Validate every input before changing any file. Dependency versions are untouched.
  for (const [name, contents] of writes) writeFileSync(resolve(root, name), contents);
  return { changed: true, openreef: release.version, gpu };
}

function git(args) {
  return execFileSync('git', args, { encoding: 'utf8', timeout: 60_000, stdio: ['ignore', 'pipe', 'pipe'] }).trim();
}
export function discoverRelease(root) {
  const { openreef } = json(root, 'versions.json');
  const tag = latestStableTag(git(['ls-remote', '--tags', '--refs', openreef.repository]));
  if (!tag) throw new Error('No stable OpenReef release tags found');
  const temporary = mkdtempSync(resolve(tmpdir(), 'openreef-release-'));
  try {
    git(['init', temporary]);
    git(['-C', temporary, 'fetch', '--depth', '1', openreef.repository, `refs/tags/${tag}`]);
    const commit = git(['-C', temporary, 'rev-parse', 'FETCH_HEAD^{commit}']);
    const project = git(['-C', temporary, 'show', `${commit}:pyproject.toml`]);
    const version = project.match(/^version\s*=\s*"([^"]+)"/m)?.[1];
    if (version !== tag.slice(1)) throw new Error(`OpenReef ${tag} declares version ${version}; refusing to update`);
    return { version, commit };
  } finally { rmSync(temporary, { recursive: true, force: true }); }
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const root = resolve(import.meta.dirname, '..');
  const result = updateVersionFiles(root, discoverRelease(root));
  if (process.env.GITHUB_OUTPUT) appendFileSync(process.env.GITHUB_OUTPUT,
    `changed=${result.changed}\nopenreef=${result.openreef}\ngpu=${result.gpu}\n`);
  console.log(result.changed ? `Updated OpenReef ${result.openreef}; openreefGPU ${result.gpu}` : `Already current: OpenReef ${result.openreef}`);
}
