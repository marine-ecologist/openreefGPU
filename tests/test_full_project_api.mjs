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

test('Drive duplicate contents are skipped before the photo limit; name collisions retain both IDs', async () => {
  setup();
  const input = [
    { id: 'z', name: 'same.jpg', size: 10, md5Checksum: 'aaa' },
    { id: 'a', name: 'same.jpg', size: 10, md5Checksum: 'aaa' },
    { id: 'b', name: 'same.jpg', size: 10, md5Checksum: 'bbb' },
    { id: 'c', name: 'SAME.JPG', size: 10 },
    { id: 'd', name: 'same__2.jpg', size: 10, md5Checksum: 'ddd' },
    { id: 'e', name: 'other.jpg', size: 10, md5Checksum: 'aaa' },
  ];
  globalThis.__fullProjectStorage.listRootFiles = async () => [...input].reverse();
  const response = await create({ maxFiles: 4 });
  assert.equal(response.status, 201);
  const { job } = await response.json();
  const record = records.get(`${job.id}/job.json`);
  assert.equal(record.files.length, 4);
  assert.equal(job.sourceSummary.duplicateCopiesSkipped, 2);
  assert.equal(new Set(record.files.map((file) => file.name.toLowerCase())).size, 4);
  assert.ok(record.files.some((file) => file.sourceId === 'b'));
  assert.ok(record.files.some((file) => file.sourceId === 'c'));
  assert.ok(record.files.some((file) => file.sourceId === 'd'));
  const ids = record.files.map((file) => file.sourceId);
  globalThis.__fullProjectStorage.listRootFiles = async () => input;
  const again = await (await create({ maxFiles: 4 })).json();
  assert.deepEqual(records.get(`${again.job.id}/job.json`).files.map((file) => file.sourceId), ids);
});

test('Drive source identity is checked before starting compute', async () => {
  setup();
  globalThis.__fullProjectStorage.listRootFiles = async () => [
    { id: 'a', name: 'a.jpg', size: 10, md5Checksum: 'aaa' },
    { id: 'b', name: 'a.jpg', size: 10, md5Checksum: 'bbb' },
  ];
  const { job } = await (await create()).json();
  globalThis.__fullProjectStorage.listRootFiles = async () => [
    { id: 'b', name: 'a.jpg', size: 10, md5Checksum: 'bbb' },
    { id: 'substitute', name: 'a.jpg', size: 10, md5Checksum: 'aaa' },
  ];
  const response = await worker.fetch(new Request(`https://test/v1/jobs/${job.id}/start`, { method: 'POST' }), env);
  assert.equal(response.status, 409);
});

test('runtime picker discovers OpenReef endpoints with sanitized live health', async () => {
  setup();
  const previous = globalThis.fetch;
  const runtimeEnv = { ...env, RUNPOD_ENDPOINT_ID: 'default', RUNPOD_API_KEY: 'private-key' };
  globalThis.fetch = async (input) => {
    if (String(input).endsWith('/graphql')) return Response.json({ data: { myself: { endpoints: [
      { id: 'default', name: 'Current runtime', workersMax: 1, env: ['secret'] },
      { id: 'fast', name: 'OpenReef fast', gpuIds: 'ADA_24', workersMax: 1 },
      { id: 'disabled', name: 'OpenReef disabled', workersMax: 0 },
      { id: 'unrelated', name: 'Other project', workersMax: 1 },
    ] } } });
    if (String(input).includes('/fast/health')) throw new Error('Health unavailable');
    return Response.json({ workers: { idle: 1, running: 0, initializing: 0 }, jobs: { inQueue: 2 }, env: ['secret'] });
  };
  try {
    const response = await worker.fetch(new Request('https://test/v1/runpod/endpoints'), runtimeEnv);
    const body = await response.json();
    assert.deepEqual(body.endpoints.map((item) => item.id), ['default', 'disabled', 'fast']);
    assert.equal(body.endpoints[0].health.idle, 1);
    assert.equal(body.endpoints[2].health, undefined);
    assert.equal(JSON.stringify(body).includes('secret'), false);
    assert.equal(JSON.stringify(body).includes('private-key'), false);
    const restricted = await worker.fetch(new Request('https://test/v1/runpod/endpoints'), { ...runtimeEnv, RUNPOD_ALLOWED_ENDPOINT_IDS: 'fast' });
    assert.deepEqual((await restricted.json()).endpoints.map((item) => item.id), ['default', 'fast']);
  } finally { globalThis.fetch = previous; }
});

test('selected runtime is persisted and used for submission and status after the default changes', async () => {
  setup();
  const previous = globalThis.fetch;
  const runtimeEnv = { ...env, RUNPOD_ENDPOINT_ID: 'default', RUNPOD_API_KEY: 'private-key' };
  const urls = [];
  let enabled = true;
  globalThis.fetch = async (input) => {
    const url = String(input);
    urls.push(url);
    if (url.endsWith('/graphql')) return Response.json({ data: { myself: { endpoints: [
      { id: 'fast', name: 'OpenReef fast', workersMax: enabled ? 1 : 0 },
    ] } } });
    if (url.endsWith('/fast/run')) return Response.json({ id: 'run-id' });
    if (url.endsWith('/fast/status/run-id')) return Response.json({ status: 'IN_PROGRESS' });
    throw new Error(`Unexpected runtime URL: ${url}`);
  };
  const createSelected = (id) => worker.fetch(new Request('https://test/v1/jobs', {
    method: 'POST', body: JSON.stringify({ name: 'survey', sourceFolder: 'images', runpodEndpointId: id }),
  }), runtimeEnv);
  try {
    assert.equal((await createSelected('unrelated')).status, 400);
    const { job } = await (await createSelected('fast')).json();
    assert.equal(records.get(`${job.id}/job.json`).runpodEndpointId, 'fast');
    assert.equal(job.runtime.name, 'OpenReef fast');
    const started = await worker.fetch(new Request(`https://test/v1/jobs/${job.id}/start`, { method: 'POST' }), runtimeEnv);
    assert.equal(started.status, 202);
    const status = await worker.fetch(new Request(`https://test/v1/jobs/${job.id}`), { ...runtimeEnv, RUNPOD_ENDPOINT_ID: 'new-default' });
    assert.equal(status.status, 200);
    assert.ok(urls.some((url) => url.endsWith('/fast/run')));
    assert.ok(urls.some((url) => url.endsWith('/fast/status/run-id')));
    enabled = false;
    assert.equal((await createSelected('fast')).status, 409);
  } finally { globalThis.fetch = previous; }
});

test('runtime discovery outages retain the default with explicitly unknown health', async () => {
  setup();
  const previous = globalThis.fetch;
  globalThis.fetch = async () => { throw new Error('Offline'); };
  try {
    const response = await worker.fetch(new Request('https://test/v1/runpod/endpoints'), { ...env, RUNPOD_ENDPOINT_ID: 'default' });
    const body = await response.json();
    assert.equal(body.endpoints[0].id, 'default');
    assert.equal(body.endpoints[0].health, undefined);
    assert.ok(body.warning);
  } finally { globalThis.fetch = previous; }
});
