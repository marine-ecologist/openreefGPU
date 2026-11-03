# Full project — openreefGPU 0.6.5-gpu.6

Choose **Save outputs → Full project + compact preview** before submitting a new
survey. The compact preview remains the default. Full project additionally
generates the original dense/mesh/texture outputs and uploads
`full-project.tar.gz` beside the preview under `reefplot/jobs/<job-id>/output/`.
The completed job page provides **Download full project (.tar.gz)**.

The archive contains the dataset folder: COLMAP database and sparse models,
camera calibration, undistorted images, OpenMVS scenes and intermediate files,
original and compact outputs, model links, `logs/pipeline.log`, and
`cloud-run.json` with the versions, command and source photograph list. Internal
model links are converted to relative paths so they work after extraction.

**Include original photographs in the archive** is optional and off by default.
When selected, photographs referenced from RunPod's source cache are embedded
as ordinary files. When omitted, copy the original photographs back into the
extracted dataset's `images/` folder before resuming work. The source files
remain in their existing Drive location either way.

This option preserves the project; it does not remove the configured feature,
dense or texture resolution caps, add Gaussian splatting, or enable processing
stages that the worker does not run. Native scene files may still contain
absolute worker paths. If a downstream tool cannot reopen a scene after
extraction, regenerate undistortion/OpenMVS import using the extracted dataset
and its retained COLMAP reconstruction.

Full projects require additional processing time, archive disk space and upload
time. The worker checks available temporary disk before creating the archive
and fails explicitly if it cannot safely fit; increase the container disk if
needed. Keep a suitable RunPod execution timeout for the archive/upload stages.
The preview and archive must both upload successfully before a Full project job
is marked completed. Earlier compact-only runs cannot be recovered because
their temporary reconstruction files have already been removed.

## Deploy together

The browser app, API and RunPod worker version must be deployed together:

1. Merge/apply the source changes to `marine-ecologist/openreefGPU` and run CI.
2. Publish the Docker image using the existing **Publish RunPod worker image**
   GitHub Actions workflow. The new tag is
   `ghcr.io/marine-ecologist/openreef-gpu:0.6.5-gpu.6`.
3. Update the RunPod endpoint image to that tag. Preserve its storage credentials,
   mounted volume and other settings; use the image's default startup command.
   If `OPENREEF_GPU_VERSION` was set explicitly in endpoint environment variables,
   update it to `0.6.5-gpu.6` too.
4. Deploy the API from `cloud/api` using `npm ci` then `npx wrangler deploy`.
   Keep all existing API secrets. Its version setting now expects GPU worker `.6`.
5. Deploy the browser app through the existing GitHub Pages workflow.
6. Submit a small Full project survey, download the archive and check its
   extracted project before submitting the full 712-image dataset.

There is no production deployment or billed GPU run included in this local
implementation. Do not enable the new UI against the old API/worker: new full
jobs are checked for API support and worker versions are checked before work.

## Validation

Offline checks (no GPU, cloud credentials or external packages required):

```sh
python3 -m unittest discover -s tests -p test_full_project.py -v
node --test tests/test_full_project_api.mjs
node scripts/check-versions.mjs
```

The Python checks cover output flags, archive contents, cached images, relative
links after extraction, invalid external links, insufficient disk, logging,
and saving the archive before temporary cleanup. API checks cover persisted
options/defaults, validation, download URLs, ranged downloads and the asset
allowlist. CI also runs the existing pytest/ruff suite and web/API build checks.

The offline checks passed locally. Full frontend/typecheck builds, the existing
pytest suite and a live reconstruction could not run in this session because
required dependencies were unavailable and network access was unavailable.

## Browse saved projects

Click **Google Drive · reefplot storage** to open **Past projects**. Select a
project, then choose **Open in viewer** to open the saved model in a new tab.
Full project jobs retain both the viewer link and the archive download. Use
**Open Jobs folder in Drive** for the original Drive folder.

The API's `GET /v1/jobs?cursor=…` lists job folders eight at a time, newest
folders first, and reads their saved `job.json` records. **Load more projects**
continues the Drive listing. Non-job folders and missing/invalid records are
skipped. Saved status is a snapshot; Refresh rereads Drive records. This browser
uses Google Drive storage and does not poll RunPod or start GPU workers. Existing
jobs from earlier worker versions can be viewed when they have a completed job
record and its referenced model asset. Bare model folders without `job.json`
are not imported automatically.

Deploy the API (`cd cloud/api && npm ci && npx wrangler deploy`) and push the
frontend changes to GitHub to publish this browser. The API dependency versions
are pinned with their lockfile. This browsing change requires no new GPU image;
processing Full project jobs still requires the matching `.6` worker image.
