# Stereo deployment — 13 November 2026

- Main local application: `openreefGPU/` (stereo changes integrated).
- GitHub source: commits `80818c7` and `680c571` on `marine-ecologist/openreefGPU` main.
- Frontend: https://marine-ecologist.github.io/openreefGPU/ — displays `0.6.5-gpu.10` and the stereo camera-folder form.
- API: https://openreef-gpu-api.openreef-gpu.workers.dev — health reports `0.6.5-gpu.10`.
- Cloudflare deployment: `8f6058fe-1269-4655-a960-c0d4a2691f01`.
- Image build: https://github.com/marine-ecologist/openreefGPU/actions/runs/43478749392 — **success**.
- Registry: `ghcr.io/marine-ecologist/openreef-gpu:0.6.5-gpu.10`.
- Published image digest: `sha256:b468fa9759fccc3c887937ea0009053f219fc473efeae716f855b35d258b2d12`.
- RunPod endpoint: `93jkogjuc9l6pu`, release **58**. Image and explicit worker-version override both changed from `0.6.5-gpu.8` to `0.6.5-gpu.10` in one saved update.
- Worker limits remain max 3, active 0; GPU selection, container disk, start command and storage credentials were preserved.

Verification: GitHub CI and Pages deployment passed; the main local application's
62 Python tests passed; live API health and the existing Drive connection passed;
incomplete stereo configuration returns HTTP 400 before creating a job.
The published image uses the existing native runtime pinned by immutable digest,
with native dependency checks before installing the tested stereo patch.

Final rollout status: **complete**. The console reports the endpoint Ready and
all three listed workers on release **58**, with no outdated workers remaining.
A subsequent API health check showed one idle worker and two initializing, with
no running or queued jobs.
The rollout completed automatically; no worker was manually terminated. Worker
IDs at verification were `ht3qo0dv3tlkss`, `j60sng6ppcvoi4`, and `kl8cnmi7ard32m`.

A real GPU stereo reconstruction remains for the user's test survey. The frontend,
API and worker rollout are deployed and ready for the user's stereo test survey.
