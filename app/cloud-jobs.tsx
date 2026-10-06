'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import {
  Check,
  CloudUpload,
  Cpu,
  FolderOpen,
  LoaderCircle,
  RotateCcw,
} from 'lucide-react';

import { Button } from '@/components/ui/button';

import { ReefViewer } from './reef-viewer';

const API_URL = (process.env.NEXT_PUBLIC_OPENREEF_API_URL ?? '').replace(
  /\/$/,
  '',
);
const UPLOAD_CHUNK_BYTES = 10 * 1024 * 1024;

type TimingMetric = {
  name?: string;
  state: 'running' | 'completed' | 'failed';
  startedAt: string;
  completedAt?: string;
  elapsedSeconds: number;
};

type CloudJob = {
  id: string;
  name: string;
  state:
    | 'uploading'
    | 'queued'
    | 'running'
    | 'completed'
    | 'failed'
    | 'cancelled';
  stage: string;
  progress: number;
  error?: string;
  result?: { modelUrl: string; manifestUrl: string };
  storage?: { provider: 'google-drive' | 'sharepoint'; root: string };
  versions?: { openreef: string; openreefGPU: string };
  transfers?: {
    sourceDownload?: {
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
    };
  };
  timings?: {
    total: TimingMetric;
    steps: TimingMetric[];
  };
};

type UploadTarget = {
  name: string;
  size: number;
  uploadUrl: string;
};

type CreateJobResponse = { job: CloudJob; uploads: UploadTarget[] };

