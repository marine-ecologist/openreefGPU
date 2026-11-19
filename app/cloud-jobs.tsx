'use client';

import { useEffect, useRef, useState } from 'react';
import {
  Check,
  CloudUpload,
  Cpu,
  LoaderCircle,
} from 'lucide-react';

import { Button } from '@/components/ui/button';

const API_URL = (process.env.NEXT_PUBLIC_OPENREEF_API_URL ?? '').replace(
  /\/$/,
  '',
);

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
    | 'review'
    | 'completed'
    | 'failed'
    | 'cancelled'
    | 'purged';
  purging?: boolean;
  stage: string;
  progress: number;
  error?: string;
  outputMode?: 'compact' | 'full';
  videoFiles?: { name: string; size: number; verified: boolean }[];
  stageAssets?: { stage: string; name: string; url: string }[];
  syncWarnings?: string[];
  pairCount?: number;
  reviewed?: boolean;
  sourceSummary?: { duplicateCopiesSkipped: number; renamedFiles: number };
  runtime?: { endpointId: string; name: string };
  result?: { bundleUrl?: string; modelUrl: string; gaussianUrl?: string; manifestUrl: string; projectUrl?: string; stereoDiagnosticsUrl?: string; rigProfileUrl?: string; stereoMatchingUrl?: string };
  storage?: { provider: 'runpod'; root: string };
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
  partPath?: string;
  partBytes?: number;
  offset?: number;
};

type CreateJobResponse = { job: CloudJob; uploads: UploadTarget[]; uploadSessionPath?: string };
type RuntimeEndpoint = {
  id: string; name: string; gpuIds?: string; workersMax?: number; isDefault: boolean;
  health?: { idle: number; running: number; initializing: number; queued: number };
};
type RuntimeCatalog = { endpoints: RuntimeEndpoint[]; checkedAt: string; warning?: string; truncated?: boolean };

