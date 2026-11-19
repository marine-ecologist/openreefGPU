# RunPod volume deployment

The browser uses the OpenReef API to relay resumable video chunks to a RunPod
network volume. RunPod's S3 API requires signed requests and does not support
presigned URLs. The broker holds the credentials and relays 32 MiB parts. It
stores the job record on the same volume as the uploads.
[RunPod S3 API](https://docs.runpod.io/storage/s3-api)

## Worker

Build the updated worker from the repository root and publish it to your registry.

```sh
docker buildx build --platform linux/amd64 -f worker/Dockerfile.release \
  -t REGISTRY/openreef-gpu:0.6.5-gpu.12 --push .
```

Configure the queue-based RunPod Serverless endpoint to use that image.
Attach the same S3-enabled network volume at `/runpod-volume`. Provide
capacity for both videos, reconstruction intermediates, and download archives.
The existing native engine version remains 0.6.5. Use the same worker and API
release. Older photo-worker jobs and data can be left on their original
deployment.

## Broker and frontend

Create `cloud/api/.dev.vars` locally, or use deployment secrets for the API.
Use the endpoint and region shown for the volume in RunPod, with the volume ID
as the bucket. The S3 access key is the RunPod user ID and the S3 secret is the
RunPod S3 API key. The regular RunPod API key authenticates compute requests.

```dotenv
RUNPOD_ENDPOINT_ID=...
RUNPOD_API_KEY=...
RUNPOD_VOLUME_ID=...
RUNPOD_S3_ENDPOINT=https://s3api-DATACENTER.runpod.io
RUNPOD_S3_REGION=DATACENTER
RUNPOD_S3_ACCESS_KEY=...
RUNPOD_S3_SECRET_KEY=...
```

Start the broker from `cloud/api` for local testing on the loopback interface.

```sh
npm ci
npm run dev -- --config wrangler.jsonc --ip 127.0.0.1 --var REQUIRE_ACCESS:false
```

Start the frontend from the repository root.

```sh
npm ci
NEXT_PUBLIC_OPENREEF_API_URL=http://localhost:8787 npm run dev -- --port 3000
```

For a hosted broker, use an authenticated Cloudflare Access route and restrict
`ALLOWED_ORIGINS` to your frontend. Keep the broker's unauthenticated routes
disabled and the volume credentials server-side.

## Results and cleanup

The worker reads videos in place on the volume. Stages are downloadable as
they complete. The final `openreef-results.tar` contains published stage
archives, the GLB, diagnostics, logs and any requested full-project archive.
Extract and open the GLB locally.

After you verify the archive, the separate purge action queues a short RunPod
worker invocation on the mounted volume. It removes that job's inputs,
outputs, diagnostics and workspace under `openreef-jobs/<job-id>/`. A small
history record remains. Existing shared source folders and other jobs are
outside the purge scope.
