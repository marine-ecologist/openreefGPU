#!/usr/bin/env node

import { execFileSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

const repositoryRoot = resolve(import.meta.dirname, '..');
const versions = readJson('versions.json');
const packageJson = readJson('package.json');
const apiPackage = readJson('cloud/api/package.json');
const dockerfile = readFileSync(
  resolve(repositoryRoot, 'worker/Dockerfile'),
  'utf8',
);
const wrangler = readFileSync(
  resolve(repositoryRoot, 'cloud/api/wrangler.jsonc'),
  'utf8',
);

assert(
  packageJson.version === versions.openreefGPU.version,
  `package.json is ${packageJson.version}; expected ${versions.openreefGPU.version}`,
);
assert(
  apiPackage.version === versions.openreefGPU.version,
  `cloud/api/package.json is ${apiPackage.version}; expected ${versions.openreefGPU.version}`,
);
assert(
  versions.openreef.ref === `v${versions.openreef.version}`,
  'The pinned OpenReef tag must match its declared version',
);
assert(
  /^[0-9a-f]{40}$/.test(versions.openreef.commit),
  'The pinned OpenReef commit must be a full 40-character SHA',
);
assert(
  /^[0-9a-f]{40}$/.test(versions.colmap.commit),
  'The pinned COLMAP commit must be a full 40-character SHA',
);
assert(
  versions.colmap.casparEnabled === true,
  'The pinned COLMAP build must declare Caspar enabled',
);
assert(
  versions.colmap.ceresCudaEnabled === true &&
    versions.colmap.ceresCudssEnabled === true,
  'The pinned COLMAP build must declare CUDA/cuDSS-enabled Ceres',
);
assert(
  /^[0-9a-f]{40}$/.test(versions.ceres.commit),
  'The pinned Ceres commit must be a full 40-character SHA',
);
assert(
  versions.ceres.cudaEnabled === true && versions.ceres.cudssEnabled === true,
  'The pinned Ceres build must enable CUDA and cuDSS',
);
assert(
  /^[0-9a-f]{40}$/.test(versions.openMVS.commit),
  'The pinned OpenMVS commit must be a full 40-character SHA',
);
assert(
  /^[0-9a-f]{40}$/.test(versions.cgal.commit),
  'The pinned CGAL commit must be a full 40-character SHA',
);
assert(
  versions.openreefGPU.version.startsWith(`${versions.openreef.version}-gpu.`),
  'The openreefGPU version must start with the pinned OpenReef version followed by -gpu.',
);
assertDockerArg('OPENREEF_VERSION', versions.openreef.version);
assertDockerArg('OPENREEF_REF', versions.openreef.commit);
assertDockerArg('OPENREEF_GPU_VERSION', versions.openreefGPU.version);
assertDockerArg('CERES_VERSION', versions.ceres.version);
assertDockerArg('CERES_REF', versions.ceres.commit);
assertDockerArg('COLMAP_VERSION', versions.colmap.version);
assertDockerArg('COLMAP_REF', versions.colmap.commit);
assertDockerArg('OPENMVS_VERSION', versions.openMVS.version);
assertDockerArg('OPENMVS_REF', versions.openMVS.commit);
assertDockerArg('CGAL_VERSION', versions.cgal.version);
assertDockerArg('CGAL_REF', versions.cgal.commit);
assert(
  dockerfile.includes("'CERES_CUDA_ENABLED=ON'") &&
    dockerfile.includes("'CERES_CUDSS_ENABLED=ON'"),
  'worker/Dockerfile must verify the COLMAP Ceres CUDA/cuDSS link contract',
);
assert(
  dockerfile.includes('OPENREEF_BA_BACKEND=ceres') &&
    dockerfile.includes('OPENREEF_CERES_USE_GPU=1'),
  'worker/Dockerfile must default the 0.6.5 cloud mapper to Ceres CUDA',
);
assert(
  wrangler.includes(`"OPENREEF_VERSION": "${versions.openreef.version}"`),
  'Cloudflare OPENREEF_VERSION does not match versions.json',
);
assert(
  wrangler.includes(
    `"OPENREEF_GPU_VERSION": "${versions.openreefGPU.version}"`,
  ),
  'Cloudflare OPENREEF_GPU_VERSION does not match versions.json',
);

const upstreamFlag = process.argv.indexOf('--openreef-dir');
if (upstreamFlag >= 0) {
  const upstream = resolve(process.argv[upstreamFlag + 1] || '');
  const pyproject = readFileSync(resolve(upstream, 'pyproject.toml'), 'utf8');
  const match = pyproject.match(/^version\s*=\s*"([^"]+)"/m);
  assert(match, 'Could not read the OpenReef version from pyproject.toml');
  assert(
    match[1] === versions.openreef.version,
    `Pinned OpenReef source is ${match[1]}; expected ${versions.openreef.version}`,
  );
  const commit = execFileSync('git', ['-C', upstream, 'rev-parse', 'HEAD'], {
    encoding: 'utf8',
  }).trim();
  assert(
    commit === versions.openreef.commit,
    `Pinned OpenReef commit is ${commit}; expected ${versions.openreef.commit}`,
  );
}

console.log(
  `Version contract OK: openreefGPU ${versions.openreefGPU.version} packages OpenReef ${versions.openreef.version} (${versions.openreef.commit.slice(0, 12)}).`,
);

function readJson(path) {
  return JSON.parse(readFileSync(resolve(repositoryRoot, path), 'utf8'));
}

function assertDockerArg(name, expected) {
  const match = dockerfile.match(new RegExp(`^ARG ${name}=(.+)$`, 'm'));
  assert(match, `worker/Dockerfile is missing ARG ${name}`);
  assert(
    match[1].trim() === expected,
    `worker/Dockerfile ${name} is ${match[1].trim()}; expected ${expected}`,
  );
}

function assert(condition, message) {
  if (!condition) throw new Error(message);
}
