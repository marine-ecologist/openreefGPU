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

test('direct Drive folder redirect is unavailable', async () => {
  setup();
  const response = await worker.fetch(new Request('https://test/v1/jobs-folder'), env);
  assert.equal(response.status, 404);
  assert.equal(response.headers.get('Location'), null);
  assert.equal(records.size, 0);
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

test('past projects browse persisted records with pagination and no RunPod requests', async () => {
  setup();
  const { job: first } = await (await create({ outputMode: 'full' })).json();
  const { job: second } = await (await create()).json();
  const record = records.get(`${first.id}/job.json`);
  record.state = 'completed';
  record.runpodJobId = 'expired-runpod-job';
  record.result = { model: 'model.glb', manifest: 'manifest.json', project: 'full-project.tar.gz' };
  globalThis.__fullProjectStorage.listJobFolders = async (cursor) => {
    assert.equal(cursor, 'drive-page-2');
    return { names: [first.id, 'unrelated-folder', second.id], nextCursor: 'drive-page-3' };
  };
  const previousFetch = globalThis.fetch;
  globalThis.fetch = () => { throw new Error('Browsing must not call RunPod'); };
  try {
    const response = await worker.fetch(new Request('https://test/v1/jobs?cursor=drive-page-2'), env);
    assert.equal(response.status, 200);
    const page = await response.json();
    assert.deepEqual(page.jobs.map((job) => job.id), [first.id, second.id]);
    assert.equal(page.nextCursor, 'drive-page-3');
    assert.equal(page.jobs[0].outputMode, 'full');
    assert.ok(page.jobs[0].result.projectUrl.endsWith('full-project.tar.gz'));
    assert.ok(page.jobs[0].result.modelUrl.endsWith('model.glb'));
    assert.equal(page.jobs[0].runpodJobId, undefined);
    assert.equal(page.jobs[1].state, 'uploading');
  } finally {
    globalThis.fetch = previousFetch;
  }
});

test('project browsing handles empty folders and invalid records; preserves access checks', async () => {
  setup();
  const { job } = await (await create()).json();
  globalThis.__fullProjectStorage.listJobFolders = async () => ({ names: [job.id] });
  records.set(`${job.id}/job.json`, { id: 'different-job', name: 'invalid' });
  let response = await worker.fetch(new Request('https://test/v1/jobs'), env);
  assert.deepEqual(await response.json(), { jobs: [], skipped: 1 });
  globalThis.__fullProjectStorage.listJobFolders = async () => ({ names: [] });
  response = await worker.fetch(new Request('https://test/v1/jobs'), env);
  assert.deepEqual(await response.json(), { jobs: [], skipped: 0 });
  response = await worker.fetch(new Request('https://test/v1/jobs'), { ...env, REQUIRE_ACCESS: 'true' });
  assert.equal(response.status, 401);
  response = await worker.fetch(new Request('https://test/v1/jobs', { headers: { Origin: 'https://other.example' } }), env);
  assert.equal(response.status, 403);
  response = await worker.fetch(new Request(`https://test/v1/jobs?cursor=${'x'.repeat(4097)}`), env);
  assert.equal(response.status, 400);
});

test('large browser uploads create sessions per file instead of during job creation', async () => {
  setup();
  const sessions = [];
  globalThis.__fullProjectStorage.createBrowserUpload = async (path, size, contentType) => {
    sessions.push({ path, size, contentType });
    return { uploadUrl: 'https://google.test/session', expiresAt: 'later' };
  };
  const files = Array.from({ length: 1500 }, (_, index) => ({ name: `photo-${index}.jpg`, size: 10, contentType: 'image/jpeg' }));
  const response = await create({ sourceFolder: undefined, files, uploadProtocol: 'per-file' });
  assert.equal(response.status, 201);
  const created = await response.json();
  assert.deepEqual(created.uploads, []);
  assert.equal(sessions.length, 0);
  assert.equal(records.get(`${created.job.id}/job.json`).files.length, 1500);
  const sessionRequest = (fileIndex) => worker.fetch(new Request(`https://test${created.uploadSessionPath}`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ fileIndex }),
  }), env);
  assert.equal((await sessionRequest(1499)).status, 200);
  assert.deepEqual(sessions, [{ path: `${created.job.id}/input/photo-1499.jpg`, size: 10, contentType: 'image/jpeg' }]);
  for (const index of [-1, 1500, '0', 0.5]) assert.equal((await sessionRequest(index)).status, 400);
  assert.equal(sessions.length, 1);
  records.get(`${created.job.id}/job.json`).state = 'queued';
  assert.equal((await sessionRequest(0)).status, 409);
  setup();
  assert.equal((await create({ sourceFolder: undefined, files })).status, 400);
  assert.equal(records.size, 0);
});
