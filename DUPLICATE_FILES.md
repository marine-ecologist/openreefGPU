# Duplicate photo handling — openreefGPU 0.6.5-gpu.7

Google Drive permits several files with the same name in one folder. Source
folder jobs now select photos by immutable Drive file ID, retaining the
original name, size and checksum in the saved job manifest.

- Repeated photos with the same Drive MD5 checksum and byte count are included
  once, including copies uploaded under different names. Deduplication occurs
  before the requested photo-count limit is applied.
- Distinct photos with colliding filenames are preserved as `photo.jpg`,
  `photo__2.jpg`, etc. Name comparison also handles case and sanitized-name
  collisions. Selection and naming are deterministic for the same folder.
- Missing checksums never establish that files are identical. Distinct IDs with
  matching names and sizes are both retained when content cannot be verified.
- Original photos are neither deleted nor renamed in Google Drive. Only local
  filenames in the reconstruction change. The job page reports skipped copies
  and filenames changed for the project.
- Before GPU submission and again when staging, the selected ID, name, size and
  available checksum are checked. A missing or changed photo requires a new
  job. The worker downloads by ID rather than resolving ambiguous filenames.
- Cache entries for ID-selected photos include the Drive ID and checksum. Such
  photos without a checksum bypass the source cache.

This applies to source photographs. Ambiguous duplicate folders or job metadata
still raise errors: selecting a folder or mutable job record arbitrarily could
load the wrong project. Browser uploads still reject ambiguous filenames.

## Deployment

The website, API and RunPod image must all use `.7` for new jobs. Publish the
worker using the **Publish RunPod worker image** workflow at the new commit,
then configure the endpoint to use:

`ghcr.io/marine-ecologist/openreef-gpu:0.6.5-gpu.7`

Deploy `cloud/api` with `npm ci && npx wrangler deploy` and publish the website.
Do not submit jobs while API and worker versions differ. Saved completed jobs
can still be viewed. Create a new job to apply duplicate handling to a source
folder; older manifests cannot recover photos they never selected.

## Verification

`node --test tests/test_full_project_api.mjs tests/test_upload_budget.mjs`

`python3 -m unittest discover -s tests -p test_duplicate_files.py`

Tests cover identical content under different names, different photos sharing a
name and size, missing checksums, filename collisions, deterministic selection,
Drive pagination, source substitution, and legacy manifest ambiguity. They use
mocked storage and Google responses; a live GPU reconstruction is not included.
