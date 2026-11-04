import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { cpSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import test from 'node:test';
import { discoverRelease, latestStableTag, updateVersionFiles } from '../scripts/sync-openreef-release.mjs';

const names = ['versions.json', 'package.json', 'package-lock.json', 'cloud/api/package.json', 'cloud/api/package-lock.json', 'cloud/api/wrangler.jsonc', 'worker/Dockerfile', 'app/page.tsx'];
function fixture(t) {
  const root = mkdtempSync(join(tmpdir(), 'release-sync-test-'));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  for (const name of names) {
    mkdirSync(dirname(join(root, name)), { recursive: true });
    cpSync(new URL(`../${name}`, import.meta.url), join(root, name));
  }
  // Keep this baseline fixed so the tests still pass after the bot updates
  // the real repository to the very release being tested.
  const contract = JSON.parse(readFileSync(join(root, 'versions.json'), 'utf8'));
  const previous = contract.openreefGPU.version;
  const baselineGpu = '0.6.5-gpu.7';
  const baselineCommit = '7329fe43e3d43f605e9961c608af81e72788b475';
  contract.openreef = { ...contract.openreef, version: '0.6.5', ref: 'v0.6.5', commit: baselineCommit };
  contract.openreefGPU.version = baselineGpu;
  writeFileSync(join(root, 'versions.json'), JSON.stringify(contract));
  for (const name of ['package.json', 'cloud/api/package.json', 'package-lock.json', 'cloud/api/package-lock.json']) {
    const value = JSON.parse(readFileSync(join(root, name), 'utf8'));
    value.version = baselineGpu;
    if (value.packages?.['']) value.packages[''].version = baselineGpu;
    writeFileSync(join(root, name), JSON.stringify(value));
  }
  const docker = join(root, 'worker/Dockerfile');
  writeFileSync(docker, readFileSync(docker, 'utf8')
    .replace(/^ARG OPENREEF_VERSION=.+$/m, 'ARG OPENREEF_VERSION=0.6.5')
    .replace(/^ARG OPENREEF_REF=.+$/m, `ARG OPENREEF_REF=${baselineCommit}`)
    .replace(/^ARG OPENREEF_GPU_VERSION=.+$/m, `ARG OPENREEF_GPU_VERSION=${baselineGpu}`));
  const config = join(root, 'cloud/api/wrangler.jsonc');
  writeFileSync(config, readFileSync(config, 'utf8')
    .replace(/("OPENREEF_VERSION"\s*:\s*)"[^"]+"/, '$1"0.6.5"')
    .replace(/("OPENREEF_GPU_VERSION"\s*:\s*)"[^"]+"/, `$1"${baselineGpu}"`));
  const page = join(root, 'app/page.tsx');
  writeFileSync(page, readFileSync(page, 'utf8').replace(`OpenReefGPU v${previous}`, `OpenReefGPU v${baselineGpu}`));
  return root;
}
const read = (root, name) => JSON.parse(readFileSync(join(root, name), 'utf8'));
const snapshot = (root) => names.map((name) => readFileSync(join(root, name), 'utf8'));
const release = { version: '0.6.6', commit: 'a'.repeat(40) };

test('stable tags are compared numerically and prereleases are ignored', () => {
  assert.equal(latestStableTag(['v0.6.9', 'v0.6.10', 'v0.7.0-rc.1', 'v0.7.0-beta', 'other'].map((tag) => `${'a'.repeat(40)}\trefs/tags/${tag}`).join('\n')), 'v0.6.10');
  assert.equal(latestStableTag(''), undefined);
});

test('all package, lockfile, Docker, API and sidebar versions advance together', (t) => {
  const root = fixture(t);
  const dependencies = read(root, 'package-lock.json').packages['node_modules/react'];
  const result = updateVersionFiles(root, release);
  assert.deepEqual(result, { changed: true, openreef: '0.6.6', gpu: '0.6.6-gpu.1' });
  const versions = read(root, 'versions.json');
  assert.equal(versions.openreef.ref, 'v0.6.6');
  assert.equal(versions.openreef.commit, release.commit);
  for (const file of ['package.json', 'cloud/api/package.json', 'package-lock.json', 'cloud/api/package-lock.json']) assert.equal(read(root, file).version, result.gpu);
  for (const file of ['package-lock.json', 'cloud/api/package-lock.json']) assert.equal(read(root, file).packages[''].version, result.gpu);
  assert.deepEqual(read(root, 'package-lock.json').packages['node_modules/react'], dependencies);
  assert.match(readFileSync(join(root, 'worker/Dockerfile'), 'utf8'), /ARG OPENREEF_REF=a{40}/);
  assert.ok(readFileSync(join(root, 'cloud/api/wrangler.jsonc'), 'utf8').includes('"OPENREEF_GPU_VERSION": "0.6.6-gpu.1"'));
  assert.ok(readFileSync(join(root, 'app/page.tsx'), 'utf8').includes('OpenReefGPU v0.6.6-gpu.1'));
  mkdirSync(join(root, 'scripts'));
  cpSync(new URL('../scripts/check-versions.mjs', import.meta.url), join(root, 'scripts/check-versions.mjs'));
  execFileSync(process.execPath, [join(root, 'scripts/check-versions.mjs')], { stdio: 'pipe' });
  const before = snapshot(root);
  assert.equal(updateVersionFiles(root, release).changed, false);
  assert.deepEqual(snapshot(root), before);
});

test('downgrades, moved tags and malformed input never rewrite the contract', (t) => {
  const root = fixture(t);
  const before = snapshot(root);
  for (const bad of [{ version: '0.6.4', commit: 'a'.repeat(40) }, { version: '0.6.5', commit: 'b'.repeat(40) }, { version: '0.7.0-rc.1', commit: 'a'.repeat(40) }, { version: '0.6.6', commit: 'short' }]) assert.throws(() => updateVersionFiles(root, bad));
  assert.deepEqual(snapshot(root), before);
});

test('validation failure leaves all other files unchanged', (t) => {
  const root = fixture(t);
  writeFileSync(join(root, 'app/page.tsx'), 'missing version');
  const before = snapshot(root);
  assert.throws(() => updateVersionFiles(root, release), /sidebar/);
  assert.deepEqual(snapshot(root), before);
});

test('discovery resolves an annotated stable tag and verifies its declared source version', (t) => {
  const root = fixture(t);
  const upstream = join(root, 'upstream');
  mkdirSync(upstream);
  const git = (...args) => execFileSync('git', ['-C', upstream, ...args], { encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] }).trim();
  git('init');
  git('config', 'user.name', 'Test');
  git('config', 'user.email', 'test@example.invalid');
  writeFileSync(join(upstream, 'pyproject.toml'), '[project]\nversion = "0.6.6"\n');
  git('add', 'pyproject.toml');
  git('commit', '-m', 'Stable release');
  git('tag', '-a', 'v0.6.6', '-m', 'Stable release');
  git('tag', 'v0.7.0-rc.1');
  const versions = read(root, 'versions.json');
  versions.openreef.repository = upstream;
  writeFileSync(join(root, 'versions.json'), JSON.stringify(versions));
  assert.deepEqual(discoverRelease(root), { version: '0.6.6', commit: git('rev-parse', 'HEAD') });
  git('tag', 'v0.6.7');
  assert.throws(() => discoverRelease(root), /declares version/);
});
