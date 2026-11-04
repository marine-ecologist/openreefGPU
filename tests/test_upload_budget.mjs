import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import test from 'node:test';

const require = createRequire(import.meta.url);
let ts;
try { ts = require('typescript'); }
catch { ts = createRequire(new URL('../cloud/api/package.json', import.meta.url))('typescript'); }
const compile = (source) => ts.transpileModule(source, { compilerOptions: {
  target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext,
} }).outputText;
const dataUrl = (source) => `data:text/javascript;base64,${Buffer.from(source).toString('base64')}`;
const storage = dataUrl(compile(readFileSync(new URL('../cloud/api/src/storage.ts', import.meta.url), 'utf8')));
const api = compile(readFileSync(new URL('../cloud/api/src/index.ts', import.meta.url), 'utf8')).replace("'./storage'", JSON.stringify(storage));
const { default: worker } = await import(dataUrl(api));

test('actual Drive adapter stays within 50 subrequests for 1500-photo upload preparation', async () => {
  const files = new Map();
  let requests = 0;
  let sessions = 0;
  const oldFetch = globalThis.fetch;
  globalThis.fetch = async (input, options = {}) => {
    requests++;
    assert.ok(requests <= 50, 'Worker exceeded 50 subrequests');
    const url = new URL(input);
    const method = options.method ?? 'GET';
    if (url.hostname === 'oauth2.googleapis.com') return Response.json({ access_token: 'fake', expires_in: 3600 });
    assert.equal(url.hostname, 'www.googleapis.com');
    if (method === 'GET' && url.searchParams.has('q')) {
      const q = url.searchParams.get('q');
      const parent = q.match(/^'([^']+)' in parents/)[1];
      const name = q.match(/name = '([^']+)'/)?.[1];
      return Response.json({ files: [...files.values()].filter((file) => file.parents.includes(parent) && (!name || file.name === name)) });
    }
    if (method === 'POST' && url.searchParams.get('uploadType') === 'resumable') {
      sessions++;
      return new Response(null, { headers: { Location: 'https://google.test/session' } });
    }
    if (method === 'POST') {
      const file = { ...JSON.parse(options.body), id: `file-${files.size}` };
      files.set(file.id, file);
      return Response.json(file);
    }
    const id = url.pathname.split('/').pop();
    const file = files.get(id);
    assert.ok(file, `Unknown file ${id}`);
    if (method === 'PATCH') { file.content = JSON.parse(options.body); return Response.json({}); }
    if (url.searchParams.get('alt') === 'media') return Response.json(file.content);
    throw new Error('Unexpected Google request');
  };
  const env = {
    ALLOWED_ORIGINS: '', STORAGE_PROVIDER: 'google-drive', REQUIRE_ACCESS: 'false',
    GOOGLE_CLIENT_ID: 'fake', GOOGLE_CLIENT_SECRET: 'fake', GOOGLE_REFRESH_TOKEN: 'fake', GOOGLE_DRIVE_ROOT_FOLDER_ID: 'root',
    OPENREEF_VERSION: '0.6.5', OPENREEF_GPU_VERSION: '0.6.5-gpu.6',
  };
  try {
    const response = await worker.fetch(new Request('https://test/v1/jobs', {
      method: 'POST', body: JSON.stringify({ name: 'large survey', uploadProtocol: 'per-file',
        files: Array.from({ length: 1500 }, (_, index) => ({ name: `photo-${index}.jpg`, size: 10 })),
      }),
    }), env);
    const body = await response.json();
    assert.equal(response.status, 201, JSON.stringify(body));
    assert.equal(sessions, 0);
    const preparationRequests = requests;
    requests = 0;
    const session = await worker.fetch(new Request(`https://test${body.uploadSessionPath}`, {
      method: 'POST', body: JSON.stringify({ fileIndex: 1499 }),
    }), env);
    assert.equal(session.status, 200, JSON.stringify(await session.json()));
    assert.equal(sessions, 1);
    console.log(`Drive subrequests: create job ${preparationRequests}; create one upload session ${requests}`);
  } finally { globalThis.fetch = oldFetch; }
});