export function CloudJobs() {
  const [name, setName] = useState('New reef survey');
  const [sourceMode, setSourceMode] = useState<'drive' | 'upload'>('drive');
  const [driveFolder, setDriveFolder] = useState('images');
  const [driveLimit, setDriveLimit] = useState(30);
  const [files, setFiles] = useState<File[]>([]);
  const [job, setJob] = useState<CloudJob | null>(null);
  const [uploadProgress, setUploadProgress] = useState(0);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showViewer, setShowViewer] = useState(false);
  const [clock, setClock] = useState(() => Date.now());
  const inputRef = useRef<HTMLInputElement>(null);

  const totalBytes = useMemo(
    () => files.reduce((total, file) => total + file.size, 0),
    [files],
  );

  useEffect(() => {
    const jobId = new URLSearchParams(window.location.search).get('job');
    if (!jobId || !/^[0-9a-f-]{36}$/i.test(jobId)) return;

    let cancelled = false;
    api<CloudJob>(`/v1/jobs/${jobId}`)
      .then((next) => {
        if (cancelled) return;
        setJob(next);
        if (next.state === 'completed' && next.result) setShowViewer(true);
      })
      .catch((reason) => {
        if (cancelled) return;
        setError(
          reason instanceof Error
            ? reason.message
            : 'Could not load the linked cloud job.',
        );
      })
      .finally(() => {
        if (!cancelled) setSubmitting(false);
      });

    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!job || !['queued', 'running'].includes(job.state)) return;
    const timer = window.setInterval(async () => {
      try {
        const next = await api<CloudJob>(`/v1/jobs/${job.id}`);
        setJob(next);
        if (next.state === 'failed')
          setError(next.error || 'GPU processing failed.');
      } catch (reason) {
        console.error(reason);
      }
    }, 5000);
    return () => window.clearInterval(timer);
  }, [job]);

  useEffect(() => {
    if (!job || !['queued', 'running'].includes(job.state)) return;
    const timer = window.setInterval(() => setClock(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [job]);

  if (showViewer && job?.result) {
    return (
      <>
        <ReefViewer
          cloudModel={{
            url: absoluteApiUrl(job.result.modelUrl),
            label: job.name,
            source: `openreefGPU ${job.versions?.openreefGPU ?? ''} · ${storageLabel(job)} job ${job.id.slice(0, 8)}`,
          }}
        />
        <Button
          className="cloud-new-job"
          variant="outline"
          onClick={() => setShowViewer(false)}
        >
          <RotateCcw data-icon="inline-start" /> Job details
        </Button>
      </>
    );
  }

  const progress =
    job?.state === 'uploading' ? uploadProgress : (job?.progress ?? 0);
  const sourceDownload = job?.transfers?.sourceDownload;
  const timings = job?.timings;

  const submit = async () => {
    if (!API_URL) {
      setError(
        'This deployment has not been connected to the openreefGPU API.',
      );
      return;
    }
    if (sourceMode === 'upload' && files.length < 2) {
      setError('Choose at least two overlapping reef photographs.');
      return;
    }
    if (sourceMode === 'drive' && !driveFolder.trim()) {
      setError('Enter the folder containing the Drive photographs.');
      return;
    }
    setSubmitting(true);
    setError(null);
    setUploadProgress(0);
    try {
      const created = await api<CreateJobResponse>('/v1/jobs', {
        method: 'POST',
        body: JSON.stringify({
          name,
          ...(sourceMode === 'drive'
            ? { sourceFolder: driveFolder, maxFiles: driveLimit }
            : {
                files: files.map((file) => ({
                  name: file.name,
                  size: file.size,
                  contentType: file.type || 'application/octet-stream',
                })),
              }),
        }),
      });
      setJob(created.job);
      let uploadedBytes = 0;
      for (const target of created.uploads) {
        const file = files.find((candidate) => candidate.name === target.name);
        if (!file)
          throw new Error(
            `The selected file ${target.name} is no longer available.`,
          );
        await uploadFile(file, target.uploadUrl, (fileBytes) => {
          setUploadProgress(
            Math.round(((uploadedBytes + fileBytes) / totalBytes) * 100),
          );
        });
        uploadedBytes += file.size;
      }
      const started = await api<CloudJob>(`/v1/jobs/${created.job.id}/start`, {
        method: 'POST',
      });
      setJob(started);
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : 'Could not submit the cloud job.',
      );
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="cloud-workspace">
      <section className="cloud-card" aria-labelledby="cloud-title">
        <div className="cloud-card-heading">
          <span className="cloud-icon">
            <Cpu />
          </span>
          <div>
            <p className="eyebrow">Scale-to-zero reconstruction</p>
            <h1 id="cloud-title">Process a reef in the cloud</h1>
          </div>
        </div>
        <p className="cloud-lead">
          Photographs upload directly to your connected cloud storage. A GPU
          worker starts only for processing, saves a compact textured model,
          then shuts down.
        </p>

        {!job || job.state === 'failed' ? (
          <div className="cloud-form">
            <label>
              <span>Survey name</span>
              <input
                value={name}
                maxLength={80}
                onChange={(event) => setName(event.target.value)}
              />
            </label>
            <div className="cloud-source-tabs" aria-label="Photograph source">
              <button
                type="button"
                aria-pressed={sourceMode === 'drive'}
                onClick={() => setSourceMode('drive')}
              >
                Use Drive folder
              </button>
              <button
                type="button"
                aria-pressed={sourceMode === 'upload'}
                onClick={() => setSourceMode('upload')}
              >
                Upload new photos
              </button>
            </div>
            {sourceMode === 'drive' ? (
              <>
                <label>
                  <span>Folder inside reefplot</span>
                  <input
                    value={driveFolder}
                    maxLength={300}
                    onChange={(event) => setDriveFolder(event.target.value)}
                  />
                </label>
                <label>
                  <span>Photos in first run</span>
                  <input
                    type="number"
                    min={2}
                    max={2000}
                    value={driveLimit}
                    onChange={(event) =>
                      setDriveLimit(
                        Number.parseInt(event.target.value, 10) || 2,
                      )
                    }
                  />
                  <small className="cloud-field-help">
                    Start with 30 photos; increase after the smoke test.
                  </small>
                </label>
              </>
            ) : (
              <label>
                <span>Source photographs</span>
                <input
                  ref={inputRef}
                  className="cloud-file-input"
                  type="file"
                  accept=".jpg,.jpeg,.png,.tif,.tiff,.webp,image/*"
                  multiple
                  onChange={(event) =>
                    setFiles(Array.from(event.target.files ?? []))
                  }
                />
                <button
                  className="cloud-file-picker"
                  type="button"
                  onClick={() => inputRef.current?.click()}
                >
                  <FolderOpen />
                  <strong>
                    {files.length
                      ? `${files.length} photographs selected`
                      : 'Choose photographs'}
                  </strong>
                  <small>
                    {files.length
                      ? formatBytes(totalBytes)
                      : 'JPG, PNG, TIFF or WebP'}
                  </small>
                </button>
              </label>
            )}
            <Button
              onClick={submit}
              disabled={
                submitting ||
                !name.trim() ||
                (sourceMode === 'upload' && files.length < 2) ||
                (sourceMode === 'drive' && !driveFolder.trim())
              }
            >
              {submitting ? <LoaderCircle className="spin" /> : <CloudUpload />}
              {submitting
                ? sourceMode === 'drive'
                  ? 'Starting Drive job…'
                  : 'Uploading photographs…'
                : 'Submit GPU job'}
            </Button>
          </div>
        ) : (
          <div className="cloud-status" aria-live="polite">
            <div className="cloud-status-line">
              {job.state === 'completed' ? (
                <Check />
              ) : (
                <LoaderCircle className="spin" />
              )}
              <div>
                <strong>{job.stage}</strong>
                <small>
                  {job.name} · {job.id.slice(0, 8)}
                  {job.versions ? ` · OpenReef ${job.versions.openreef}` : ''}
                </small>
              </div>
              <output>{progress}%</output>
            </div>
            <progress max={100} value={progress}>
              {progress}%
            </progress>
            {timings && (
              <section className="cloud-timings" aria-label="Process timings">
                <div className="cloud-timings-heading">
                  <div>
                    <strong>Process timings</strong>
                    <small>Live and retained with this job</small>
                  </div>
                  <span>
                    Total{' '}
                    {formatDuration(liveTimingSeconds(timings.total, clock))}
                  </span>
                </div>
                <ol>
                  {timings.steps.map((step, index) => (
                    <li
                      key={`${step.name ?? 'Step'}-${step.startedAt}-${index}`}
                      data-state={step.state}
                    >
                      <i aria-hidden="true" />
                      <strong>{step.name ?? `Step ${index + 1}`}</strong>
                      <time>
                        {formatDuration(liveTimingSeconds(step, clock))}
                      </time>
                    </li>
                  ))}
                </ol>
              </section>
            )}
            {sourceDownload && (
              <section
                className="cloud-transfer"
                aria-label="Source transfer log"
              >
                <div className="cloud-transfer-heading">
                  <div>
                    <strong>
                      {(sourceDownload.cacheHits ?? 0) > 0
                        ? 'Runpod source cache'
                        : `${storageLabel(job)} → Runpod`}
                    </strong>
                    <small>
                      {sourceDownload.state === 'running'
                        ? 'Live source staging'
                        : 'Source staging complete'}
                    </small>
                  </div>
                  <span>{formatDuration(sourceDownload.elapsedSeconds)}</span>
                </div>
                <dl className="cloud-transfer-stats">
                  <div>
                    <dt>Images</dt>
                    <dd>
                      {sourceDownload.filesCompleted}/
                      {sourceDownload.filesTotal}
                    </dd>
                  </div>
                  <div>
                    <dt>Staged</dt>
                    <dd>
                      {formatBytes(sourceDownload.bytesCompleted)} /{' '}
                      {formatBytes(sourceDownload.bytesTotal)}
                    </dd>
                  </div>
                  <div>
                    <dt>Average</dt>
                    <dd>{formatRate(sourceDownload.bytesPerSecond)}</dd>
                  </div>
                  <div>
                    <dt>Cache hits</dt>
                    <dd>
                      {sourceDownload.cacheHits ?? 0}/
                      {sourceDownload.filesCompleted}
                    </dd>
                  </div>
                </dl>
                <div className="cloud-transfer-log" role="log">
                  <span>
                    {sourceDownload.state === 'running'
                      ? `Staging ${sourceDownload.currentFile ?? 'source images'}…`
                      : `Staged ${sourceDownload.filesTotal} source images in ${formatDuration(sourceDownload.elapsedSeconds)}; ${sourceDownload.cacheHits ?? 0} reused from Runpod storage.`}
                  </span>
                  <span>
                    Runpod logs retain the same file count, byte total, and
                    measured throughput.
                  </span>
                </div>
              </section>
            )}
            {job.state === 'completed' && job.result && (
              <Button onClick={() => setShowViewer(true)}>
                Open model in viewer
              </Button>
            )}
          </div>
        )}

        {error && (
          <p className="cloud-error" role="alert">
            {error}
          </p>
        )}
        {!API_URL && (
          <p className="cloud-notice">
            Cloud submission is ready in the codebase but is disabled in this
            preview until the API URL and deployment secrets are configured.
          </p>
        )}
        <div className="cloud-route" aria-label="Processing route">
          <span>Browser</span>
          <b>→</b>
          <span>{job ? storageLabel(job) : 'Cloud storage'}</span>
          <b>→</b>
          <span>RunPod GPU</span>
          <b>→</b>
          <span>Viewer</span>
        </div>
      </section>
    </div>
  );
}

async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.body) headers.set('Content-Type', 'application/json');
  const response = await fetch(absoluteApiUrl(path), {
    ...init,
    headers,
    credentials: 'include',
  });
  const payload = (await response.json()) as T & { error?: string };
  if (!response.ok)
    throw new Error(payload.error || `Cloud API returned ${response.status}`);
  return payload;
}

