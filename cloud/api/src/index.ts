import { StorageError, storageForEnv, type StorageEnv } from './storage';

interface Env extends StorageEnv {
  ALLOWED_ORIGINS: string;
  REQUIRE_ACCESS?: string;
  OPENREEF_VERSION: string;
  OPENREEF_GPU_VERSION: string;
  RUNPOD_ENDPOINT_ID: string;
  RUNPOD_API_KEY: string;
}

type JobState =
  | 'uploading'
  | 'queued'
  | 'running'
  | 'completed'
  | 'failed'
  | 'cancelled';

interface JobFile {
  name: string;
  size: number;
  contentType: string;
}

interface TransferMetric {
  state: 'running' | 'completed';
  startedAt: string;
  completedAt?: string;
  filesCompleted: number;
  filesTotal: number;
  bytesCompleted: number;
  bytesTotal: number;
  bytesPerSecond: number;
  elapsedSeconds: number;
  currentFile?: string;
  cacheHits?: number;
  filesDownloaded?: number;
}

interface TimingMetric {
  name?: string;
  state: 'running' | 'completed' | 'failed';
  startedAt: string;
  completedAt?: string;
  elapsedSeconds: number;
}

interface JobRecord {
  schemaVersion: 1;
  id: string;
  name: string;
  state: JobState;
  stage: string;
  progress: number;
  createdAt: string;
  updatedAt: string;
  files: JobFile[];
  source?: {
    kind: 'job-upload' | 'storage-folder';
    folder: string;
  };
  versions: {
    openreef: string;
    openreefGPU: string;
  };
  runpodJobId?: string;
  error?: string;
  result?: {
    model: string;
    manifest: string;
  };
  transfers?: {
    sourceDownload?: TransferMetric;
  };
  timings?: {
    total: TimingMetric;
    steps: TimingMetric[];
  };
}

const RUNPOD_ROOT = 'https://api.runpod.ai/v2';
const MAX_FILES = 2_000;
const MAX_FILE_SIZE = 2 * 1024 ** 3;
const MAX_TOTAL_SIZE = 50 * 1024 ** 3;
const IMAGE_EXTENSIONS = new Set(['jpg', 'jpeg', 'png', 'tif', 'tiff', 'webp']);

const worker = {
  async fetch(request: Request, env: Env): Promise<Response> {
    const origin = request.headers.get('Origin');
    const cors = corsHeaders(origin, env);
    if (request.method === 'OPTIONS')
      return new Response(null, { headers: cors });

    try {
      requireAllowedOrigin(origin, env);
      requireAccess(request, env);
      const url = new URL(request.url);
      const parts = url.pathname.split('/').filter(Boolean);

      if (request.method === 'GET' && url.pathname === '/v1/health') {
        return json(
          {
            ok: true,
            service: 'openreef-gpu-api',
            versions: deploymentVersions(env),
          },
          200,
          cors,
        );
      }
      if (request.method === 'POST' && url.pathname === '/v1/jobs') {
        return json(await createJob(request, env), 201, cors);
      }
      if (parts[0] === 'v1' && parts[1] === 'jobs' && isJobId(parts[2])) {
        const jobId = parts[2];
        if (
          request.method === 'POST' &&
          parts[3] === 'start' &&
          parts.length === 4
        ) {
          return json(await startJob(jobId, env), 202, cors);
        }
        if (request.method === 'GET' && parts.length === 3) {
          return json(await getJob(jobId, env), 200, cors);
        }
        if (
          request.method === 'GET' &&
          parts[3] === 'assets' &&
          parts.length === 5
        ) {
          return proxyAsset(
            request,
            env,
            jobId,
            decodeURIComponent(parts[4]),
            cors,
          );
        }
      }
      return json({ error: 'Not found' }, 404, cors);
    } catch (error) {
      const status =
        error instanceof HttpError || error instanceof StorageError
          ? error.status
          : 500;
      const message =
        error instanceof Error ? error.message : 'Unexpected error';
      if (status >= 500) console.error(error);
      return json({ error: message }, status, cors);
    }
  },
};

export default worker;

