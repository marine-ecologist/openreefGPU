# openreefGPU deployment

The first deployment target uses a personal Google Drive account for durable storage:

```text
browser → Google Drive resumable upload → Cloudflare API → Runpod Serverless GPU
        → Google Drive output/model.glb → Cloudflare proxy → Three.js viewer
```

Google Drive holds each job's input photographs, status, and outputs. The API and GPU worker need no
database or persistent disk, so Runpod can keep **Active workers = 0** and scale to zero between
reconstructions.

Each worker copies its job's source images from Drive to ephemeral local disk before processing.
The browser job screen reports live image count, bytes, elapsed time, and average throughput during
that transfer and retains the final summary for the rest of the run. Retrying a job downloads the
source set again unless a persistent Runpod cache is added later.

SharePoint remains supported by setting `STORAGE_PROVIDER=sharepoint`; see the final section.

## Current live deployment

The initial Google Drive deployment is available at:

```text
Browser: https://marine-ecologist.github.io/openreefGPU/
API:     https://openreef-gpu-api.openreef-gpu.workers.dev
RunPod:  endpoint 93jkogjuc9l6pu (openreef-gpu)
```

GitHub Actions injects the API address from the repository variable `OPENREEF_API_URL`. Cloudflare
allows the exact production origin `https://marine-ecologist.github.io` and the local development
origins declared in `cloud/api/wrangler.jsonc`. Disposable Cloudflare preview URLs are disabled.

This first deployment currently uses `REQUIRE_ACCESS=false` for smoke testing. Do not leave it as
an unattended public service: enable Cloudflare Access and change `REQUIRE_ACCESS` to `true` after
the first end-to-end reconstruction has been verified.

## Repository pieces

- `app/cloud-jobs.tsx`: photograph selection, chunked uploads, job polling, and viewer handoff.
- `cloud/api`: Cloudflare Worker that keeps Google and Runpod credentials out of the browser.
- `cloud/api/scripts/google-drive-oauth.mjs`: one-time personal Google account authorization helper.
- `worker/handler.py`: RunPod handler that invokes the pinned OpenReef pipeline and publishes
  compact outputs.
- `worker/Dockerfile`: CUDA COLMAP + OpenMVS image pinned to the OpenReef version in `versions.json`.

The API and RunPod endpoint must use the same Google OAuth values, root folder ID,
`GOOGLE_DRIVE_JOB_ROOT`, `OPENREEF_VERSION`, and `OPENREEF_GPU_VERSION` values.

## 1. Connect a personal Google Drive

This is a one-time setup for the Gmail account that will own the OpenReef files.