async function uploadFile(
  file: File,
  uploadUrl: string,
  onProgress: (uploadedBytes: number) => void,
) {
  let start = 0;
  while (start < file.size) {
    const endExclusive = Math.min(start + UPLOAD_CHUNK_BYTES, file.size);
    const response = await fetch(uploadUrl, {
      method: 'PUT',
      headers: {
        'Content-Range': `bytes ${start}-${endExclusive - 1}/${file.size}`,
      },
      body: file.slice(start, endExclusive),
    });
    if (
      !response.ok &&
      response.status !== 202 &&
      response.status !== 201 &&
      response.status !== 308
    ) {
      throw new Error(
        `Cloud upload failed for ${file.name} (${response.status}).`,
      );
    }
    start = endExclusive;
    onProgress(start);
  }
}

function absoluteApiUrl(path: string): string {
  return `${API_URL}${path.startsWith('/') ? path : `/${path}`}`;
}

function storageLabel(job: CloudJob): string {
  return job.storage?.provider === 'google-drive'
    ? 'Google Drive'
    : job.storage?.provider === 'sharepoint'
      ? 'SharePoint'
      : 'Cloud storage';
}

function formatBytes(bytes: number): string {
  if (bytes < 1024 ** 2) return `${Math.ceil(bytes / 1024)} KB`;
  if (bytes < 1024 ** 3) return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
  return `${(bytes / 1024 ** 3).toFixed(1)} GB`;
}

function formatRate(bytesPerSecond: number): string {
  return `${formatBytes(bytesPerSecond)}/s`;
}

function formatDuration(seconds: number): string {
  const rounded = Math.max(0, Math.round(seconds));
  const minutes = Math.floor(rounded / 60);
  const remainder = rounded % 60;
  return minutes ? `${minutes}m ${remainder}s` : `${remainder}s`;
}

function liveTimingSeconds(metric: TimingMetric, now: number): number {
  if (metric.state !== 'running') return metric.elapsedSeconds;
  const started = Date.parse(metric.startedAt);
  return Number.isFinite(started)
    ? Math.max(metric.elapsedSeconds, (now - started) / 1000)
    : metric.elapsedSeconds;
}
