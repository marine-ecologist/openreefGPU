#!/usr/bin/env node

import { execFileSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

const repositoryRoot = resolve(import.meta.dirname, '..');
const versions = JSON.parse(
  readFileSync(resolve(repositoryRoot, 'versions.json'), 'utf8'),
);
const output = execFileSync(
  'git',
  ['ls-remote', '--tags', '--refs', versions.openreef.repository],
  { encoding: 'utf8' },
);
const releases = output
  .split('\n')
  .map((line) => line.match(/refs\/tags\/v(\d+)\.(\d+)\.(\d+)$/))
  .filter(Boolean)
  .map((match) => ({
    tag: `v${match[1]}.${match[2]}.${match[3]}`,
    parts: match.slice(1).map(Number),
  }))
  .sort((left, right) => {
    for (let index = 0; index < 3; index += 1) {
      const difference = right.parts[index] - left.parts[index];
      if (difference) return difference;
    }
    return 0;
  });

if (!releases.length) throw new Error('OpenReef has no semantic release tags');
if (releases[0].tag !== versions.openreef.ref) {
  throw new Error(
    `openreefGPU pins ${versions.openreef.ref}, but the latest OpenReef release is ${releases[0].tag}. Update versions.json and rebuild the RunPod image.`,
  );
}

console.log(`Upstream release contract OK: ${releases[0].tag}.`);
