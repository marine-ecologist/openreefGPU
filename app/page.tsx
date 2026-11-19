'use client';

import { Cloud, HardDrive } from 'lucide-react';
import Image from 'next/image';
import { useEffect, useState } from 'react';

import { CloudJobs } from './cloud-jobs';
import { PastProjects } from './past-projects';

const basePath = process.env.NEXT_PUBLIC_BASE_PATH ?? '';

export default function Home() {
  const [showProjects, setShowProjects] = useState(false);
  useEffect(() => {
    const frame = requestAnimationFrame(() => {
      setShowProjects(new URLSearchParams(window.location.search).get('projects') === '1');
    });
    return () => cancelAnimationFrame(frame);
  }, []);
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
            <strong>Configure &amp; Run</strong>
            <small>RunPod Serverless</small>
          </span>
        </a>
        <a
          className="runtime-card muted"
          href={`${basePath}/?projects=1`}
          aria-label="Browse RunPod job history"
          title="Browse job status and local downloads"
        >
          <HardDrive aria-hidden="true" />
          <span>
            <strong>Job history</strong>
            <small>RunPod network volume</small>
          </span>
        </a>

        <p className="sidebar-version">OpenReefGPU v0.6.5-gpu.12</p>
      </aside>

      <section
        id="workspace"
        className="workspace"
        aria-label="Cloud GPU workspace"
      >
        <>{showProjects ? <PastProjects /> : <CloudJobs />}</>
      </section>
    </main>
  );
}
