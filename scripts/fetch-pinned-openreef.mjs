#!/usr/bin/env node

import { execFileSync } from 'node:child_process';
import { existsSync, mkdirSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';

const repositoryRoot = resolve(import.meta.dirname, '..');
const versions = JSON.parse(
  readFileSync(resolve(repositoryRoot, 'versions.json'), 'utf8'),
);
const destination = resolve(process.argv[2] || '.upstream/openreef');

mkdirSync(destination, { recursive: true });
if (!existsSync(resolve(destination, '.git'))) run(['init', destination]);
const remotes = execFileSync('git', ['-C', destination, 'remote'], {
  encoding: 'utf8',
})
  .split(/\s+/)
  .filter(Boolean);
if (remotes.includes('origin')) {
  run([
    '-C',
    destination,
    'remote',
    'set-url',
    'origin',
    versions.openreef.repository,
  ]);
} else {
  run([
    '-C',
    destination,
    'remote',
    'add',
    'origin',
    versions.openreef.repository,
  ]);
}
run([
  '-C',
  destination,
  'fetch',
  '--depth',
  '1',
  'origin',
  versions.openreef.commit,
]);
run(['-C', destination, 'checkout', '--detach', 'FETCH_HEAD']);

console.log(
  `Fetched OpenReef ${versions.openreef.version} at ${versions.openreef.commit}.`,
);

function run(arguments_) {
  execFileSync('git', arguments_, { stdio: 'inherit' });
}
