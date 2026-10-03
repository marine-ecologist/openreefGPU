# openreefGPU

`openreefGPU` is the cloud edition of [OpenReef](https://github.com/marine-ecologist/openreef).
It provides a browser workflow, a Cloudflare API broker, and scale-to-zero RunPod GPU processing.
The OpenReef desktop repository remains the source of truth for reconstruction and model-generation
code.

```text
Browser → Google Drive → Cloudflare API → RunPod GPU
        → pinned OpenReef pipeline → GLB → browser viewer
```

## Product boundary

- **OpenReef** is the desktop application and canonical Python reconstruction pipeline.
- **openreefGPU** owns cloud authentication, job orchestration, RunPod packaging, browser upload,
  progress, and web viewing.
- Pipeline fixes belong in OpenReef first. openreefGPU consumes a released OpenReef commit; it does
  not maintain a second drifting copy of the pipeline.

The first cloud release is `0.6.3-gpu.1`, based on OpenReef `0.6.3`. The suffix may advance for
cloud-only changes (`gpu.2`, `gpu.3`) without pretending the desktop pipeline changed.

## Version contract

[`versions.json`](versions.json) is the single version lock. It records:

- the openreefGPU version;
- the compatible OpenReef release, tag, and immutable commit;
- the job and result schema versions.

The RunPod Docker image fetches that exact OpenReef commit and verifies its package version during
the image build. The API includes both versions in every job, and the worker refuses jobs whose
versions do not match its image. CI also checks `package.json`, Cloudflare configuration, Docker
arguments, the actual pinned OpenReef checkout, and the newest stable OpenReef release tag. A new
desktop release therefore blocks openreefGPU's main build until its compatibility pin is updated.

For a new desktop release:

1. Release and tag OpenReef first.
2. Update the OpenReef version, tag, and commit in `versions.json`.
3. Set the openreefGPU version to `<openreef-version>-gpu.1`.
4. Update the three version build arguments in `worker/Dockerfile` and the two version variables in
   `cloud/api/wrangler.jsonc`.
5. Run `npm run check:versions -- --openreef-dir /path/to/openreef`.
6. Rebuild the RunPod image and run an end-to-end smoke test before releasing openreefGPU.

This makes version drift a build error instead of a runtime surprise.

## Components

- `app/`: openreefGPU browser interface and Three.js viewer.
- `cloud/api/`: Cloudflare Worker API and Google Drive/SharePoint adapters.
- `worker/`: RunPod handler and CUDA/OpenMVS image definition.
- `scripts/check-versions.mjs`: local and CI version-contract check.
- `CLOUD_DEPLOYMENT.md`: Google Drive, Cloudflare, RunPod, and frontend deployment.

## Local web development

```bash
npm install
npm run check:versions -- --openreef-dir /Users/rof011/openreef
npm run dev
```

Set `NEXT_PUBLIC_OPENREEF_API_URL` when testing cloud submission. Without it, the viewer and example
models still work but GPU submission remains disabled.

## RunPod image

Build from this repository root:

```bash
docker build --platform linux/amd64 \
  -f worker/Dockerfile \
  -t YOUR_REGISTRY/openreef-gpu:0.6.3-gpu.1 .
```

The Dockerfile packages OpenReef `0.6.3` from its pinned commit, CUDA COLMAP, CUDA OpenMVS, and the
small openreefGPU job adapter. See [CLOUD_DEPLOYMENT.md](CLOUD_DEPLOYMENT.md) for complete setup and
the required smoke test.

## Storage

Personal Google Drive is the initial provider. OpenReef only requests the `drive.file` scope and can
access the `OpenReef` folder and files it creates. The existing SharePoint adapter remains available
for later organisational deployment.
