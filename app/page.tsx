'use client';

import { Cloud, HardDrive } from 'lucide-react';
import Image from 'next/image';

import { CloudJobs } from './cloud-jobs';

const basePath = process.env.NEXT_PUBLIC_BASE_PATH ?? '';
const apiUrl = (process.env.NEXT_PUBLIC_OPENREEF_API_URL ?? '').replace(/\/$/, '');

export default function Home() {
  return (
    <main className="app-shell">
      <aside className="app-sidebar" aria-label="OpenReef Cloud">
        <div className="traffic-lights" aria-hidden="true">
          <span className="traffic-red" />
          <span className="traffic-yellow" />
          <span className="traffic-green" />
        </div>

        <a
          className="app-brand"
          href="#workspace"
          aria-label="OpenReef Cloud home"
        >
          <Image
            src={`${basePath}/openreef-icon.png`}
            width={44}
            height={44}
            alt=""
            unoptimized
          />
          <span>
            <strong>OpenReef</strong>
            <small>CLOUD GPU</small>
          </span>
        </a>

        <div className="sidebar-divider" />
        <p className="sidebar-section">CLOUD RUNTIME</p>
        <a className="runtime-card" href={`${basePath}/`} aria-label="RunPod Serverless — return to homepage">
          <Cloud aria-hidden="true" />
          <span>
            <strong>RunPod Serverless</strong>
            <small>Scale to zero</small>
          </span>
        </a>
        <a
          className="runtime-card muted"
          href={apiUrl ? `${apiUrl}/v1/jobs-folder` : undefined}
          target="_blank"
          rel="noopener noreferrer"
          aria-label="Google Drive — open past jobs folder"
          aria-disabled={!apiUrl}
          title={apiUrl ? 'Open past jobs in Google Drive' : 'Configure the cloud API to open the jobs folder'}
        >
          <HardDrive aria-hidden="true" />
          <span>
            <strong>Google Drive</strong>
            <small>reefplot storage</small>
          </span>
        </a>

        <p className="sidebar-version">OpenReefGPU v0.6.5-gpu.6</p>
      </aside>

      <section
        id="workspace"
        className="workspace"
        aria-label="Cloud GPU workspace"
      >
        <CloudJobs />
      </section>
    </main>
  );
}
