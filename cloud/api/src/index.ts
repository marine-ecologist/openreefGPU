import { StorageError, storageForEnv, type StorageEnv } from './storage';
import { VIDEO_PART_BYTES, type RunpodStorage } from './runpod-storage';

interface Env extends StorageEnv {
  ALLOWED_ORIGINS: string;
  REQUIRE_ACCESS?: string;
  OPENREEF_VERSION: string;
  OPENREEF_GPU_VERSION: string;
  RUNPOD_ENDPOINT_ID: string;
  RUNPOD_API_KEY: string;
  RUNPOD_ALLOWED_ENDPOINT_IDS?: string;
}

type JobState =
  | 'uploading'
  | 'queued'
  | 'running'
  | 'review'
  | 'completed'
  | 'failed'
  | 'cancelled'
  | 'purged';

interface JobFile {
  name: string;
  size: number;
  contentType: string;
  cameraId?: string;
  sourceId?: string;
  sourceName?: string;
  sourceMd5?: string;
}

interface StereoConfig {
  leftFolder: string;
  rightFolder: string;
  profile: {
    schemaVersion: number;
    cameras: { id: string; width: number; height: number; model: string; params: number[] }[];
    translationUnit: string;
    rightFromLeft: { rotation: number[][]; translation: number[] };
  };
  maxDeltaMs: number;
  rightOffsetMs: number;
  overlap: number;
  loopInterval: number;
  loopNeighbors: number;
  timestamps?: Record<string, Record<string, number>>;
}

interface StereoVideoConfig {
  referenceCamera: 'A' | 'B';
  originalNames: string[];
  intervalSeconds: number;
  maxPairs: number;
  jpegQuality: number;
  reviewPairs: boolean;
  colourCorrection: boolean;
  profile?: StereoConfig['profile'];
}