1. Create or select a project in [Google Cloud Console](https://console.cloud.google.com/).
2. Enable the **Google Drive API** for that project.
3. Configure the OAuth consent screen:
   - Audience: **External**.
   - While testing, add the Gmail address as a test user.
   - Add the `.../auth/drive.file` scope if the console asks for scopes. This lets OpenReef access
     only files and folders it creates, not the rest of My Drive.
4. Create an OAuth client with application type **Desktop app** and download its JSON file.
5. From `openreefGPU/cloud/api`, run one of these commands. The default creates a dedicated
   `OpenReef` folder with the narrow `drive.file` permission:

   ```bash
   npm run google-drive:authorize -- /path/to/client_secret.json
   ```

   To reuse an existing dedicated folder such as `reefplot`, run:

   ```bash
   npm run google-drive:authorize -- /path/to/client_secret.json --folder reefplot --existing --output-env .dev.vars
   ```

   Google requires the broader Drive permission to locate and use a folder that existed before this
   OAuth app. Use a dedicated root such as `reefplot`; the deployed API and worker are configured to
   operate only below the returned folder ID.

6. Open the address printed by the helper, sign in, and approve access. With `--output-env`, the
   helper saves the four values below to a private, git-ignored `.dev.vars` file (mode `600`).
   Without that option, it prints them so they can be copied directly into a secret manager:

   ```text
   GOOGLE_CLIENT_ID
   GOOGLE_CLIENT_SECRET
   GOOGLE_REFRESH_TOKEN
   GOOGLE_DRIVE_ROOT_FOLDER_ID
   ```

Treat all four as secrets. Do not commit the downloaded JSON file, the refresh token, or a populated
`.dev.vars` file.

Google expires refresh tokens after seven days while an External OAuth app remains in **Testing**
and requests a Drive scope. That is acceptable for the first smoke test. Before unattended use,
move the OAuth app to **Production** and review Google's publishing requirements, or expect to run
the authorization helper again when the test token expires.

For the current `reefplot` deployment, OpenReef uses this layout without moving the existing source
photographs:

```text
reefplot/
├── images/                    # existing photographs
└── jobs/
    └── <uuid>/
        ├── job.json
        ├── input/             # used only for new browser uploads
        └── output/
            ├── model.glb
            ├── flow-surface.ply       # when produced
            ├── sparse.ply             # when produced
            └── manifest.json
```

## 2. Build and publish the GPU worker

The recommended first deployment is the included GitHub Actions workflow. In the repository's
**Actions** tab, run **Publish RunPod worker image**. It builds on a Linux runner and publishes:

```text
ghcr.io/<github-owner>/openreef-gpu:0.6.3-gpu.5
```

Make that package public, or add GitHub Container Registry credentials to the RunPod template. The
versioned tag is the source of truth for the endpoint; `latest` is provided only for convenience.

For a local build instead, run this from the openreefGPU repository root. The image fetches the
exact OpenReef desktop commit declared by its build arguments and verifies the installed package
version:

```bash
docker build --platform linux/amd64 \
  -f worker/Dockerfile \
  -t YOUR_REGISTRY/openreef-gpu:0.6.3-gpu.5 .
docker push YOUR_REGISTRY/openreef-gpu:0.6.3-gpu.5
```

The image starts from CUDA-enabled COLMAP and builds OpenMVS with CUDA enabled. It is large, so a
container-registry CI builder is usually more practical than a laptop build.

Create a **queue-based** Runpod Serverless endpoint from that image with:

- GPU: one 24 GB card for initial trials; allow a 48 GB fallback for larger surveys.
- Active workers: `0`.
- Max workers: `1` initially, as a cost and concurrency limit.
- GPUs per worker: `1`.
- Idle timeout: the minimum suitable value.
- Container disk: start at 100 GB for photographs and reconstruction intermediates.
- Execution timeout: 48 hours.
- Job TTL: 72 hours.

Set these Runpod endpoint secrets/environment variables:

```text
STORAGE_PROVIDER=google-drive
GOOGLE_CLIENT_ID
GOOGLE_CLIENT_SECRET
GOOGLE_REFRESH_TOKEN
GOOGLE_DRIVE_ROOT_FOLDER_ID
GOOGLE_DRIVE_JOB_ROOT=jobs
OPENREEF_VERSION=0.6.3
OPENREEF_GPU_VERSION=0.6.3-gpu.5
```

Optional compact-profile tuning variables are `OPENREEF_MAX_IMAGE_SIZE`,
`OPENREEF_MAX_RESOLUTION`, `OPENREEF_MAX_TEXTURE_SIZE`, and
`OPENREEF_TEXTURE_RESOLUTION_LEVEL`. `OPENREEF_MAX_CORES` defaults to `32` to avoid passing very
large cloud-host CPU counts into COLMAP and OpenMVS.

Record the Runpod endpoint ID and create a scoped Runpod API key for the broker.

## 3. Deploy the API broker

`cloud/api/wrangler.jsonc` is already configured for Google Drive. Set `ALLOWED_ORIGINS` to the exact
HTTPS frontend origin(s), comma separated. For local API work, copy `.dev.vars.example` to
`.dev.vars` and enter the values printed by the authorization helper plus the Runpod values.

For Cloudflare deployment, run from `openreefGPU/cloud/api`:

```bash
npm install
npx wrangler secret put GOOGLE_CLIENT_ID
npx wrangler secret put GOOGLE_CLIENT_SECRET
npx wrangler secret put GOOGLE_REFRESH_TOKEN
npx wrangler secret put GOOGLE_DRIVE_ROOT_FOLDER_ID
npx wrangler secret put RUNPOD_ENDPOINT_ID
npx wrangler secret put RUNPOD_API_KEY
npx wrangler deploy
```

Keep `GOOGLE_DRIVE_JOB_ROOT=jobs` equal to the Runpod setting.

Before each main build, run the version guard from the repository root:

```bash
npm run check:versions -- --openreef-dir /Users/rof011/openreef
```

The build must stop if the desktop release, Docker image, API, and cloud package versions disagree.

Before making GPU submissions publicly reachable, change `REQUIRE_ACCESS` to `true` and place the API
hostname behind a Cloudflare Access self-hosted application. Require an authenticated user for
`/v1/*`. The Worker checks Cloudflare's authenticated-email header, preventing anonymous visitors
from starting billable GPU jobs.

## 4. Deploy the browser frontend

Set the deployed API origin, with no trailing slash:

```text
NEXT_PUBLIC_OPENREEF_API_URL=https://openreef-api.example.workers.dev
```

Then build and deploy the existing static site. For the included GitHub Pages workflow, create a
repository variable named `OPENREEF_API_URL`; the workflow maps it into the public build variable.
Without it, the public example viewer still builds and the Cloud GPU screen reports that it is not
connected.

When Cloudflare Access is enabled, users may need to sign in to the API hostname once before the
browser can make credentialed cross-origin requests. `ALLOWED_ORIGINS` must exactly match the final
frontend origin.

## 5. Smoke test

1. Use 10–30 small, strongly overlapping photographs for the first test.
2. Open the **Cloud GPU** screen, choose the files, and submit the job.
3. Confirm `OpenReef/jobs/<job-id>/input/` and `job.json` appear in Google Drive.
4. Confirm Runpod scales from zero to one worker and the UI advances through reconstruction stages.
5. Confirm `output/model.glb` and `output/manifest.json` appear in Drive.
6. Select **Open model in viewer** and verify orbit, pan, zoom, display mode, screenshot, and full
   screen.
7. Confirm the Runpod worker returns to zero after its idle timeout.

A container start alone is not a successful smoke test. The complete test must exercise CUDA feature
extraction, OpenMVS densification/meshing/texturing, Drive output upload, and browser GLB loading.

## Operations and limits

- Browser uploads use 10 MiB ranges and go directly to Google's resumable-session URL; photograph
  content does not pass through Cloudflare.
- The API rejects unsupported extensions, duplicate names, a single file over 2 GiB, or a job over
  50 GiB. Start much smaller than these safety limits.
- The finished GLB is streamed through the authenticated API, keeping the OAuth refresh token and
  Drive file IDs out of the browser.
- The API requests a 48-hour Runpod execution timeout and a 72-hour job TTL. Google Drive retains
  the job record and output after Runpod's result window expires.
- Delete old job folders from `OpenReef/jobs` when no longer needed; personal Drive storage is not
  automatically cleaned up.
- A failed job stores its last stage and error in `job.json`; detailed process output remains in the
  Runpod worker logs.

The implementation follows Google's
[resumable-upload protocol](https://developers.google.com/workspace/drive/api/guides/manage-uploads),
[offline OAuth flow](https://developers.google.com/identity/protocols/oauth2/web-server), and
[Drive scope guidance](https://developers.google.com/workspace/drive/api/guides/api-specific-auth),
plus Runpod's [async request API](https://docs.runpod.io/serverless/endpoints/send-requests).

## Optional SharePoint backend

The original SharePoint implementation remains available. Set `STORAGE_PROVIDER=sharepoint` in both
the API and Runpod endpoint, remove the Google variables, and configure:

```text
GRAPH_TENANT_ID
GRAPH_CLIENT_ID
GRAPH_CLIENT_SECRET
SHAREPOINT_DRIVE_ID
SHAREPOINT_JOB_ROOT=OpenReef/jobs
```

The Entra application needs write access to the chosen SharePoint document library. Prefer
`Sites.Selected` permission scoped to the OpenReef site. No frontend change is required when
switching providers.
