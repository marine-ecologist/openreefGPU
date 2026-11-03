import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { stripTypeScriptTypes } from 'node:module';
import test from 'node:test';

// Exercise the actual Worker routes against an in-memory cloud storage adapter.
const source = readFileSync(new URL('../cloud/api/src/index.ts', import.meta.url), 'utf8')
  .replace(/^import .* from '\.\/storage';/m, `
    class StorageError extends Error { status = 500; }
    const storageForEnv = () => globalThis.__fullProjectStorage;
  `);
const compiled = stripTypeScriptTypes(source);
const { default: worker } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`);
const env = {
  ALLOWED_ORIGINS: '', REQUIRE_ACCESS: 'false',
  OPENREEF_VERSION: '0.6.5', OPENREEF_GPU_VERSION: '0.6.5-gpu.6',
};
let records;
function setup() {
  records = new Map();
  globalThis.__fullProjectStorage = {
    provider: 'google-drive', publicRoot: 'jobs',
    async ensureFolder() {},
    async listRootFiles() { return [{ name: 'a.jpg', size: 5 }, { name: 'b.jpg', size: 5 }]; },
    async writeJson(path, value) { records.set(path, structuredClone(value)); },
    async readJson(path) { return structuredClone(records.get(path)); },
    async readFile(path, range) {
      assert.ok(path.endsWith('/output/full-project.tar.gz'));
      assert.equal(range, 'bytes=0-3');
      return new Response('test', { status: 206, headers: {
        'Content-Type': 'application/gzip', 'Content-Range': 'bytes 0-3/100',
      } });
    },
  };
}
const create = (options = {}) => worker.fetch(new Request('https://test/v1/jobs', {
  method: 'POST', headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ name: 'survey', sourceFolder: 'images', ...options }),
}), env);

test('past jobs link redirects to configured storage folder without creating a job', async () => {
  setup();
  globalThis.__fullProjectStorage.jobsFolderUrl = async () => 'https://drive.google.com/drive/folders/jobs-folder';
  const response = await worker.fetch(new Request('https://test/v1/jobs-folder'), env);
  assert.equal(response.status, 302);
  assert.equal(response.headers.get('Location'), 'https://drive.google.com/drive/folders/jobs-folder');
  assert.equal(response.headers.get('Cache-Control'), 'no-store');
  assert.equal(records.size, 0);
  delete globalThis.__fullProjectStorage.jobsFolderUrl;
  assert.equal((await worker.fetch(new Request('https://test/v1/jobs-folder'), env)).status, 404);
});

test('full-project options persist; old clients default to compact', async () => {
  setup();
  for (const options of [{}, { outputMode: 'full', includeSourceImages: true }]) {
    const response = await create(options);
    assert.equal(response.status, 201);
    const { job } = await response.json();
    const record = records.get(`${job.id}/job.json`);
    assert.equal(record.outputMode, options.outputMode ?? 'compact');
    assert.equal(record.includeSourceImages, options.includeSourceImages ?? false);
    assert.equal(job.outputMode, record.outputMode);
  }
});

test('malformed archive options fail before cloud storage writes', async () => {
  setup();
  for (const options of [
    { outputMode: 'anything' }, { includeSourceImages: 'true' },
    { outputMode: 'compact', includeSourceImages: true },
  ]) {
    assert.equal((await create(options)).status, 400);
  }
  assert.equal(records.size, 0);
});

test('completed archives support ranged downloads; arbitrary files remain blocked', async () => {
  setup();
  const { job } = await (await create({ outputMode: 'full' })).json();
  const record = records.get(`${job.id}/job.json`);
  record.state = 'completed';
  record.result = { model: 'model.glb', manifest: 'manifest.json', project: 'full-project.tar.gz' };
  const details = await worker.fetch(new Request(`https://test/v1/jobs/${job.id}`), env);
  const result = (await details.json()).result;
  assert.ok(result.projectUrl.endsWith('/full-project.tar.gz'));
  const response = await worker.fetch(new Request(`https://test${result.projectUrl}`, {
    headers: { Range: 'bytes=0-3' },
  }), env);
  assert.equal(response.status, 206);
  assert.equal(response.headers.get('Content-Disposition'), 'attachment; filename="full-project.tar.gz"');
  assert.equal(response.headers.get('Content-Range'), 'bytes 0-3/100');
  assert.equal(await response.text(), 'test');
  assert.equal((await worker.fetch(new Request(`https://test/v1/jobs/${job.id}/assets/private.json`), env)).status, 404);
});