async function createJob(request: Request, env: Env) {
  const preparationStartedAt = new Date().toISOString();
  const preparationStarted = Date.now();
  const body = await parseJson<{
    name?: unknown;
    files?: unknown;
    sourceFolder?: unknown;
    maxFiles?: unknown;
  }>(request);
  const name = cleanDatasetName(body.name);
  const storage = storageForEnv(env);
  const sourceFolder =
    body.sourceFolder === undefined
      ? undefined
      : cleanSourceFolder(body.sourceFolder);
  const source = sourceFolder
    ? { kind: 'storage-folder' as const, folder: sourceFolder }
    : { kind: 'job-upload' as const, folder: '' };
  const files = sourceFolder
    ? await filesFromStorageFolder(storage, sourceFolder, body.maxFiles)
    : validateFiles(body.files);
  const id = crypto.randomUUID();
  const now = preparationStartedAt;
  const record: JobRecord = {
    schemaVersion: 1,
    id,
    name,
    state: 'uploading',
    stage: 'Uploading source images',
    progress: 0,
    createdAt: now,
    updatedAt: now,
    files,
    source,
    versions: deploymentVersions(env),
    timings: {
      total: {
        state: 'running',
        startedAt: preparationStartedAt,
        elapsedSeconds: 0,
      },
      steps: [
        {
          name: 'Preparing job',
          state: 'running',
          startedAt: preparationStartedAt,
          elapsedSeconds: 0,
        },
      ],
    },
  };

  await storage.ensureFolder(jobFolder(id));
  if (!sourceFolder) await storage.ensureFolder(`${jobFolder(id)}/input`);
  await storage.ensureFolder(`${jobFolder(id)}/output`);
  await writeJob(env, record);

  const uploads = sourceFolder
    ? []
    : await Promise.all(
        files.map(async (file) => {
          const session = await storage.createBrowserUpload(
            `${jobFolder(id)}/input/${file.name}`,
            file.size,
            file.contentType,
          );
          return {
            name: file.name,
            size: file.size,
            uploadUrl: session.uploadUrl,
            expiresAt: session.expiresAt,
          };
        }),
      );
  const preparedAt = new Date().toISOString();
  const preparation = record.timings!.steps[0];
  preparation.state = 'completed';
  preparation.completedAt = preparedAt;
  preparation.elapsedSeconds = (Date.now() - preparationStarted) / 1000;
  record.timings!.total.elapsedSeconds = preparation.elapsedSeconds;
  if (!sourceFolder) {
    record.timings!.steps.push({
      name: 'Browser → Drive upload',
      state: 'running',
      startedAt: preparedAt,
      elapsedSeconds: 0,
    });
  }
  record.updatedAt = preparedAt;
  await writeJob(env, record);
  return { job: publicJob(record, env), uploads };
}

