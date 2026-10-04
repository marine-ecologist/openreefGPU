import { rename, rmdir } from 'node:fs/promises';
import { join } from 'node:path';

const repository = process.env.GITHUB_REPOSITORY;

if (!repository) {
  console.log(
    'No GitHub repository path detected; no output normalization needed.',
  );
  process.exit(0);
}

const repositoryName = repository.split('/')[1];

if (!repositoryName) {
  throw new Error(`Invalid GITHUB_REPOSITORY value: ${repository}`);
}

const clientDirectory = join(process.cwd(), 'dist', 'client');
const prefixedDirectory = join(clientDirectory, repositoryName);

await rename(join(prefixedDirectory, '_next'), join(clientDirectory, '_next'));
await rmdir(prefixedDirectory);

console.log(
  `Prepared static assets for the /${repositoryName}/ GitHub Pages path.`,
);
