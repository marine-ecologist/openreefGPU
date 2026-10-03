import type { NextConfig } from 'next';

const repositoryName = process.env.GITHUB_REPOSITORY?.split('/')[1];
const deploymentPath =
  process.env.GITHUB_ACTIONS === 'true' && repositoryName
    ? `/${repositoryName}`
    : '';

const nextConfig: NextConfig = {
  output: 'export',
  assetPrefix: deploymentPath,
  env: {
    NEXT_PUBLIC_BASE_PATH: deploymentPath,
    NEXT_PUBLIC_OPENREEF_API_URL:
      process.env.NEXT_PUBLIC_OPENREEF_API_URL ?? '',
  },
};

export default nextConfig;