export function CloudJobs() {
  const [name, setName] = useState('New reef survey');
  const [videos, setVideos] = useState<(File | null)[]>([null, null]);
  const [videoProgress, setVideoProgress] = useState([{ bytes: 0, rate: 0 }, { bytes: 0, rate: 0 }]);
  const [calibrated, setCalibrated] = useState(false);
  const [referenceCamera, setReferenceCamera] = useState<'A' | 'B'>('B');
  const [interval, setIntervalSeconds] = useState(1);
  const [maxPairs, setMaxPairs] = useState(300);
  const [jpegQuality, setJpegQuality] = useState(2);
  const [reviewPairs, setReviewPairs] = useState(false);
  const [colourCorrection, setColourCorrection] = useState(false);
  const [excludedPairs, setExcludedPairs] = useState('');
  const [localCopyVerified, setLocalCopyVerified] = useState(false);
  const [autoDownload, setAutoDownload] = useState(false);
  const retrieved = useRef(new Set<string>());
  const [rigProfile, setRigProfile] = useState('');
  const [outputMode, setOutputMode] = useState<'compact' | 'full'>('compact');
  const [includeSourceImages, setIncludeSourceImages] = useState(false);
  const [job, setJob] = useState<CloudJob | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [clock, setClock] = useState(() => Date.now());
  const [runtimeCatalog, setRuntimeCatalog] = useState<RuntimeCatalog | null>(null);
  const [runtimeId, setRuntimeId] = useState('');
  const [runtimeLoading, setRuntimeLoading] = useState(false);
  const [runtimeError, setRuntimeError] = useState('');
  const [runtimeRefresh, setRuntimeRefresh] = useState(0);

  useEffect(() => {
    if (!API_URL) return;
    const controller = new AbortController();
    const frame = requestAnimationFrame(() => {
      if (!controller.signal.aborted) { setRuntimeLoading(true); setRuntimeError(''); }
    });
    api<RuntimeCatalog>('/v1/runpod/endpoints', { signal: controller.signal })
      .then((catalog) => { if (!controller.signal.aborted) setRuntimeCatalog(catalog); })
      .catch((reason) => {
        if (!controller.signal.aborted) setRuntimeError(reason instanceof Error ? reason.message : 'Could not load RunPod workers.');
      })
      .finally(() => { cancelAnimationFrame(frame); if (!controller.signal.aborted) setRuntimeLoading(false); });
    return () => { cancelAnimationFrame(frame); controller.abort(); };
  }, [runtimeRefresh]);

  useEffect(() => {
    const jobId = new URLSearchParams(window.location.search).get('job');
    if (!jobId || !/^[0-9a-f-]{36}$/i.test(jobId)) return;

    let cancelled = false;
    api<CloudJob>(`/v1/jobs/${jobId}`)
      .then((next) => {
        if (cancelled) return;
        if (next.storage?.provider !== 'runpod') throw new Error('This job is outside the RunPod volume workflow.');
        setJob(next);
        if (next.state === 'uploading' && next.videoFiles) {
          api<{ offsets: number[] }>(`/v1/jobs/${jobId}/uploads`)
            .then(({ offsets }) => {
              if (!cancelled) setVideoProgress(offsets.map(bytes => ({ bytes, rate: 0 })));
            })
            .catch(() => { /* Resume retrieves the committed offsets again. */ });
        }
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
    if (!job || (!job.purging && !['queued', 'running'].includes(job.state))) return;
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

  useEffect(() => {
    if (!autoDownload || !job) return;
    for (const asset of job.stageAssets ?? []) {
      if (!/^stage-\d+\.tar\.gz$/.test(asset.name) || retrieved.current.has(asset.url)) continue;
      retrieved.current.add(asset.url);
      const link = document.createElement('a');
      link.href = absoluteApiUrl(asset.url);
      link.download = asset.name;
      document.body.appendChild(link);
      link.click();
      link.remove();
    }
  }, [autoDownload, job]);

  const progress = job?.progress ?? 0;
  const sourceDownload = job?.transfers?.sourceDownload;
  const timings = job?.timings;

  const submit = async () => {
    if (!API_URL) {
      setError(
        'This deployment has not been connected to the openreefGPU API.',
      );
      return;
    }
    if (videos.some(file => !file)) {
      setError('Choose one GoPro MP4 for each camera.');
      return;
    }
    let stereoProfile: unknown;
    if (calibrated) {
      if (!rigProfile.trim()) {
        setError('Choose an existing calibrated rig profile.');
        return;
      }
      try {
        stereoProfile = JSON.parse(rigProfile);
      } catch {
        setError('The rig profile is not valid JSON. Choose the complete calibration .json file and try again.');
        return;
      }
      if (!stereoProfile || typeof stereoProfile !== 'object' || Array.isArray(stereoProfile)) {
        setError('Choose a rig calibration JSON object, rather than a list or another JSON value.');
        return;
      }
    }
    setSubmitting(true);
    setError(null);
    setLocalCopyVerified(false);
    try {
      const created = await api<CreateJobResponse>('/v1/jobs', {
        method: 'POST',
        body: JSON.stringify({
          name,
          outputMode,
          includeSourceImages: outputMode === 'full' && includeSourceImages,
          uploadProtocol: 'per-file',
          requireRunpod: true,
          runpodEndpointId: runtimeId || undefined,
          stereoVideo: { referenceCamera, intervalSeconds: interval, maxPairs,
            jpegQuality, reviewPairs, colourCorrection, profile: stereoProfile },
          files: videos.map(file => ({ name: file!.name, size: file!.size })),
        }),
      });
      if (created.job.storage?.provider !== 'runpod') {
        throw new Error('Connect this app to the updated RunPod volume API.');
      }
      setJob(created.job);
      const url = new URL(window.location.href);
      url.searchParams.set('job', created.job.id);
      window.history.replaceState(null, '', url);
      await transferVideos(created.job);
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

  async function transferVideos(current: CloudJob) {
    for (const [index, expected] of (current.videoFiles ?? []).entries()) {
      const sessionPath = `/v1/jobs/${current.id}/uploads`;
      const loadSession = () => api<UploadTarget>(sessionPath, {
        method: 'POST', body: JSON.stringify({ fileIndex: index }),
      });
      let session = await loadSession();
      const file = videos[index];
      const base = session.offset ?? 0;
      const started = performance.now();
      const report = (bytes: number) => setVideoProgress(previous => previous.map((value, camera) =>
        camera === index ? { bytes, rate: Math.max(0, bytes - base) * 1000 / Math.max(1, performance.now() - started) } : value
      ));
      report(base);
      if (base === expected.size) continue;
      if (!file || file.name !== expected.name || file.size !== expected.size) {
        throw new Error(`Reselect the original Camera ${index === 0 ? 'A' : 'B'} video (${expected.name}).`);
      }
      for (let attempt = 0; ; attempt++) {
        try {
          if ((session.offset ?? 0) >= file.size) break;
          await uploadVideo(file, session, report);
          break;
        } catch (reason) {
          if (attempt >= 2) throw reason;
          session = await loadSession();
          report(session.offset ?? 0);
        }
      }
      report(expected.size);
    }
    setJob(await api<CloudJob>(`/v1/jobs/${current.id}/start`, { method: 'POST' }));
  }

  async function resumeVideo(current: CloudJob) {
    setSubmitting(true);
    setError(null);
    try {
      if (current.state === 'uploading') {
        await transferVideos(current);
      } else {
        const excluded = excludedPairs.trim().split(/[\s,]+/).filter(Boolean).map(Number);
        const body = current.state === 'review' ? JSON.stringify({ excluded }) : undefined;
        setJob(await api<CloudJob>(`/v1/jobs/${current.id}/start`, { method: 'POST', body }));
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not resume this job.');
    } finally {
      setSubmitting(false);
    }
  }

  async function purge(id: string) {
    setSubmitting(true);
    setError(null);
    try {
      setJob(await api<CloudJob>(`/v1/jobs/${id}/purge`, {
        method: 'POST', body: JSON.stringify({ localCopyVerified }),
      }));
      setLocalCopyVerified(false);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not purge the RunPod files.');
    } finally {
      setSubmitting(false);
    }
  }

  const chooseVideo = (index: number, file: File | null) => {
    setVideos(previous => previous.map((value, camera) => camera === index ? file : value));
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
            <h1 id="cloud-title">Configure &amp; Run</h1>
          </div>
        </div>
        <p className="cloud-lead">
          Upload Camera A and Camera B, extract synchronized image pairs, and reconstruct the survey on RunPod.
        </p>

        {!job ? (
          <div className="cloud-form">
            <label>
              <span>RunPod runtime</span>
              <select value={runtimeId} disabled={submitting || runtimeLoading} onChange={(event) => setRuntimeId(event.target.value)}>
                <option value="">Configured default endpoint</option>
                {runtimeCatalog?.endpoints.map((endpoint) => (
                  <option key={endpoint.id} value={endpoint.id} disabled={endpoint.workersMax === 0 || !endpoint.isDefault}>
                    {endpoint.name}{endpoint.gpuIds ? ` · ${endpoint.gpuIds}` : ''}
                    {endpoint.workersMax === 0 ? ' · disabled' : endpoint.health
                      ? ` · ${endpoint.health.idle} idle, ${endpoint.health.running} busy, ${endpoint.health.initializing} starting`
                      : ' · status unknown'}
                  </option>
                ))}
              </select>
              <small className="cloud-field-help">Use the endpoint attached to the configured RunPod network volume.</small>
            </label>
            <button type="button" disabled={runtimeLoading || submitting || !API_URL} onClick={() => setRuntimeRefresh((value) => value + 1)}>
              {runtimeLoading ? 'Checking workers…' : 'Refresh RunPod workers'}
            </button>
            {runtimeCatalog?.checkedAt && <small>Worker status checked {new Date(runtimeCatalog.checkedAt).toLocaleTimeString()}</small>}
            {(runtimeError || runtimeCatalog?.warning) && <output className="cloud-notice">{runtimeError || runtimeCatalog?.warning}</output>}
            {runtimeCatalog?.truncated && <p className="cloud-notice">Showing the first 12 endpoints. Configure the endpoint allowlist to narrow the selection.</p>}
            <label>
              <span>Survey name</span>
              <input
                value={name}
                maxLength={80}
                onChange={(event) => setName(event.target.value)}
              />
            </label>
            {['A', 'B'].map((camera, index) => (
                  <label key={camera}><span>Camera {camera} video</span>
                    <input type="file" accept=".mp4,video/mp4"
                      onChange={event => chooseVideo(index, event.target.files?.[0] ?? null)} />
                  </label>
                ))}
                <label><span>Left camera (time reference)</span>
                  <select value={referenceCamera} onChange={event => setReferenceCamera(event.target.value as 'A' | 'B')}>
                    <option value="B">Camera B</option><option value="A">Camera A</option>
                  </select>
                </label>
                <label><span>Calibration</span>
                  <select value={calibrated ? 'rig' : 'none'} onChange={event => setCalibrated(event.target.value === 'rig')}>
                    <option value="none">Uncalibrated · estimate camera geometry</option>
                    <option value="rig">Use existing rig.json</option>
                  </select>
                </label>
                {calibrated && <label><span>Rig profile (rig.json)</span>
                  <input type="file" accept=".json,application/json" onChange={async event => {
                    const file = event.target.files?.[0];
                    try {
                      setRigProfile(file ? await file.text() : '');
                      setError(null);
                    } catch {
                      setRigProfile('');
                      setError('Could not read the rig profile.');
                    }
                  }} />
                  <small>The first profile camera must be the selected left camera.</small>
                </label>}
                <label><span>Sampling interval (seconds)</span>
                  <input type="number" step="0.1" min={0.1} max={120} value={interval}
                    onChange={event => setIntervalSeconds(Number(event.target.value))} />
                </label>
                <label><span>Maximum pairs</span>
                  <input type="number" min={2} max={2000} value={maxPairs}
                    onChange={event => setMaxPairs(Number(event.target.value))} />
                </label>
                <label><span>JPEG quality</span>
                  <select value={jpegQuality} onChange={event => setJpegQuality(Number(event.target.value))}>
                    <option value={2}>High (2)</option><option value={1}>Highest (1)</option>
                  </select>
                </label>
                <label className="cloud-checkbox"><input type="checkbox" checked={colourCorrection}
                  onChange={event => setColourCorrection(event.target.checked)} /> Apply underwater colour correction</label>
                <label className="cloud-checkbox"><input type="checkbox" checked={reviewPairs}
                  onChange={event => setReviewPairs(event.target.checked)} /> Pause for frame-pair review</label>
            <label>
              <span>Save outputs</span>
              <select value={outputMode} onChange={(event) => setOutputMode(event.target.value as 'compact' | 'full')}>
                <option value="compact">Compact preview</option>
                <option value="full">Full project + compact preview</option>
              </select>
              <small className="cloud-field-help">
                Full project retains COLMAP and OpenMVS files, original outputs, settings and logs in a downloadable archive. It uses more processing time and storage; reconstruction resolution settings stay the same.
              </small>
            </label>
            {outputMode === 'full' && (
              <label className="cloud-checkbox">
                <input type="checkbox" checked={includeSourceImages} onChange={(event) => setIncludeSourceImages(event.target.checked)} />
                Include original photographs in the archive
              </label>
            )}
            <Button
              onClick={submit}
              disabled={
                submitting ||
                !name.trim() ||
                videos.some(file => !file) || (calibrated && !rigProfile)
              }
            >
              {submitting ? <LoaderCircle className="spin" /> : <CloudUpload />}
              {submitting ? 'Uploading videos…' : 'Upload & run'}
            </Button>
          </div>
        ) : (
          <div className="cloud-status" aria-live="polite">
            <div className="cloud-status-line">
              {['completed', 'purged'].includes(job.state) && !job.purging ? (
                <Check />
              ) : (
                <LoaderCircle className="spin" />
              )}
              <div>
                <strong>{job.stage}</strong>
                <small>
                  {job.name} · {job.id.slice(0, 8)}
                  {job.runtime ? ` · ${job.runtime.name}` : ''}
                  {job.versions ? ` · OpenReef ${job.versions.openreef}` : ''}
                </small>
              </div>
              <output>{progress}%</output>
            </div>
            <progress max={100} value={progress}>
              {progress}%
            </progress>
            {job.videoFiles && <section className="cloud-transfer" aria-label="Camera uploads">
              <strong>Stage 0 — Upload &amp; prepare</strong>
              {job.videoFiles.map((file, index) => {
                const camera = index === 0 ? 'A' : 'B';
                const bytes = file.verified ? file.size : videoProgress[index].bytes;
                const percent = Math.floor(100 * bytes / file.size);
                return <div key={camera}>
                  <p>Camera {camera} · {file.name} · {percent}% · {formatBytes(bytes)} / {formatBytes(file.size)}
                    {file.verified ? ' · verified on RunPod' : videoProgress[index].rate > 0 ? ` · ${formatRate(videoProgress[index].rate)}` : ''}</p>
                  <progress aria-label={`Camera ${camera} upload`} max={100} value={percent} />
                  {job.state === 'uploading' && !submitting && !file.verified &&
                    <input type="file" aria-label={`Reselect Camera ${camera}`} accept=".mp4,video/mp4"
                      onChange={event => chooseVideo(index, event.target.files?.[0] ?? null)} />}
                </div>;
              })}
              {job.state === 'uploading' && !submitting && <Button onClick={() => resumeVideo(job)}>Resume uploads</Button>}
              {['failed', 'cancelled'].includes(job.state) && <Button disabled={submitting}
                onClick={() => resumeVideo(job)}>Retry with verified videos</Button>}
              {(job.syncWarnings ?? []).map((warning, index) => <p key={index}>{warning}</p>)}
            </section>}
            {job.videoFiles && <section className="cloud-transfer" aria-label="Stage downloads">
              <strong>Stage outputs</strong>
              <label className="cloud-checkbox"><input type="checkbox" checked={autoDownload}
                onChange={event => setAutoDownload(event.target.checked)} /> Automatically download available stage archives</label>
              {(job.stageAssets ?? []).map(asset => <div key={asset.name}>
                <a href={absoluteApiUrl(asset.url)} download>{asset.stage} · {asset.name}</a>
              </div>)}
              {job.state === 'review' && <>
                <p>Review the paired frames and pairs.csv. {job.pairCount} pairs are available.</p>
                <label><span>Pair numbers to exclude (comma separated)</span>
                  <input value={excludedPairs} onChange={event => setExcludedPairs(event.target.value)} />
                </label>
                <Button disabled={submitting} onClick={() => resumeVideo(job)}>Run reconstruction</Button>
              </>}
            </section>}
            {['completed', 'failed', 'cancelled', 'uploading', 'review', 'purged'].includes(job.state) && !submitting &&
              <a href={window.location.pathname}>Configure a new job</a>}
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
                        : `RunPod network volume`}
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
                    <dt>{job.videoFiles ? 'Videos' : 'Images'}</dt>
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
                      ? `Staging ${sourceDownload.currentFile ?? (job.videoFiles ? 'videos' : 'source images')}…`
                      : `Staged ${sourceDownload.filesTotal} ${job.videoFiles ? 'videos' : 'source images'} in ${formatDuration(sourceDownload.elapsedSeconds)}; ${sourceDownload.cacheHits ?? 0} reused from Runpod storage.`}
                  </span>
                  <span>
                    Runpod logs retain the same file count, byte total, and
                    measured throughput.
                  </span>
                </div>
              </section>
            )}
            {job.state === 'completed' && job.result && (
              <>
                {job.result.bundleUrl && <>
                  <a className="cloud-project-download" href={absoluteApiUrl(job.result.bundleUrl)} download>
                    Download all results locally (.tar)
                  </a>
                  <label className="cloud-checkbox">
                    <input type="checkbox" checked={localCopyVerified} disabled={job.purging}
                      onChange={event => setLocalCopyVerified(event.target.checked)} />
                    I saved and opened the local results archive.
                  </label>
                  <Button variant="outline" disabled={!localCopyVerified || submitting || job.purging}
                    onClick={() => purge(job.id)}>
                    {job.purging ? 'Purging RunPod files…' : 'Purge this job’s RunPod files'}
                  </Button>
                </>}
                {job.result.stereoDiagnosticsUrl && <a href={absoluteApiUrl(job.result.stereoDiagnosticsUrl)} download>Download stereo pairing diagnostics</a>}
                {job.result.rigProfileUrl && <a href={absoluteApiUrl(job.result.rigProfileUrl)} download>Download reusable rig profile</a>}
                {job.result.stereoMatchingUrl && <a href={absoluteApiUrl(job.result.stereoMatchingUrl)} download>Download matching diagnostics</a>}
                {job.result.projectUrl && (
                  <a className="cloud-project-download" href={absoluteApiUrl(job.result.projectUrl)} download="full-project.tar.gz">
                    Download full project (.tar.gz)
                  </a>
                )}
              </>
            )}
          </div>
        )}

        {(error || job?.error) && (
          <p className="cloud-error" role="alert">
            {error || job?.error}
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
          <span>RunPod network volume</span>
          <b>→</b>
          <span>RunPod GPU</span>
          <b>→</b>
          <span>Local downloads</span>
        </div>
      </section>
    </div>
  );
}

async function uploadVideo(file: File, session: UploadTarget, report: (bytes: number) => void) {
  const { partPath, partBytes } = session;
  if (!partPath || !partBytes) throw new Error('RunPod did not return a multipart upload session.');
  for (let start = session.offset ?? 0; start < file.size;) {
    const end = Math.min(start + partBytes, file.size);
    const part = Math.floor(start / partBytes) + 1;
    const acknowledged = await new Promise<number>((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open('PUT', absoluteApiUrl(`${partPath}/${part}`));
      xhr.withCredentials = true;
      xhr.responseType = 'json';
      xhr.timeout = 300_000;
      xhr.upload.onprogress = event => report(start + event.loaded);
      xhr.onload = () => {
        if (xhr.status === 200 && xhr.response?.offset === end) resolve(end);
        else reject(new Error(xhr.response?.error || `Upload stopped for ${file.name} (HTTP ${xhr.status}).`));
      };
      xhr.onerror = xhr.ontimeout = () => reject(new Error(`Upload connection interrupted for ${file.name}.`));
      xhr.send(file.slice(start, end));
    });
    start = acknowledged;
    report(start);
  }
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

function absoluteApiUrl(path: string): string {
  return `${API_URL}${path.startsWith('/') ? path : `/${path}`}`;
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
