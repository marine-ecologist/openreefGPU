# Select an existing RunPod runtime

The new-job form includes **RunPod runtime** and **Refresh RunPod workers**.
The selector lists the configured default plus serverless endpoints named
`OpenReef …`, `OpenReef-…` or `OpenReef_…` in the account associated with the
API's RunPod key. It displays endpoint GPU pool IDs and live counts of idle,
running and initializing workers. A zero maximum disables an endpoint option.
Zero idle workers does not mean there is no GPU stock: the endpoint may have
scaled to zero. Status is a snapshot, not a reservation or stock guarantee.

This selects a queue-based endpoint. RunPod assigns an individual worker within
that endpoint; a job cannot be pinned to a specific ephemeral worker here.
Each selected endpoint must use the matching OpenReef image, credentials and
storage configuration. Endpoint names and health do not verify image versions.
The existing worker version contract still rejects incompatible images.

## Optional explicit endpoint allowlist

Set `RUNPOD_ALLOWED_ENDPOINT_IDS` on the Cloudflare Worker to a comma-separated
list of approved endpoint IDs. When set, discovery is restricted to those IDs
plus `RUNPOD_ENDPOINT_ID`; name-based discovery is disabled. This also allows
approved endpoints whose names do not start with OpenReef. No endpoint IDs or
API keys are supplied by browser build variables. The API key remains on the
server and response fields are explicitly selected; no worker environment or
template secrets are exposed.

The key needs permission to list account endpoints and read endpoint health.
When discovery fails, only the configured default is shown, with a warning.
A failed health check displays unknown status. At most 12 endpoints have health
queried per refresh, in groups of three, staying below 50 subrequests. Narrow
the allowlist if your account has more endpoints.

## Job routing and compatibility

New jobs save their endpoint ID and name in `job.json`. Submission and polling
use that saved ID, even if the deployment's default endpoint later changes.
Alternatives are revalidated before submitting compute and rejected when no
longer enabled; jobs are never silently rerouted. Older job records without an
endpoint ID retain the previous default-endpoint fallback.

Deploy the updated API and website. This feature changes no RunPod worker code
and requires no additional image build beyond the existing `.7` release.
It does not alter endpoint GPU configuration, worker counts or active-worker
settings. Configure different endpoint GPU pools in RunPod if you want choices
between different GPU configurations.

## Suggested settings for sequential OpenReef runs

Worker priority: Least VRAM; max workers: 1; active workers: 0; GPUs per worker:
1. Raising max workers permits parallel jobs; keeping active workers above zero
keeps workers warm and incurs idle charges.