async function startJob(jobId: string, env: Env) {
  let job = await readJob(env, jobId);
  if (job.state !== 'uploading') {
    throw new HttpError(409, `Job is already ${job.state}`);
  }
  const storage = storageForEnv(env);
  const children =
    job.source?.kind === 'storage-folder'
      ? await storage.listRootFiles(job.source.folder)
      : await storage.listFiles(`${jobFolder(jobId)}/input`);
  const uploaded = new Map(
    children.map((item) => [item.name.toLowerCase(), item.size]),
  );
  const missing = job.files.filter(
    (file) => uploaded.get(file.name.toLowerCase()) !== file.size,
  );
  if (missing.length) {
    throw new HttpError(
      409,
      `${missing.length} source file(s) are not fully uploaded`,
    );
  }

  const response = await fetch(`${RUNPOD_ROOT}/${env.RUNPOD_ENDPOINT_ID}/run`, {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${env.RUNPOD_API_KEY}`,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      input: {
        schema_version: 1,
        job_id: jobId,
        name: job.name,
        profile: 'compact',
        openreef_version: job.versions.openreef,
        openreef_gpu_version: job.versions.openreefGPU,
      },
      policy: {
        executionTimeout: 172_800_000,
        ttl: 259_200_000,
      },
    }),
  });
  const result = await response.json<{ id?: string; error?: string }>();
  if (!response.ok || !result.id) {
    throw new HttpError(502, result.error || 'Runpod rejected the job');
  }
  const queuedAt = new Date().toISOString();
  finishActiveTiming(job, queuedAt);
  job.timings ??= {
    total: {
      state: 'running',
      startedAt: job.createdAt,
      elapsedSeconds: 0,
    },
    steps: [],
  };
  job.timings.steps.push({
    name: 'Waiting for GPU worker',
    state: 'running',
    startedAt: queuedAt,
    elapsedSeconds: 0,
  });
  job.timings.total.elapsedSeconds = elapsedSeconds(
    job.timings.total.startedAt,
    queuedAt,
  );
  job = {
    ...job,
    state: 'queued',
    stage: 'Waiting for a GPU worker',
    progress: 1,
    runpodJobId: result.id,
    updatedAt: new Date().toISOString(),
  };
  await writeJob(env, job);
  return publicJob(job, env);
}

async function getJob(jobId: string, env: Env) {
  let job = await readJob(env, jobId);
  if (
    job.runpodJobId &&
    !['completed', 'failed', 'cancelled'].includes(job.state)
  ) {
    const status = await runpodStatus(env, job.runpodJobId);
    const state = mapRunpodState(status.status);
    if (state && state !== job.state) {
      // The GPU worker writes richer stage/progress updates. Only persist a
      // provider terminal state when it has not already done so.
      job = await readJob(env, jobId);
      if (!['completed', 'failed', 'cancelled'].includes(job.state)) {
        job.state = state;
        job.stage = state === 'running' ? 'Reconstructing reef model' : state;
        job.progress =
          state === 'running' ? Math.max(job.progress, 3) : job.progress;
        if (state === 'failed')
          job.error = status.error || 'GPU processing failed';
        job.updatedAt = new Date().toISOString();
        await writeJob(env, job);
      }
    }
  }
  return publicJob(job, env);
}

async function proxyAsset(
  request: Request,
  env: Env,
  jobId: string,
  fileName: string,
  cors: Headers,
) {
  if (!/^[A-Za-z0-9._-]+$/.test(fileName))
    throw new HttpError(400, 'Invalid asset name');
  const job = await readJob(env, jobId);
  const allowed = new Set(
    [job.result?.model, job.result?.manifest].filter(Boolean),
  );
  if (job.state !== 'completed' || !allowed.has(fileName)) {
    throw new HttpError(404, 'Asset not found');
  }
  const upstream = await storageForEnv(env).readFile(
    `${jobFolder(jobId)}/output/${fileName}`,
    request.headers.get('Range'),
  );
  if (!upstream.ok && upstream.status !== 206) {
    throw new HttpError(
      upstream.status === 404 ? 404 : 502,
      'Could not read result asset from cloud storage',
    );
  }
  const responseHeaders = new Headers(cors);
  for (const name of [
    'Content-Type',
    'Content-Length',
    'Content-Range',
    'Accept-Ranges',
    'ETag',
  ]) {
    const value = upstream.headers.get(name);
    if (value) responseHeaders.set(name, value);
  }
  responseHeaders.set('Cache-Control', 'private, max-age=3600');
  return new Response(upstream.body, {
    status: upstream.status,
    headers: responseHeaders,
  });
}

async function runpodStatus(env: Env, id: string) {
  const response = await fetch(
    `${RUNPOD_ROOT}/${env.RUNPOD_ENDPOINT_ID}/status/${id}`,
    {
      headers: { Authorization: `Bearer ${env.RUNPOD_API_KEY}` },
    },
  );
  const payload = await response.json<{ status?: string; error?: string }>();
  if (!response.ok)
    throw new HttpError(502, 'Could not read Runpod job status');
  return payload;
}

function mapRunpodState(value?: string): JobState | undefined {
  switch (value) {
    case 'IN_QUEUE':
      return 'queued';
    case 'IN_PROGRESS':
      return 'running';
    case 'COMPLETED':
      return 'completed';
    case 'FAILED':
    case 'TIMED_OUT':
      return 'failed';
    case 'CANCELLED':
      return 'cancelled';
    default:
      return undefined;
  }
}

function publicJob(job: JobRecord, env: Env) {
  const assetBase = `/v1/jobs/${job.id}/assets`;
  const storage = storageForEnv(env);
  return {
    id: job.id,
    name: job.name,
    state: job.state,
    stage: job.stage,
    progress: job.progress,
    createdAt: job.createdAt,
    updatedAt: job.updatedAt,
    error: job.error,
    transfers: job.transfers,
    timings: job.timings,
    result: job.result
      ? {
          modelUrl: `${assetBase}/${encodeURIComponent(job.result.model)}`,
          manifestUrl: `${assetBase}/${encodeURIComponent(job.result.manifest)}`,
        }
      : undefined,
    versions: job.versions,
    storage: {
      provider: storage.provider,
      root: storage.publicRoot,
    },
  };
}

function finishActiveTiming(job: JobRecord, completedAt: string) {
  const steps = job.timings?.steps ?? [];
  let active: TimingMetric | undefined;
  for (let index = steps.length - 1; index >= 0; index -= 1) {
    if (steps[index].state === 'running') {
      active = steps[index];
      break;
    }
  }
  if (!active) return;
  active.state = 'completed';
  active.completedAt = completedAt;
  active.elapsedSeconds = elapsedSeconds(active.startedAt, completedAt);
}

function elapsedSeconds(startedAt: string, completedAt: string) {
  return Math.max(
    0,
    (new Date(completedAt).getTime() - new Date(startedAt).getTime()) / 1000,
  );
}

function deploymentVersions(env: Env) {
  if (!env.OPENREEF_VERSION || !env.OPENREEF_GPU_VERSION) {
    throw new HttpError(500, 'Deployment version settings are missing');
  }
  return {
    openreef: env.OPENREEF_VERSION,
    openreefGPU: env.OPENREEF_GPU_VERSION,
  };
}

async function readJob(env: Env, jobId: string): Promise<JobRecord> {
  return storageForEnv(env).readJson<JobRecord>(`${jobFolder(jobId)}/job.json`);
}

async function writeJob(env: Env, job: JobRecord): Promise<void> {
  await storageForEnv(env).writeJson(`${jobFolder(job.id)}/job.json`, job);
}

function validateFiles(value: unknown): JobFile[] {
  if (!Array.isArray(value) || value.length < 2 || value.length > MAX_FILES) {
    throw new HttpError(400, `Choose between 2 and ${MAX_FILES} source images`);
  }
  let total = 0;
  const names = new Set<string>();
  return value.map((raw) => {
    if (!raw || typeof raw !== 'object')
      throw new HttpError(400, 'Invalid file metadata');
    const item = raw as Record<string, unknown>;
    const name = cleanFileName(item.name);
    const size = Number(item.size);
    const contentType =
      typeof item.contentType === 'string'
        ? item.contentType
        : 'application/octet-stream';
    const extension = name.split('.').pop()?.toLowerCase() || '';
    if (!IMAGE_EXTENSIONS.has(extension))
      throw new HttpError(400, `${name} is not a supported image`);
    if (!Number.isSafeInteger(size) || size < 1 || size > MAX_FILE_SIZE)
      throw new HttpError(400, `${name} has an invalid size`);
    const key = name.toLowerCase();
    if (names.has(key))
      throw new HttpError(400, `Duplicate file name: ${name}`);
    names.add(key);
    total += size;
    if (total > MAX_TOTAL_SIZE)
      throw new HttpError(413, 'The upload is larger than 50 GiB');
    return { name, size, contentType };
  });
}

async function filesFromStorageFolder(
  storage: ReturnType<typeof storageForEnv>,
  folder: string,
  limitValue: unknown,
): Promise<JobFile[]> {
  const limit =
    limitValue === undefined
      ? MAX_FILES
      : typeof limitValue === 'number' || typeof limitValue === 'string'
        ? Number.parseInt(`${limitValue}`, 10)
        : Number.NaN;
  if (!Number.isSafeInteger(limit) || limit < 2 || limit > MAX_FILES) {
    throw new HttpError(400, `Photo limit must be between 2 and ${MAX_FILES}`);
  }
  const items = (await storage.listRootFiles(folder))
    .filter((file) =>
      IMAGE_EXTENSIONS.has(file.name.split('.').pop()?.toLowerCase() || ''),
    )
    .sort((left, right) => left.name.localeCompare(right.name))
    .slice(0, limit)
    .map((file) => ({
      name: file.name,
      size: file.size,
      contentType: 'application/octet-stream',
    }));
  return validateFiles(items);
}

function cleanSourceFolder(value: unknown): string {
  if (typeof value !== 'string')
    throw new HttpError(400, 'Enter a Drive source folder');
  const path = value
    .normalize('NFKC')
    .replace(/^\/+|\/+$/g, '')
    .trim();
  const parts = path.split('/');
  if (
    !path ||
    path.length > 300 ||
    parts.some(
      (part) =>
        !part ||
        part === '.' ||
        part === '..' ||
        /[<>:"\\|?*]/.test(part) ||
        containsControlCharacter(part),
    )
  ) {
    throw new HttpError(400, 'Invalid Drive source folder');
  }
  return parts.join('/');
}

function containsControlCharacter(value: string): boolean {
  for (let index = 0; index < value.length; index += 1) {
    if (value.charCodeAt(index) < 32) return true;
  }
  return false;
}

function cleanFileName(value: unknown): string {
  if (typeof value !== 'string') throw new HttpError(400, 'Invalid file name');
  const name = value
    .normalize('NFKC')
    .replace(/[^A-Za-z0-9._ -]/g, '_')
    .trim();
  if (!name || name.length > 180 || name === '.' || name === '..')
    throw new HttpError(400, 'Invalid file name');
  return name;
}

function cleanDatasetName(value: unknown): string {
  if (typeof value !== 'string')
    throw new HttpError(400, 'Enter a dataset name');
  const result = value
    .normalize('NFKC')
    .replace(/[<>:"/\\|?*]/g, '-')
    .trim();
  if (!result || result.length > 80)
    throw new HttpError(400, 'Dataset name must be 1–80 characters');
  return result;
}

function jobFolder(id: string): string {
  return id;
}

function isJobId(value?: string): value is string {
  return Boolean(
    value &&
    /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(
      value,
    ),
  );
}

function corsHeaders(origin: string | null, env: Env): Headers {
  const headers = new Headers({
    'Access-Control-Allow-Headers': 'Content-Type, Authorization, Range',
    'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
    'Access-Control-Expose-Headers':
      'Content-Length, Content-Range, Accept-Ranges, ETag',
    'Access-Control-Max-Age': '86400',
    Vary: 'Origin',
  });
  if (origin && allowedOrigins(env).has(origin)) {
    headers.set('Access-Control-Allow-Origin', origin);
    headers.set('Access-Control-Allow-Credentials', 'true');
  }
  return headers;
}

function allowedOrigins(env: Env): Set<string> {
  return new Set(
    (env.ALLOWED_ORIGINS || '')
      .split(',')
      .map((item) => item.trim())
      .filter(Boolean),
  );
}

function requireAllowedOrigin(origin: string | null, env: Env) {
  if (origin && !allowedOrigins(env).has(origin))
    throw new HttpError(403, 'Origin is not allowed');
}

function requireAccess(request: Request, env: Env) {
  if (env.REQUIRE_ACCESS !== 'true') return;
  if (!request.headers.get('Cf-Access-Authenticated-User-Email')) {
    throw new HttpError(401, 'Sign in through Cloudflare Access');
  }
}

async function parseJson<T>(request: Request): Promise<T> {
  try {
    return await request.json<T>();
  } catch {
    throw new HttpError(400, 'Expected a JSON request body');
  }
}

function json(value: unknown, status: number, cors: Headers): Response {
  const headers = new Headers(cors);
  headers.set('Content-Type', 'application/json; charset=utf-8');
  headers.set('Cache-Control', 'no-store');
  return new Response(JSON.stringify(value), { status, headers });
}

class HttpError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}
