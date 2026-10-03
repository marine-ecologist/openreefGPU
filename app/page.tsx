'use client';

import { useState } from 'react';
import { Check, Clock3, Layers3 } from 'lucide-react';

import { ReefViewer } from './reef-viewer';
import { CloudJobs } from './cloud-jobs';

type DatasetId = 'patchreef' | 'annularis' | 'natans' | 'cloud';

const datasets: Array<{
  id: DatasetId;
  label: string;
  scientific?: string;
  ready: boolean;
}> = [
  { id: 'patchreef', label: 'Patch reef', ready: true },
  {
    id: 'annularis',
    label: 'Annularis',
    scientific: 'Orbicella annularis',
    ready: false,
  },
  {
    id: 'natans',
    label: 'Natans',
    scientific: 'Orbicella faveolata',
    ready: false,
  },
  { id: 'cloud', label: 'RunPod GPU', ready: true },
];

export default function Home() {
  const [datasetId, setDatasetId] = useState<DatasetId>('cloud');
  const dataset = datasets.find((item) => item.id === datasetId)!;

  return (
    <main className="site-shell">
      <header className="topbar">
        <a className="brand" href="#viewer" aria-label="openreefGPU home">
          <span className="brand-mark" aria-hidden="true">
            <span />
            <span />
            <span />
          </span>
          <span>
            <strong>OPENREEF GPU</strong>
            <small>CLOUD RECONSTRUCTION</small>
          </span>
        </a>

        <nav className="dataset-nav" aria-label="Reef examples">
          {datasets.map((item) => (
            <button
              className="dataset-tab"
              data-active={item.id === datasetId}
              key={item.id}
              onClick={() => setDatasetId(item.id)}
              type="button"
            >
              <span>{item.label}</span>
              {item.ready ? (
                <span className="availability ready">
                  <Check aria-hidden="true" /> Ready
                </span>
              ) : (
                <span className="availability">
                  <Clock3 aria-hidden="true" /> Soon
                </span>
              )}
            </button>
          ))}
        </nav>

        <div className="project-tag">RunPod accelerated</div>
      </header>

      <section
        id="viewer"
        className="workspace"
        aria-label={`${dataset.label} viewer`}
      >
        {dataset.id === 'cloud' ? (
          <CloudJobs />
        ) : dataset.ready ? (
          <ReefViewer />
        ) : (
          <Placeholder
            name={dataset.label}
            scientific={dataset.scientific ?? dataset.label}
          />
        )}
      </section>
    </main>
  );
}

function Placeholder({
  name,
  scientific,
}: {
  name: string;
  scientific: string;
}) {
  return (
    <div className="placeholder">
      <div className="placeholder-grid" aria-hidden="true" />
      <div className="placeholder-card">
        <div className="placeholder-icon" aria-hidden="true">
          <Layers3 />
        </div>
        <p className="eyebrow">Compact reconstruction processing</p>
        <h1>{name}</h1>
        <p className="scientific">{scientific}</p>
        <p className="placeholder-copy">
          This example is wired into the collection. Its viewer will activate as
          soon as the complete compact asset set is available.
        </p>
        <div className="asset-checklist" aria-label="Required views">
          <span>Textured mesh</span>
          <span>Sparse cloud · original colour</span>
          <span>Dense points + mesh</span>
        </div>
      </div>
      <p className="interaction-hint muted">
        Choose Patch reef to explore the live reconstruction
      </p>
    </div>
  );
}
