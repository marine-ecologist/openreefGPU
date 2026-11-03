'use client';

import { useEffect, useState } from 'react';

const basePath = process.env.NEXT_PUBLIC_BASE_PATH ?? '';
const apiUrl = (process.env.NEXT_PUBLIC_OPENREEF_API_URL ?? '').replace(/\/$/, '');

type SavedProject = {
  id: string;
  name: string;
  state: string;
  stage: string;
  createdAt: string;
  outputMode?: string;
  result?: { modelUrl: string; projectUrl?: string };
};
type ProjectPage = { jobs: SavedProject[]; nextCursor?: string; skipped: number };

export function PastProjects() {
  const [projects, setProjects] = useState<SavedProject[]>([]);
  const [selectedId, setSelectedId] = useState('');
  const [cursor, setCursor] = useState<string>();
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [skipped, setSkipped] = useState(0);
  const [refresh, setRefresh] = useState(0);

  async function readPage(next?: string, signal?: AbortSignal) {
    if (!apiUrl) throw new Error('The cloud API has not been configured.');
    const response = await fetch(`${apiUrl}/v1/jobs${next ? `?cursor=${encodeURIComponent(next)}` : ''}`, {
      credentials: 'include', signal,
    });
    const page = await response.json() as ProjectPage & { error?: string };
    if (!response.ok) throw new Error(page.error || `Could not load projects (${response.status}).`);
    return page;
  }

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError('');
    readPage(undefined, controller.signal).then((page) => {
      setProjects(page.jobs);
      setCursor(page.nextCursor);
      setSkipped(page.skipped);
      setSelectedId(page.jobs[0]?.id ?? '');
    }).catch((reason) => {
      if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : 'Could not load projects.');
    }).finally(() => {
      if (!controller.signal.aborted) setLoading(false);
    });
    return () => controller.abort();
  }, [refresh]);

  async function loadMore() {
    if (!cursor || loading) return;
    setLoading(true);
    setError('');
    try {
      const page = await readPage(cursor);
      setProjects((previous) => Array.from(new Map([...previous, ...page.jobs].map((job) => [job.id, job])).values()));
      setCursor(page.nextCursor);
      setSkipped((previous) => previous + page.skipped);
      setSelectedId((previous) => previous || page.jobs[0]?.id || '');
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not load projects.');
    } finally {
      setLoading(false);
    }
  }

  const selected = projects.find((project) => project.id === selectedId);
  return (
    <div className="cloud-workspace">
      <section className="cloud-card" aria-labelledby="projects-title">
        <p className="eyebrow">Google Drive · reefplot storage</p>
        <h1 id="projects-title">Past projects</h1>
        <p className="cloud-lead">Select a saved project to view its model. Browsing and viewing use no GPU compute.</p>
        <div className="past-project-actions">
          <button type="button" disabled={loading} onClick={() => setRefresh((value) => value + 1)}>Refresh projects</button>
          {apiUrl && <a href={`${apiUrl}/v1/jobs-folder`} target="_blank" rel="noopener noreferrer">Open Jobs folder in Drive</a>}
          <a href={`${basePath}/`}>New reconstruction</a>
        </div>
        {error && <p className="cloud-error" role="alert">{error}</p>}
        <div aria-busy={loading}>
          {projects.length > 0 && (
            <div className="past-project-table">
              <table>
                <thead><tr><th scope="col">Project</th><th scope="col">Created</th><th scope="col">Status</th></tr></thead>
                <tbody>{projects.map((project) => (
                  <tr key={project.id}>
                    <td><label><input type="radio" name="saved-project" checked={selectedId === project.id} onChange={() => setSelectedId(project.id)} /> {project.name} <small>· {project.id.slice(0, 8)}</small></label></td>
                    <td>{new Date(project.createdAt).toLocaleString()}</td>
                    <td>{project.state}</td>
                  </tr>
                ))}</tbody>
              </table>
            </div>
          )}
          {loading && <p role="status">Loading projects…</p>}
          {!loading && !error && projects.length === 0 && <p>No saved projects found on this page.</p>}
          {cursor && <button type="button" disabled={loading} onClick={loadMore}>Load more projects</button>}
        </div>
        {skipped > 0 && <p role="status">{skipped} folders had missing or unreadable job records.</p>}
        {selected && (
          <section className="past-project-details" aria-label="Selected project">
            <h2>{selected.name}</h2>
            <p>{selected.stage} · {selected.outputMode === 'full' ? 'Full project' : 'Compact output'}</p>
            {selected.state === 'completed' && selected.result?.modelUrl ? (
              <div className="past-project-actions">
                <a href={`${basePath}/?job=${encodeURIComponent(selected.id)}`} target="_blank" rel="noopener noreferrer">Open in viewer ↗</a>
                {selected.result.projectUrl && <a href={`${apiUrl}${selected.result.projectUrl}`} download="full-project.tar.gz">Download full project</a>}
              </div>
            ) : <p>A completed model is required to open this project in the viewer.</p>}
          </section>
        )}
      </section>
    </div>
  );
}