interface StageAsset {
  stage: string;
  name: string;
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
  outputMode?: 'compact' | 'full';
  includeSourceImages?: boolean;
  stereo?: StereoConfig;
  stereoVideo?: StereoVideoConfig;
  volumeUploads?: Record<number, string>;
  purgeJobId?: string;
  videoUploads?: Record<number, { uploadUrl: string; expiresAt: string }>;
  stageAssets?: StageAsset[];
  syncWarnings?: string[];
  pairCount?: number;
  excludedPairs?: number[];
  reviewed?: boolean;
  sourceSummary?: { duplicateCopiesSkipped: number; renamedFiles: number };
  source?: {
    kind: 'job-upload' | 'storage-folder';
    folder: string;
  };
  versions: {
    openreef: string;
    openreefGPU: string;
  };
  runpodJobId?: string;
  runpodEndpointId?: string;
  runpodEndpointName?: string;
  error?: string;
  result?: {
    model: string;
    manifest: string;
    project?: string;
    gaussian?: string;
    bundle?: string;
    stereo_diagnostics?: string;
    rig_profile?: string;
    stereo_matching?: string;
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
      if (request.method === 'GET' && url.pathname === '/v1/jobs') {
        return json(await listJobs(env, url.searchParams.get('cursor') ?? undefined), 200, cors);
      }
      if (request.method === 'GET' && url.pathname === '/v1/runpod/endpoints') {
        return json(await listRunpodEndpoints(env), 200, cors);
      }
      if (request.method === 'POST' && url.pathname === '/v1/jobs') {
        return json(await createJob(request, env), 201, cors);
      }
      if (parts[0] === 'v1' && parts[1] === 'jobs' && isJobId(parts[2])) {
        const jobId = parts[2];
        if (parts[3] === 'uploads' && parts.length === 6 && request.method === 'PUT') {
          return json(await uploadVolumePart(request, env, jobId, Number(parts[4]), Number(parts[5])), 200, cors);
        }
        if (parts[3] === 'purge' && parts.length === 4 && request.method === 'POST') {
          return json(await purgeJob(request, env, jobId), 202, cors);
        }
        if (parts[3] === 'uploads' && parts.length === 4) {
          if (request.method === 'GET') return json(await videoUploadProgress(jobId, env), 200, cors);
          if (request.method === 'POST') return json(await createUploadSession(request, jobId, env), 200, cors);
        }
        if (
          request.method === 'POST' &&
          parts[3] === 'start' &&
          parts.length === 4
        ) {
          return json(await startJob(request, jobId, env), 202, cors);
        }
        if (request.method === 'GET' && parts.length === 3) {
          return json(await getJob(jobId, env), 200, cors);
        }
        if (
          request.method === 'GET' &&
          parts[3] === 'assets' &&
          parts.length === 5
        ) {
          return await proxyAsset(
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

type RunpodEndpoint = {
  id: string;
  name: string;
  gpuIds?: string;
  workersMin?: number;
  workersMax?: number;
};

async function runpodEndpoints(env: Env): Promise<RunpodEndpoint[]> {
  let response: Response;
  try {
    response = await fetch('https://api.runpod.io/graphql', {
      method: 'POST',
      headers: { Authorization: `Bearer ${env.RUNPOD_API_KEY}`, 'Content-Type': 'application/json' },
      body: JSON.stringify({ query: 'query { myself { endpoints { id name gpuIds workersMin workersMax } } }' }),
      signal: AbortSignal.timeout(10_000),
    });
  } catch {
    throw new HttpError(502, 'Could not list RunPod endpoints');
  }
  const payload = await response.json<{ data?: { myself?: { endpoints?: RunpodEndpoint[] } }; errors?: unknown[] }>();
  if (!response.ok || payload.errors?.length || !Array.isArray(payload.data?.myself?.endpoints)) {
    throw new HttpError(502, 'Could not list RunPod endpoints; check the server API key permissions');
  }
  const configured = env.RUNPOD_ALLOWED_ENDPOINT_IDS?.split(',').map((id) => id.trim()).filter(Boolean);
  const allowed = new Set([env.RUNPOD_ENDPOINT_ID, ...(configured ?? [])]);
  // An explicit allowlist restricts discovery. Otherwise discover OpenReef-
  // named endpoints only, keeping unrelated account workloads out of the UI.
  return payload.data.myself.endpoints
    .filter((item) => typeof item.id === 'string' && /^[A-Za-z0-9_-]{1,100}$/.test(item.id)
      && (allowed.has(item.id) || (!configured?.length && /^openreef(?:\b|[-_])/i.test(item.name ?? ''))))
    .map((item) => ({
      id: item.id, name: item.name || item.id,
      gpuIds: typeof item.gpuIds === 'string' ? item.gpuIds : undefined,
      workersMin: typeof item.workersMin === 'number' ? item.workersMin : undefined,
      workersMax: typeof item.workersMax === 'number' ? item.workersMax : undefined,
    }))
    .sort((a, b) => Number(b.id === env.RUNPOD_ENDPOINT_ID) - Number(a.id === env.RUNPOD_ENDPOINT_ID) || a.name.localeCompare(b.name));
}

async function listRunpodEndpoints(env: Env) {
  let endpoints: RunpodEndpoint[];
  let warning: string | undefined;
  try { endpoints = await runpodEndpoints(env); }
  catch {
    endpoints = env.RUNPOD_ENDPOINT_ID ? [{ id: env.RUNPOD_ENDPOINT_ID, name: 'Default OpenReef endpoint' }] : [];
    warning = 'Endpoint discovery unavailable. Only the configured default is shown.';
  }
  const readHealth = async (endpoint: RunpodEndpoint) => {
    let health: { idle: number; running: number; initializing: number; queued: number } | undefined;
    try {
      const response = await fetch(`${RUNPOD_ROOT}/${endpoint.id}/health`, {
        headers: { Authorization: `Bearer ${env.RUNPOD_API_KEY}` }, signal: AbortSignal.timeout(5_000),
      });
      if (response.ok) {
        const body = await response.json<{ workers?: Record<string, number>; jobs?: Record<string, number> }>();
        const count = (value: unknown) => typeof value === 'number' && Number.isFinite(value) ? Math.max(0, value) : 0;
        if (body.workers) health = {
          idle: count(body.workers.idle), running: count(body.workers.running),
          initializing: count(body.workers.initializing), queued: count(body.jobs?.inQueue),
        };
      }
    } catch { /* A failed health check is unknown, not zero available workers. */ }
    return { ...endpoint, isDefault: endpoint.id === env.RUNPOD_ENDPOINT_ID, health };
  };
  const entries = [];
  for (let start = 0; start < Math.min(endpoints.length, 12); start += 3) {
    entries.push(...await Promise.all(endpoints.slice(start, Math.min(start + 3, 12)).map(readHealth)));
  }
  return { endpoints: entries, checkedAt: new Date().toISOString(), warning,
    truncated: endpoints.length > 12 };
}

async function createJob(request: Request, env: Env) {
  const preparationStartedAt = new Date().toISOString();
  const preparationStarted = Date.now();
  const body = await parseJson<{
    name?: unknown;
    files?: unknown;
    stereo?: unknown;
    stereoVideo?: unknown;
    requireRunpod?: unknown;
    sourceFolder?: unknown;
    maxFiles?: unknown;
    outputMode?: unknown;
    includeSourceImages?: unknown;
    uploadProtocol?: unknown;
    runpodEndpointId?: unknown;
  }>(request);
  if (body.uploadProtocol !== undefined && body.uploadProtocol !== 'per-file') {
    throw new HttpError(400, 'Unsupported upload protocol');
  }
  if (body.outputMode !== undefined && body.outputMode !== 'compact' && body.outputMode !== 'full') {
    throw new HttpError(400, 'outputMode must be compact or full');
  }
  if (body.includeSourceImages !== undefined && typeof body.includeSourceImages !== 'boolean') {
    throw new HttpError(400, 'includeSourceImages must be a boolean');
  }
  if (body.includeSourceImages === true && body.outputMode !== 'full') {
    throw new HttpError(400, 'Source photographs can only be included with Full project');
  }
  const stereo = body.stereo === undefined ? undefined : validateStereo(body.stereo);
  if (stereo && (body.sourceFolder !== undefined || body.files !== undefined)) {
    throw new HttpError(400, 'Stereo sources are specified by leftFolder and rightFolder');
  }
  const video = body.stereoVideo === undefined ? undefined : validateStereoVideo(body.stereoVideo, body.files);
  if (video && (stereo || body.sourceFolder !== undefined || body.uploadProtocol !== 'per-file')) {
    throw new HttpError(400, 'Stereo video requires two direct video uploads');
  }
  const name = cleanDatasetName(body.name);
  let endpointId = env.RUNPOD_ENDPOINT_ID;
  let endpointName = 'Default OpenReef endpoint';
  if (body.runpodEndpointId !== undefined && body.runpodEndpointId !== '') {
    if (typeof body.runpodEndpointId !== 'string' || !/^[A-Za-z0-9_-]{1,100}$/.test(body.runpodEndpointId)) {
      throw new HttpError(400, 'Invalid RunPod endpoint selection');
    }
    const endpoint = (await runpodEndpoints(env)).find((item) => item.id === body.runpodEndpointId);
    if (!endpoint) throw new HttpError(400, 'Selected RunPod endpoint is not enabled for OpenReef');
    if (endpoint.workersMax === 0) throw new HttpError(409, 'Selected RunPod endpoint is disabled');
    endpointId = endpoint.id;
    endpointName = endpoint.name;
  }
  const storage = storageForEnv(env);
  if (body.requireRunpod === true && storage.provider !== 'runpod') {
    throw new HttpError(503, 'This API is not configured for RunPod volume storage');
  }
  if (video && !['runpod', 'google-drive'].includes(storage.provider)) {
    throw new HttpError(400, 'Stereo video uploads require a configured RunPod volume');
  }
  if (storage.provider === 'runpod' && !video) {
    throw new HttpError(400, 'Choose two GoPro MP4s for volume processing');
  }
  if (storage.provider === 'runpod' && endpointId !== env.RUNPOD_ENDPOINT_ID) {
    throw new HttpError(400, 'Choose the endpoint attached to the configured network volume');
  }
  const sourceFolder =
    stereo ? stereo.leftFolder : body.sourceFolder === undefined
      ? undefined
      : cleanSourceFolder(body.sourceFolder);
  const source = sourceFolder
    ? { kind: 'storage-folder' as const, folder: sourceFolder }
    : { kind: 'job-upload' as const, folder: '' };
  const sourceSelection = stereo
    ? await stereoFiles(storage, stereo, body.maxFiles)
    : sourceFolder
    ? await filesFromStorageFolder(storage, sourceFolder, body.maxFiles)
    : undefined;
  const files = video?.files ?? (sourceSelection ? sourceSelection.files : validateFiles(body.files));
  const perFileUploads = !sourceFolder && body.uploadProtocol === 'per-file';
  if (!sourceFolder && !perFileUploads && files.length > 4) {
    throw new HttpError(400, 'Reload the updated website to upload more than four photos using per-file upload sessions');
  }
  const id = crypto.randomUUID();
  const now = preparationStartedAt;
  const record: JobRecord = {
    schemaVersion: 1,
    id,
    name,
    state: 'uploading',
    stage: video ? 'Stage 0 — Upload & prepare' : 'Uploading source images',
    progress: 0,
    createdAt: now,
    updatedAt: now,
    files,
    runpodEndpointId: endpointId,
    runpodEndpointName: endpointName,
    sourceSummary: sourceSelection?.summary,
    stereo,
    stereoVideo: video?.config,
    source,
    outputMode: body.outputMode === 'full' ? 'full' : 'compact',
    includeSourceImages: body.outputMode === 'full' && body.includeSourceImages === true,
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

  const uploads = sourceFolder || perFileUploads
    ? []
    : await (async () => {
        const targets = [];
        for (const file of files) {
          const session = await storage.createBrowserUpload(
            `${jobFolder(id)}/input/${file.name}`,
            file.size,
            file.contentType,
          );
          targets.push({
            name: file.name,
            size: file.size,
            uploadUrl: session.uploadUrl,
            expiresAt: session.expiresAt,
          });
        }
        return targets;
      })();
  const preparedAt = new Date().toISOString();
  const preparation = record.timings!.steps[0];
  preparation.state = 'completed';
  preparation.completedAt = preparedAt;
  preparation.elapsedSeconds = (Date.now() - preparationStarted) / 1000;
  record.timings!.total.elapsedSeconds = preparation.elapsedSeconds;
  if (!sourceFolder) {
    record.timings!.steps.push({
      name: storage.provider === 'runpod' ? 'Browser → RunPod volume upload' : 'Browser → Drive upload',
      state: 'running',
      startedAt: preparedAt,
      elapsedSeconds: 0,
    });
  }
  record.updatedAt = preparedAt;
  await writeJob(env, record);
  return {
    job: publicJob(record, env), uploads,
    uploadSessionPath: perFileUploads ? `/v1/jobs/${id}/uploads` : undefined,
  };
}

async function createUploadSession(request: Request, jobId: string, env: Env) {
  const storage = storageForEnv(env);
  const job = await storage.readJson<JobRecord>(`${jobFolder(jobId)}/job.json`);
  if (job.state !== 'uploading' || job.source?.kind === 'storage-folder') {
    throw new HttpError(409, 'This job is not accepting uploads');
  }
  const body = await parseJson<{ fileIndex?: unknown }>(request);
  if (!Number.isInteger(body.fileIndex) || (body.fileIndex as number) < 0 || (body.fileIndex as number) >= job.files.length) {
    throw new HttpError(400, 'Invalid upload file index');
  }
  const index = body.fileIndex as number;
  const file = job.files[index];
  if (job.stereoVideo && storage.provider === 'runpod') {
    return volumeUploadSession(storage as RunpodStorage, env, job, index);
  }
  if (job.stereoVideo) {
    const existing = (await storage.listFiles(`${jobFolder(jobId)}/input`))
      .find((item) => item.name === file.name && item.size === file.size && item.md5Checksum);
    if (existing) return { name: file.name, size: file.size, offset: file.size };
    const saved = job.videoUploads?.[index];
    const offset = await savedUploadOffset(saved, file.size);
    if (offset !== undefined) return { name: file.name, size: file.size, ...saved, offset };
  }
  const session = await storage.createBrowserUpload(
    `${jobFolder(jobId)}/input/${file.name}`, file.size, file.contentType,
  );
  if (job.stereoVideo) {
    job.videoUploads = { ...job.videoUploads, [index]: session };
    await writeJob(env, job);
  }
  return { name: file.name, size: file.size, ...session, offset: 0 };
}

async function volumeUploadSession(storage: RunpodStorage, env: Env, job: JobRecord, index: number) {
  const file = job.files[index];
  const target = { name: file.name, size: file.size, partBytes: VIDEO_PART_BYTES };
  const path = `${jobFolder(job.id)}/input/${file.name}`;
  if ((await storage.listFiles(`${jobFolder(job.id)}/input`)).some(item =>
    item.name === file.name && item.size === file.size)) return { ...target, offset: file.size };

  let uploadId = job.volumeUploads?.[index];
  if (!uploadId) {
    uploadId = await storage.beginVideo(path);
    job.volumeUploads = { ...job.volumeUploads, [index]: uploadId };
    await writeJob(env, job);
  }
  const parts = await storage.videoParts(path, uploadId);
  const offset = committedVideoBytes(parts, file.size);
  if (offset === file.size) await storage.finishVideo(path, uploadId, parts);
  return { ...target, offset, partPath: `/v1/jobs/${job.id}/uploads/${index}` };
}

function committedVideoBytes(parts: { number: number; size: number }[], total: number) {
  let offset = 0;
  for (let index = 0; index < parts.length; index++) {
    const part = parts[index];
    const expected = Math.min(VIDEO_PART_BYTES, total - offset);
    if (part.number !== index + 1 || expected <= 0 || part.size !== expected) {
      throw new HttpError(409, 'Incomplete or invalid RunPod multipart sequence');
    }
    offset += part.size;
  }
  return offset;
}

async function uploadVolumePart(request: Request, env: Env, jobId: string, index: number, part: number) {
  const storage = storageForEnv(env);
  if (storage.provider !== 'runpod') throw new HttpError(404, 'Volume upload unavailable');
  const job = await readJob(env, jobId);
  const file = job.files[index];
  const uploadId = job.volumeUploads?.[index];
  if (job.state !== 'uploading' || !job.stereoVideo || !file || !uploadId) {
    throw new HttpError(409, 'Start or resume the upload session first');
  }
  const offset = (part - 1) * VIDEO_PART_BYTES;
  const length = Math.min(VIDEO_PART_BYTES, file.size - offset);
  if (!Number.isSafeInteger(part) || part < 1 || length <= 0 ||
      Number(request.headers.get('Content-Length')) !== length) {
    throw new HttpError(400, 'Invalid video part number or length');
  }
  const volume = storage as RunpodStorage;
  const path = `${jobFolder(jobId)}/input/${file.name}`;
  const before = await volume.videoParts(path, uploadId);
  const committed = committedVideoBytes(before, file.size);
  if (offset > committed) throw new HttpError(409, 'Resume from the last committed part');
  if (offset === committed) {
    const bytes = await request.arrayBuffer();
    if (bytes.byteLength !== length) throw new HttpError(400, 'Incorrect video part length');
    await volume.putVideoPart(path, uploadId, part, bytes);
  }
  if (offset + length === file.size) {
    const parts = await volume.videoParts(path, uploadId);
    if (committedVideoBytes(parts, file.size) !== file.size) {
      throw new HttpError(409, 'RunPod has not committed all video parts');
    }
    await volume.finishVideo(path, uploadId, parts);
  }
  return { offset: Math.max(committed, offset + length) };
}

async function savedUploadOffset(session: NonNullable<JobRecord['videoUploads']>[number] | undefined, size: number) {
  if (!session || Date.parse(session.expiresAt) <= Date.now()) return undefined;
  // Query Drive here so its committed byte range is available regardless of browser CORS.
  const status = await fetch(session.uploadUrl, {
    method: 'PUT', body: '',
    headers: { 'Content-Range': `bytes */${size}` },
    redirect: 'manual',
  });
  const range = /^bytes=0-(\d+)$/.exec(status.headers.get('Range') ?? '');
  await status.body?.cancel();
  if (status.ok) return size;
  if (status.status === 308) {
    const offset = range ? Number(range[1]) + 1 : 0;
    if (offset <= size) return offset;
  }
  if ([404, 410].includes(status.status)) return undefined;
  throw new HttpError(502, 'Could not check the saved video upload. Retry shortly.');
}

async function videoUploadProgress(jobId: string, env: Env) {
  const job = await readJob(env, jobId);
  if (!job.stereoVideo) throw new HttpError(404, 'Video upload not found');
  if (job.state !== 'uploading') return { offsets: job.files.map(file => file.size) };
  const storage = storageForEnv(env);
  const children = await storage.listFiles(`${jobFolder(jobId)}/input`);
  if (storage.provider === 'runpod') {
    const offsets = await Promise.all(job.files.map(async (file, index) => {
      if (children.some(item => item.name === file.name && item.size === file.size)) return file.size;
      const id = job.volumeUploads?.[index];
      return id ? committedVideoBytes(await (storage as RunpodStorage).videoParts(
        `${jobFolder(jobId)}/input/${file.name}`, id), file.size) : 0;
    }));
    return { offsets };
  }
  const offsets = await Promise.all(job.files.map(async (file, index) => {
    if (children.some(item => item.name === file.name && item.size === file.size && item.md5Checksum)) return file.size;
    return (await savedUploadOffset(job.videoUploads?.[index], file.size)) ?? 0;
  }));
  return { offsets };
}

async function startJob(request: Request, jobId: string, env: Env) {
  let job = await readJob(env, jobId);
  const previousState = job.state;
  const retry = job.stereoVideo && ['review', 'failed', 'cancelled'].includes(previousState);
  if (previousState !== 'uploading' && !retry) {
    throw new HttpError(409, `Job is already ${job.state}`);
  }
  if (previousState === 'review') {
    const { excluded } = await parseJson<{ excluded?: unknown }>(request);
    if (!job.pairCount || !Array.isArray(excluded) ||
        !excluded.every(pair => Number.isInteger(pair) && pair >= 1 && pair <= job.pairCount!)) {
      throw new HttpError(400, 'Exclude pairs using their numbers in pairs.csv');
    }
    job.excludedPairs = Array.from(new Set<number>(excluded));
    if (job.pairCount - job.excludedPairs.length < 2) throw new HttpError(400, 'Keep at least two pairs');
    job.reviewed = true;
  }
  const storage = storageForEnv(env);
  const children = job.stereo
    ? (await Promise.all([job.stereo.leftFolder, job.stereo.rightFolder].map(
        (folder) => storage.listRootFiles(folder)))).flat()
    : job.source?.kind === 'storage-folder'
      ? await storage.listRootFiles(job.source.folder)
      : await storage.listFiles(`${jobFolder(jobId)}/input`);
  const uploaded = new Map(
    children.map((item) => [item.id ? `id:${item.id}` : `name:${item.name.toLowerCase()}`, item]),
  );
  const missing = job.files.filter(
    (file) => {
      const item = file.sourceId
        ? uploaded.get(`id:${file.sourceId}`)
        : children.find((candidate) => candidate.name.toLowerCase() === file.name.toLowerCase());
      return !item || item.size !== file.size
        || (file.sourceName && item.name !== file.sourceName)
        || (file.sourceMd5 && item.md5Checksum?.toLowerCase() !== file.sourceMd5.toLowerCase());
    },
  );
  if (missing.length) {
    throw new HttpError(
      409,
      `${missing.length} source file(s) are not fully uploaded`,
    );
  }

  if (job.stereoVideo) {
    job.files = job.files.map((file) => {
      const item = children.find((candidate) => candidate.name === file.name && candidate.size === file.size);
      if (!item?.id || (storage.provider !== 'runpod' && !item.md5Checksum)) throw new HttpError(409, 'Waiting for storage to verify both video uploads');
      return { ...file, sourceId: item.id, sourceName: item.name, sourceMd5: item.md5Checksum };
    });
    delete job.videoUploads;
    delete job.volumeUploads;
    // Persist verified identities before the worker can read the record.
    if (previousState === 'uploading') await writeJob(env, job);
  }
  const endpointId = job.runpodEndpointId || env.RUNPOD_ENDPOINT_ID;
  // Revalidate explicit alternatives in case they were disabled or removed
  // while the user was uploading. Never silently move a job to another endpoint.
  if (endpointId !== env.RUNPOD_ENDPOINT_ID) {
    const endpoint = (await runpodEndpoints(env)).find((item) => item.id === endpointId);
    if (!endpoint || endpoint.workersMax === 0) throw new HttpError(409, 'Selected RunPod endpoint is no longer enabled');
  }
  const response = await fetch(`${RUNPOD_ROOT}/${endpointId}/run`, {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${env.RUNPOD_API_KEY}`,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      input: {
        schema_version: 1,
        job_id: jobId,
        ...(storage.provider === 'runpod' ? { volume_job: true } : {}),
        name: job.name,
        profile: job.outputMode ?? 'compact',
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
  job.timings.total.state = 'running';
  delete job.timings.total.completedAt;
  job.timings.total.elapsedSeconds = elapsedSeconds(
    job.timings.total.startedAt,
    queuedAt,
  );
  job = {
    ...job,
    state: 'queued',
    error: undefined,
    stage: 'Waiting for a GPU worker',
    progress: 1,
    runpodJobId: result.id,
    runpodEndpointId: endpointId,
    updatedAt: new Date().toISOString(),
  };
  await writeJob(env, job);
  return publicJob(job, env);
}

async function listJobs(env: Env, cursor?: string) {
  if (cursor && cursor.length > 4096) throw new HttpError(400, 'Invalid jobs cursor');
  const storage = storageForEnv(env);
  if (!storage.listJobFolders) throw new HttpError(400, 'Job history is unavailable for this storage provider');
  const page = await storage.listJobFolders(cursor);
  let skipped = 0;
  const jobs = [];
  // Read saved metadata only: browsing never calls RunPod or starts a GPU.
  // Sequential reads also stay within Drive and Worker connection limits.
  for (const id of page.names.filter(isJobId)) {
    try {
      const job = await storage.readJson<JobRecord>(`${jobFolder(id)}/job.json`);
      if (!job || job.id !== id || typeof job.name !== 'string' || !job.state) {
        skipped++;
        continue;
      }
      jobs.push(publicJob(job, env));
    } catch (error) {
      if ((error instanceof StorageError && error.status === 404) || error instanceof SyntaxError) {
        skipped++;
        continue;
      }
      throw error;
    }
  }
  return { jobs, nextCursor: page.nextCursor, skipped };
}

async function getJob(jobId: string, env: Env) {
  let job = await readJob(env, jobId);
  if (job.state === 'completed' && job.result?.bundle) {
    const cleanup = await readCleanup(env, jobId);
    if (cleanup.id) {
      const status = await runpodStatus(env, cleanup.id, job.runpodEndpointId);
      job = await readJob(env, jobId);
      if (job.state !== 'purged') {
        if (['FAILED', 'TIMED_OUT', 'CANCELLED', 'COMPLETED'].includes(status.status ?? '')) {
          const error = status.error || 'Volume cleanup did not finish. Try the purge again.';
          await storageForEnv(env).writeJson(`${jobFolder(jobId)}/cleanup.json`, { error });
          return publicJob({ ...job, error }, env);
        }
        return publicJob({ ...job, purgeJobId: cleanup.id }, env);
      }
    } else if (cleanup.error) {
      return publicJob({ ...job, error: cleanup.error }, env);
    }
  }
  if (
    job.runpodJobId &&
    !['completed', 'failed', 'cancelled', 'review', 'purged'].includes(job.state)
  ) {
    let status: Awaited<ReturnType<typeof runpodStatus>>;
    try {
      status = await runpodStatus(env, job.runpodJobId, job.runpodEndpointId);
    } catch (error) {
      if (!job.stereoVideo || !(error instanceof HttpError) || error.status !== 404) throw error;
      const lastSaved = await readJob(env, jobId);
      if (['completed', 'failed', 'cancelled', 'review', 'purged'].includes(lastSaved.state)) {
        return publicJob(lastSaved, env);
      }
      return publicJob({
        ...lastSaved,
        error: 'RunPod no longer returns status for this attempt. Showing the last saved stage and downloads.',
      }, env, true);
    }
    const state = mapRunpodState(status.status);
    if (state && state !== job.state && (!job.stereoVideo || ['completed', 'failed', 'cancelled'].includes(state))) {
      // The GPU worker writes richer stage/progress updates. Only persist a
      // provider terminal state when it has not already done so.
      job = await readJob(env, jobId);
      if (!['completed', 'failed', 'cancelled', 'review', 'purged'].includes(job.state)) {
        job.state = state;
        job.stage = state === 'running' ? 'Reconstructing reef model' : state;
        job.progress =
          state === 'running' ? Math.max(job.progress, 3) : job.progress;
        if (state === 'failed')
          job.error = runpodErrorMessage(status.error);
        job.updatedAt = new Date().toISOString();
        try {
          await writeJob(env, job);
        } catch (error) {
          if (!job.stereoVideo || state !== 'failed') throw error;
          console.warn('Could not persist terminal RunPod job state', job.id);
        }
      }
    }
  }
  return publicJob(job, env);
}

async function readCleanup(env: Env, id: string) {
  try {
    return await storageForEnv(env).readJson<{ id?: string; error?: string }>(
      `${jobFolder(id)}/cleanup.json`,
    );
  } catch (error) {
    if (error instanceof StorageError && error.status === 404) return {};
    throw error;
  }
}

async function purgeJob(request: Request, env: Env, id: string) {
  const { localCopyVerified } = await parseJson<{ localCopyVerified?: boolean }>(request);
  if (localCopyVerified !== true || storageForEnv(env).provider !== 'runpod') {
    throw new HttpError(400, 'Confirm that you saved and verified the local results archive');
  }
  const job = await readJob(env, id);
  if (job.state === 'purged') return publicJob(job, env);
  if (job.state !== 'completed' || !job.result?.bundle) {
    throw new HttpError(409, 'Only a completed job with a download bundle can be purged');
  }
  const pending = await readCleanup(env, id);
  if (pending.id) return publicJob({ ...job, purgeJobId: pending.id }, env);
  const response = await fetch(`${RUNPOD_ROOT}/${job.runpodEndpointId ?? env.RUNPOD_ENDPOINT_ID}/run`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${env.RUNPOD_API_KEY}`, 'Content-Type': 'application/json' },
    body: JSON.stringify({ input: {
      schema_version: 1, job_id: id, volume_job: true, action: 'purge',
      openreef_version: job.versions.openreef, openreef_gpu_version: job.versions.openreefGPU,
    } }),
  });
  const result = await response.json<{ id?: string }>();
  if (!response.ok || !result.id) throw new HttpError(502, 'RunPod rejected the purge request');
  const current = await readJob(env, id);
  if (current.state === 'purged') return publicJob(current, env);
  await storageForEnv(env).writeJson(`${jobFolder(id)}/cleanup.json`, { id: result.id });
  return publicJob({ ...current, purgeJobId: result.id, error: undefined }, env);
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
    [job.result?.bundle, job.result?.model, job.result?.manifest, job.result?.project, job.result?.gaussian, job.result?.stereo_diagnostics, job.result?.rig_profile, job.result?.stereo_matching].filter(Boolean),
  );
  const stageAsset = job.stageAssets?.some(asset => asset.name === fileName);
  if (!stageAsset && (job.state !== 'completed' || !allowed.has(fileName))) {
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
  if (stageAsset || fileName === job.result?.project || storageForEnv(env).provider === 'runpod') {
    responseHeaders.set('Content-Disposition', `attachment; filename="${fileName}"`);
  }
  responseHeaders.set('Cache-Control', storageForEnv(env).provider === 'runpod' ? 'private, no-store' : fileName.endsWith('.log') ? 'private, no-cache' : 'private, max-age=3600');
  return new Response(upstream.body, {
    status: upstream.status,
    headers: responseHeaders,
  });
}

function runpodErrorMessage(error?: string) {
  if (!error) return 'GPU processing failed';
  try {
    const detail = JSON.parse(error) as { error_message?: unknown };
    if (typeof detail?.error_message === 'string') return detail.error_message;
  } catch {
    // RunPod also returns plain-text errors.
  }
  return error;
}

async function runpodStatus(env: Env, id: string, endpointId = env.RUNPOD_ENDPOINT_ID) {
  const response = await fetch(
    `${RUNPOD_ROOT}/${endpointId}/status/${id}`,
    {
      headers: { Authorization: `Bearer ${env.RUNPOD_API_KEY}` },
    },
  );
  if (response.status === 404) throw new HttpError(404, 'RunPod job status is no longer available');
  if (!response.ok)
    throw new HttpError(502, 'Could not read Runpod job status');
  return response.json<{ status?: string; error?: string }>();
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

function publicJob(job: JobRecord, env: Env, statusUnavailable = false) {
  const assetBase = `/v1/jobs/${job.id}/assets`;
  const storage = storageForEnv(env);
  return {
    id: job.id,
    ...(statusUnavailable ? { statusUnavailable: true } : {}),
    name: job.name,
    state: job.state,
    stage: job.purgeJobId ? 'Purging RunPod files' : job.stage,
    purging: Boolean(job.purgeJobId),
    progress: job.progress,
    createdAt: job.createdAt,
    updatedAt: job.updatedAt,
    error: job.error,
    outputMode: job.outputMode ?? 'compact',
    sourceSummary: job.sourceSummary,
    videoFiles: job.stereoVideo ? job.files.map((file, index) => ({
      name: job.stereoVideo!.originalNames[index], size: file.size,
      verified: Boolean(file.sourceId && (file.sourceMd5 || storage.provider === 'runpod')),
    })) : undefined,
    syncWarnings: job.syncWarnings,
    pairCount: job.pairCount,
    reviewed: job.reviewed,
    stageAssets: job.stageAssets?.map(asset => ({
      ...asset, url: `${assetBase}/${encodeURIComponent(asset.name)}`,
    })),
    stereo: job.stereo ? { cameras: job.stereo.profile.cameras.map(c => c.id) } : undefined,
    runtime: job.runpodEndpointId ? { endpointId: job.runpodEndpointId, name: job.runpodEndpointName ?? 'OpenReef endpoint' } : undefined,
    transfers: job.transfers,
    timings: job.timings,
    result: job.result
      ? {
          stereoDiagnosticsUrl: job.result.stereo_diagnostics ? `${assetBase}/${encodeURIComponent(job.result.stereo_diagnostics)}` : undefined,
          rigProfileUrl: job.result.rig_profile ? `${assetBase}/${encodeURIComponent(job.result.rig_profile)}` : undefined,
          stereoMatchingUrl: job.result.stereo_matching ? `${assetBase}/${encodeURIComponent(job.result.stereo_matching)}` : undefined,
          gaussianUrl: job.result.gaussian ? `${assetBase}/${encodeURIComponent(job.result.gaussian)}` : undefined,
          bundleUrl: job.result.bundle ? `${assetBase}/${encodeURIComponent(job.result.bundle)}` : undefined,
          modelUrl: `${assetBase}/${encodeURIComponent(job.result.model)}`,
          manifestUrl: `${assetBase}/${encodeURIComponent(job.result.manifest)}`,
          projectUrl: job.result.project ? `${assetBase}/${encodeURIComponent(job.result.project)}` : undefined,
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

function validateStereoVideo(value: unknown, files: unknown) {
  if (!value || typeof value !== 'object' || !Array.isArray(files) || files.length !== 2) {
    throw new HttpError(400, 'Choose one video for Camera A and one for Camera B');
  }
  const raw = value as StereoVideoConfig;
  if (raw.referenceCamera !== 'A' && raw.referenceCamera !== 'B') {
    throw new HttpError(400, 'Choose the physical camera corresponding to the left/reference camera');
  }
  if (!Number.isFinite(raw.intervalSeconds) || raw.intervalSeconds < 0.1 || raw.intervalSeconds > 120 ||
      !Number.isInteger(raw.maxPairs) || raw.maxPairs < 2 || raw.maxPairs > 2000 ||
      !Number.isInteger(raw.jpegQuality) || raw.jpegQuality < 1 || raw.jpegQuality > 2 ||
      (raw.reviewPairs !== undefined && typeof raw.reviewPairs !== 'boolean') ||
      (raw.colourCorrection !== undefined && typeof raw.colourCorrection !== 'boolean')) {
    throw new HttpError(400, 'Invalid extraction interval, pair limit or JPEG quality');
  }
  const config: StereoVideoConfig = {
    referenceCamera: raw.referenceCamera, originalNames: [],
    intervalSeconds: raw.intervalSeconds, maxPairs: raw.maxPairs, jpegQuality: raw.jpegQuality,
    reviewPairs: raw.reviewPairs === true, colourCorrection: raw.colourCorrection === true,
  };
  if (raw.profile !== undefined) {
    config.profile = validateStereo({ leftFolder: 'left', rightFolder: 'right', profile: raw.profile }).profile;
  }
  let total = 0;
  const validated = files.map((item, index): JobFile => {
    if (!item || typeof item !== 'object') throw new HttpError(400, 'Invalid video metadata');
    const original = cleanFileName(item.name);
    if (!/\.mp4$/i.test(original) || !Number.isSafeInteger(item.size) || item.size < 1) {
      throw new HttpError(400, 'Each source must be a nonempty GoPro MP4');
    }
    total += item.size;
    if (total > MAX_TOTAL_SIZE) throw new HttpError(413, 'The video upload is larger than 50 GiB');
    config.originalNames.push(original);
    const cameraId = index === 0 ? 'A' : 'B';
    return { name: `camera-${cameraId}.mp4`, size: item.size, contentType: 'video/mp4', cameraId };
  });
  return { config, files: validated };
}

function validateStereo(value: unknown): StereoConfig {
  const fail = (message: string): never => { throw new HttpError(400, message); };
  if (!value || typeof value !== 'object') fail('Invalid stereo configuration');
  const raw = value as StereoConfig;
  const leftFolder = cleanSourceFolder(raw.leftFolder);
  const rightFolder = cleanSourceFolder(raw.rightFolder);
  if (leftFolder === rightFolder) fail('Choose separate camera folders');
  const p = raw.profile;
  if (!p || p.schemaVersion !== 1 || p.translationUnit !== 'metres'
      || !Array.isArray(p.cameras) || p.cameras.length !== 2) fail('Invalid metric rig profile');
  for (const c of p.cameras) {
    if (!c || typeof c !== 'object' || typeof c.id !== 'string') fail('Invalid camera');
    const count = c.model === 'PINHOLE' ? 4 : (c.model === 'OPENCV' || c.model === 'OPENCV_FISHEYE') ? 8 : 0;
    if (!/^[A-Za-z0-9_-]{1,80}$/.test(c.id) || !count
        || !Number.isSafeInteger(c.width) || c.width < 1
        || !Number.isSafeInteger(c.height) || c.height < 1
        || !Array.isArray(c.params) || c.params.length !== count
        || !c.params.every(Number.isFinite) || c.params[0] <= 0 || c.params[1] <= 0) {
      fail('Invalid camera identity or calibrated intrinsics');
    }
  }
  if (p.cameras[0].id === p.cameras[1].id) fail('Camera identities must differ');
  const pose = p.rightFromLeft;
  if (!pose || !Array.isArray(pose.rotation) || pose.rotation.length !== 3
      || !pose.rotation.every(row => Array.isArray(row) && row.length === 3 && row.every(Number.isFinite))
      || !Array.isArray(pose.translation) || pose.translation.length !== 3
      || !pose.translation.every(Number.isFinite) || Math.hypot(...pose.translation) < 1e-6) {
    fail('Invalid rig rotation or baseline');
  }
  const r = pose.rotation;
  for (let i = 0; i < 3; i++) for (let j = 0; j < 3; j++) {
    if (Math.abs(r.reduce((sum, row) => sum + row[i] * row[j], 0) - (i === j ? 1 : 0)) > 1e-5) {
      fail('Rig rotation must be orthonormal');
    }
  }
  const determinant = r[0][0]*(r[1][1]*r[2][2]-r[1][2]*r[2][1])
    -r[0][1]*(r[1][0]*r[2][2]-r[1][2]*r[2][0])+r[0][2]*(r[1][0]*r[2][1]-r[1][1]*r[2][0]);
  if (Math.abs(determinant - 1) > 1e-5) fail('Rig rotation must have determinant +1');
  const number = (input: unknown, fallback: number, min: number, max: number, integer = false) => {
    const n = input === undefined ? fallback : input;
    if (typeof n !== 'number' || !Number.isFinite(n) || n < min || n > max
        || (integer && !Number.isSafeInteger(n))) fail('Invalid stereo pairing/matching setting');
    return n as number;
  };
  if (raw.timestamps !== undefined) {
    if (!raw.timestamps || typeof raw.timestamps !== 'object' || Array.isArray(raw.timestamps)) fail('Invalid timestamps');
    for (const [id, entries] of Object.entries(raw.timestamps)) {
      if (!p.cameras.some(c => c.id === id) || !entries || typeof entries !== 'object'
          || !Object.values(entries).every(Number.isFinite)) fail('Invalid camera timestamps');
    }
  }
  return { leftFolder, rightFolder, profile: p,
    maxDeltaMs: number(raw.maxDeltaMs, 30, 0.001, 60000),
    rightOffsetMs: number(raw.rightOffsetMs, 0, -86400000, 86400000),
    overlap: number(raw.overlap, 10, 1, 100, true),
    loopInterval: number(raw.loopInterval, 50, 0, 10000, true),
    loopNeighbors: number(raw.loopNeighbors, 5, 1, 20, true),
    ...(raw.timestamps ? { timestamps: raw.timestamps } : {}) };
}

async function stereoFiles(storage: ReturnType<typeof storageForEnv>, stereo: StereoConfig,
                           limit: unknown) {
  if (storage.provider !== 'google-drive') throw new HttpError(400, 'Stereo folders require Google Drive');
  const streams = await Promise.all([stereo.leftFolder, stereo.rightFolder].map(
    folder => filesFromStorageFolder(storage, folder, limit)));
  const files = streams.flatMap((stream, index) => stream.files.map(file => ({
    ...file, cameraId: stereo.profile.cameras[index].id,
  })));
  // The form and folder selector apply MAX_FILES to each physical camera.
  const maxStereoFiles = MAX_FILES * stereo.profile.cameras.length;
  if (files.length > maxStereoFiles) {
    throw new HttpError(413, `Stereo sources contain ${files.length} photos; the limit is ${MAX_FILES} per camera (${maxStereoFiles} total)`);
  }
  const totalSize = files.reduce((sum, file) => sum + file.size, 0);
  if (totalSize > MAX_TOTAL_SIZE) {
    throw new HttpError(413, `Combined stereo source is ${(totalSize / 1024 ** 3).toFixed(2)} GiB; the job limit is 50 GiB. Reduce Maximum photos per camera.`);
  }
  if (new Set(files.map(file => file.sourceId)).size !== files.length || files.some(f => !f.sourceId)) {
    throw new HttpError(400, 'Stereo folders must contain separate images with stable Drive IDs');
  }
  return { files, summary: {
    duplicateCopiesSkipped: streams.reduce((sum, s) => sum + s.summary.duplicateCopiesSkipped, 0),
    renamedFiles: streams.reduce((sum, s) => sum + s.summary.renamedFiles, 0),
  } };
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
): Promise<{ files: JobFile[]; summary: { duplicateCopiesSkipped: number; renamedFiles: number } }> {
  const limit =
    limitValue === undefined
      ? MAX_FILES
      : typeof limitValue === 'number' || typeof limitValue === 'string'
        ? Number.parseInt(`${limitValue}`, 10)
        : Number.NaN;
  if (!Number.isSafeInteger(limit) || limit < 2 || limit > MAX_FILES) {
    throw new HttpError(400, `Photo limit must be between 2 and ${MAX_FILES}`);
  }
  const candidates = (await storage.listRootFiles(folder))
    .filter((file) =>
      IMAGE_EXTENSIONS.has(file.name.split('.').pop()?.toLowerCase() || ''),
    )
    .sort((left, right) => {
      const a = `${left.name}\u0000${left.id ?? ''}`;
      const b = `${right.name}\u0000${right.id ?? ''}`;
      return a < b ? -1 : a > b ? 1 : 0;
    });
  const items: JobFile[] = [];
  const usedNames = new Set<string>();
  const content = new Set<string>();
  let duplicateCopiesSkipped = 0;
  for (const file of candidates) {
    // An MD5 and byte count identify repeated Drive uploads. A filename or
    // matching size alone cannot establish that two photos are identical.
    const identity = file.md5Checksum ? `${file.md5Checksum.toLowerCase()}:${file.size}` : undefined;
    if (identity && content.has(identity)) {
      duplicateCopiesSkipped++;
      continue;
    }
    if (identity) content.add(identity);
    const original = cleanFileName(file.name);
    let name = original;
    let suffix = 2;
    const dot = original.lastIndexOf('.');
    while (usedNames.has(name.toLowerCase())) {
      if (!file.id) throw new HttpError(409, `Duplicate source filename cannot be resolved without a file ID: ${file.name}`);
      const tag = `__${suffix++}`;
      const extension = dot >= 0 ? original.slice(dot) : '';
      name = `${original.slice(0, dot >= 0 ? dot : undefined).slice(0, 180 - tag.length - extension.length)}${tag}${extension}`;
    }
    usedNames.add(name.toLowerCase());
    items.push({
      name,
      size: file.size,
      contentType: 'application/octet-stream',
      ...(storage.provider === 'google-drive' && file.id ? {
        sourceId: file.id, sourceName: file.name, sourceMd5: file.md5Checksum,
      } : {}),
    });
  }
  const selected = items.slice(0, limit);
  const validated = validateFiles(selected);
  return {
    files: validated.map((file, index) => ({ ...selected[index], ...file })),
    summary: {
      duplicateCopiesSkipped,
      renamedFiles: selected.filter((file) => file.sourceName && file.name !== file.sourceName).length,
    },
  };
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
    'Access-Control-Allow-Methods': 'GET, POST, PUT, OPTIONS',
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
  readonly status: number;

  constructor(
    status: number,
    message: string,
  ) {
    super(message);
    this.status = status;
  }
}
