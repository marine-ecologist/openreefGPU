# Follow stable OpenReef releases

The **Follow stable OpenReef releases** workflow checks hourly, at minute 17
UTC (GitHub scheduling may be delayed), and can also be started manually.
It follows stable semantic version tags, not every desktop commit or prerelease.

For example, OpenReef `v0.6.6` creates openreefGPU `0.6.6-gpu.1`. The workflow
resolves annotated or lightweight tags to a commit and verifies that the
source's declared package version matches the tag. It updates:

- `versions.json`, including the full OpenReef commit SHA;
- browser and API package versions and their lockfile root versions;
- Docker's desktop version, source commit and cloud version;
- Cloudflare version variables and the browser sidebar version.

It opens or updates one PR on `automation/openreef-release` and explicitly
starts the compatibility workflow for that branch. Existing releases are a
no-op. Downgrades, malformed versions and moved pinned tags fail rather than
silently rewriting the contract. Upstream toolchain pins and dependency
versions are not automatically changed.

## Enable in GitHub

Push these workflow files to `main`. In **Settings → Actions → General →
Workflow permissions**, enable **Allow GitHub Actions to create and approve
pull requests**. The workflow requests contents, pull-request and Actions write
permissions. No additional personal token is required. The Actions permission
is used to dispatch CI explicitly; bot-created events can otherwise require
approval or fail to start additional workflows.

Review and merge the release PR once its checks pass. The **Publish RunPod
worker image** workflow then builds the exact main-branch commit that passed
validation, provided `versions.json` changed. Ordinary main-branch changes do
not rebuild the image. Manual publishing and GPU version tags remain supported.

## Deployment remains coordinated

This automates version maintenance, compatibility checks and image publishing.
It does not switch the live RunPod endpoint or deploy the Cloudflare API. Once
the image is published, update the endpoint to its versioned image, ensure new
workers use it, and deploy the matching API before submitting new jobs. Existing
jobs retain the version they were created with; make a new job for the new
runtime. Avoid submitting jobs during the switch.

A desktop update can change pipeline behaviour. The automated checks and image
build are useful compatibility checks, not a live GPU reconstruction test.

## Local use

`npm run sync:upstream` updates the version files to the latest stable tag.

`node --test tests/test_release_sync.mjs` tests tag selection, all version
updates, repeat execution, moved tags, downgrades and source-version mismatches
using a temporary local upstream Git repository, with no network access.
