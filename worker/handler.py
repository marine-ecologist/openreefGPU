"""Runpod Serverless entry point for the cloud-backed OpenReef pipeline."""

from __future__ import annotations

import json
import mimetypes
import os
import re
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

GRAPH_ROOT = "https://graph.microsoft.com/v1.0"
GOOGLE_DRIVE_ROOT = "https://www.googleapis.com/drive/v3"
GOOGLE_UPLOAD_ROOT = "https://www.googleapis.com/upload/drive/v3"
JOB_ID_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
STAGE_PROGRESS = {
    "Feature extraction": 8,
    "Sequential matching": 18,
    "Sparse reconstruction": 30,
    "Undistort / PINHOLE": 40,
    "OpenMVS import": 48,
    "Dense point cloud": 64,
    "Surface mesh": 80,
    "Texture mesh": 92,
}
JOB_SCHEMA_VERSION = 1


class WorkerConfigurationError(RuntimeError):
    """The container is missing a required deployment setting."""


class GraphClient:
    """Small Microsoft Graph client using app-only client credentials."""

    def __init__(self) -> None:
        self.tenant_id = required_env("GRAPH_TENANT_ID")
        self.client_id = required_env("GRAPH_CLIENT_ID")
        self.client_secret = required_env("GRAPH_CLIENT_SECRET")
        self.drive_id = required_env("SHAREPOINT_DRIVE_ID")
        self.job_root = os.environ.get("SHAREPOINT_JOB_ROOT", "OpenReef/jobs").strip("/")
        validate_job_root(self.job_root, "SHAREPOINT_JOB_ROOT")
        self._token = ""
        self._token_expires = 0.0

    def job_path(self, job_id: str) -> str:
        return f"{self.job_root}/{job_id}"

    def get_json(self, path: str) -> dict[str, Any]:
        payload = self.request(
            "GET",
            f"/drives/{urllib.parse.quote(self.drive_id)}/root:/{encode_graph_path(path)}:/content",
        )
        if not isinstance(payload, dict):
            raise RuntimeError("Cloud job metadata is not valid JSON")
        return payload

    def request(
        self,
        method: str,
        path: str,
        *,
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        request_headers = {"Authorization": f"Bearer {self.token()}"}
        request_headers.update(headers or {})
        request = urllib.request.Request(
            f"{GRAPH_ROOT}{path}", data=data, headers=request_headers, method=method
        )
        try:
            with urllib.request.urlopen(request, timeout=300) as response:
                payload = response.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            raise RuntimeError(f"Microsoft Graph returned {exc.code}: {detail}") from exc
        if not payload:
            return None
        content_type = response.headers.get_content_type()
        if content_type == "application/json" or payload[:1] in {b"{", b"["}:
            return json.loads(payload)
        return payload

    def token(self) -> str:
        if self._token and self._token_expires > time.time() + 60:
            return self._token
        body = urllib.parse.urlencode(
            {
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "scope": "https://graph.microsoft.com/.default",
                "grant_type": "client_credentials",
            }
        ).encode()
        request = urllib.request.Request(
            "https://login.microsoftonline.com/"
            f"{urllib.parse.quote(self.tenant_id)}/oauth2/v2.0/token",
            data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                payload = json.load(response)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            raise WorkerConfigurationError(
                f"Microsoft identity authentication failed ({exc.code}): {detail}"
            ) from exc
        self._token = str(payload["access_token"])
        self._token_expires = time.time() + int(payload.get("expires_in", 3600))
        return self._token

    def list_files(self, folder: str) -> list[dict[str, Any]]:
        path = (
            f"/drives/{urllib.parse.quote(self.drive_id)}/root:/"
            f"{encode_graph_path(folder)}:/children?"
            "$select=id,name,size,file,@microsoft.graph.downloadUrl"
        )
        items: list[dict[str, Any]] = []
        while path:
            page = self.request("GET", path)
            items.extend(page.get("value", []))
            next_link = page.get("@odata.nextLink")
            path = next_link.removeprefix(GRAPH_ROOT) if next_link else ""
        return [item for item in items if "file" in item]

    def download_folder(
        self, source: str, destination: Path, expected_names: list[str] | None = None
    ) -> int:
        destination.mkdir(parents=True, exist_ok=True)
        items = self.list_files(source)
        items = select_expected_files(items, expected_names)
        if not items:
            raise RuntimeError("The cloud input folder contains no source images")
        for index, item in enumerate(items, start=1):
            name = safe_file_name(item["name"])
            download_url = item.get("@microsoft.graph.downloadUrl")
            if not download_url:
                raise RuntimeError(f"SharePoint did not return a download URL for {name}")
            print(f"Downloading source image {index}/{len(items)}: {name}", flush=True)
            with urllib.request.urlopen(download_url, timeout=600) as response:
                with (destination / name).open("wb") as output:
                    shutil.copyfileobj(response, output, length=8 * 1024 * 1024)
        return len(items)

    def put_bytes(self, path: str, payload: bytes, content_type: str) -> None:
        if len(payload) <= 4 * 1024 * 1024:
            self.request(
                "PUT",
                f"/drives/{urllib.parse.quote(self.drive_id)}/root:/"
                f"{encode_graph_path(path)}:/content",
                data=payload,
                headers={"Content-Type": content_type},
            )
            return
        self._upload_large(path, payload, content_type)

    def put_file(self, path: str, source: Path) -> None:
        content_type = mimetypes.guess_type(source.name)[0] or "application/octet-stream"
        size = source.stat().st_size
        if size <= 4 * 1024 * 1024:
            self.put_bytes(path, source.read_bytes(), content_type)
            return
        session = self.request(
            "POST",
            f"/drives/{urllib.parse.quote(self.drive_id)}/root:/"
            f"{encode_graph_path(path)}:/createUploadSession",
            data=json.dumps(
                {"item": {"@microsoft.graph.conflictBehavior": "replace", "name": source.name}}
            ).encode(),
            headers={"Content-Type": "application/json"},
        )
        self._upload_stream(session["uploadUrl"], source, size, content_type)

    def _upload_large(self, path: str, payload: bytes, content_type: str) -> None:
        name = path.rsplit("/", 1)[-1]
        session = self.request(
            "POST",
            f"/drives/{urllib.parse.quote(self.drive_id)}/root:/"
            f"{encode_graph_path(path)}:/createUploadSession",
            data=json.dumps(
                {"item": {"@microsoft.graph.conflictBehavior": "replace", "name": name}}
            ).encode(),
            headers={"Content-Type": "application/json"},
        )
        with tempfile.NamedTemporaryFile() as temporary:
            temporary.write(payload)
            temporary.flush()
            self._upload_stream(
                session["uploadUrl"], Path(temporary.name), len(payload), content_type
            )

    @staticmethod
    def _upload_stream(upload_url: str, source: Path, size: int, content_type: str) -> None:
        # Graph requires every non-final fragment to be a multiple of 320 KiB.
        chunk_size = 10 * 1024 * 1024
        with source.open("rb") as stream:
            start = 0
            while start < size:
                chunk = stream.read(chunk_size)
                end = start + len(chunk) - 1
                request = urllib.request.Request(
                    upload_url,
                    data=chunk,
                    headers={
                        "Content-Length": str(len(chunk)),
                        "Content-Range": f"bytes {start}-{end}/{size}",
                        "Content-Type": content_type,
                    },
                    method="PUT",
                )
                try:
                    with urllib.request.urlopen(request, timeout=900):
                        pass
                except urllib.error.HTTPError as exc:
                    detail = exc.read().decode("utf-8", errors="replace")[:500]
                    raise RuntimeError(
                        f"SharePoint upload failed at byte {start} ({exc.code}): {detail}"
                    ) from exc
                start = end + 1


class GoogleDriveClient:
    """Google Drive client using a personal account's OAuth refresh token."""

    folder_mime_type = "application/vnd.google-apps.folder"

    def __init__(self) -> None:
        self.client_id = required_env("GOOGLE_CLIENT_ID")
        self.client_secret = required_env("GOOGLE_CLIENT_SECRET")
        self.refresh_token = required_env("GOOGLE_REFRESH_TOKEN")
        self.root_folder_id = required_env("GOOGLE_DRIVE_ROOT_FOLDER_ID")
        self.job_root = os.environ.get("GOOGLE_DRIVE_JOB_ROOT", "jobs").strip("/")
        validate_job_root(self.job_root, "GOOGLE_DRIVE_JOB_ROOT")
        self._token = ""
        self._token_expires = 0.0

    def job_path(self, job_id: str) -> str:
        return f"{self.job_root}/{job_id}"

    def token(self) -> str:
        if self._token and self._token_expires > time.time() + 60:
            return self._token
        body = urllib.parse.urlencode(
            {
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "refresh_token": self.refresh_token,
                "grant_type": "refresh_token",
            }
        ).encode()
        request = urllib.request.Request(
            "https://oauth2.googleapis.com/token",
            data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                payload = json.load(response)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            raise WorkerConfigurationError(
                f"Google identity authentication failed ({exc.code}): {detail}"
            ) from exc
        self._token = str(payload["access_token"])
        self._token_expires = time.time() + int(payload.get("expires_in", 3600))
        return self._token

    def request(
        self,
        method: str,
        path: str,
        *,
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
        upload: bool = False,
    ) -> Any:
        request_headers = {"Authorization": f"Bearer {self.token()}"}
        request_headers.update(headers or {})
        root = GOOGLE_UPLOAD_ROOT if upload else GOOGLE_DRIVE_ROOT
        request = urllib.request.Request(
            f"{root}{path}", data=data, headers=request_headers, method=method
        )
        try:
            with urllib.request.urlopen(request, timeout=600) as response:
                payload = response.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            raise RuntimeError(f"Google Drive returned {exc.code}: {detail}") from exc
        if not payload:
            return None
        content_type = response.headers.get_content_type()
        if content_type == "application/json" or payload[:1] in {b"{", b"["}:
            return json.loads(payload)
        return payload

    def get_json(self, path: str) -> dict[str, Any]:
        item = self._resolve_file(path)
        payload = self.request("GET", f"/files/{urllib.parse.quote(item['id'])}?alt=media")
        if not isinstance(payload, dict):
            raise RuntimeError("Cloud job metadata is not valid JSON")
        return payload

    def list_files(self, folder: str) -> list[dict[str, Any]]:
        parent_id = self._resolve_folder(folder)
        items: list[dict[str, Any]] = []
        page_token = ""
        while True:
            query = {
                "q": f"'{google_query_value(parent_id)}' in parents and trashed = false",
                "fields": "nextPageToken,files(id,name,size,mimeType)",
                "pageSize": "1000",
            }
            if page_token:
                query["pageToken"] = page_token
            page = self.request("GET", f"/files?{urllib.parse.urlencode(query)}")
            items.extend(
                item
                for item in page.get("files", [])
                if item.get("mimeType") != self.folder_mime_type
            )
            page_token = page.get("nextPageToken", "")
            if not page_token:
                return items

    def download_folder(
        self, source: str, destination: Path, expected_names: list[str] | None = None
    ) -> int:
        destination.mkdir(parents=True, exist_ok=True)
        items = self.list_files(source)
        items = select_expected_files(items, expected_names)
        if not items:
            raise RuntimeError("The Google Drive input folder contains no source images")
        for index, item in enumerate(items, start=1):
            name = safe_file_name(item["name"])
            print(f"Downloading source image {index}/{len(items)}: {name}", flush=True)
            request = urllib.request.Request(
                f"{GOOGLE_DRIVE_ROOT}/files/{urllib.parse.quote(item['id'])}?alt=media",
                headers={"Authorization": f"Bearer {self.token()}"},
            )
            try:
                with urllib.request.urlopen(request, timeout=600) as response:
                    with (destination / name).open("wb") as output:
                        shutil.copyfileobj(response, output, length=8 * 1024 * 1024)
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")[:500]
                raise RuntimeError(
                    f"Google Drive download failed for {name} ({exc.code}): {detail}"
                ) from exc
        return len(items)

    def put_bytes(self, path: str, payload: bytes, content_type: str) -> None:
        parts = path.split("/")
        name = parts.pop()
        parent_id = self._resolve_folder("/".join(parts))
        item = self._find_child(parent_id, name)
        if item is None:
            item = self.request(
                "POST",
                "/files?fields=id,name",
                data=json.dumps({"name": name, "parents": [parent_id]}).encode(),
                headers={"Content-Type": "application/json; charset=UTF-8"},
            )
        self.request(
            "PATCH",
            f"/files/{urllib.parse.quote(item['id'])}?uploadType=media",
            data=payload,
            headers={"Content-Type": content_type},
            upload=True,
        )

    def put_file(self, path: str, source: Path) -> None:
        content_type = mimetypes.guess_type(source.name)[0] or "application/octet-stream"
        size = source.stat().st_size
        upload_url = self._create_upload_session(path, size, content_type)
        self._upload_stream(upload_url, source, size, content_type)

    def _create_upload_session(self, path: str, size: int, content_type: str) -> str:
        parts = path.split("/")
        name = parts.pop()
        parent_id = self._resolve_folder("/".join(parts))
        item = self._find_child(parent_id, name)
        if item:
            endpoint = (
                f"{GOOGLE_UPLOAD_ROOT}/files/{urllib.parse.quote(item['id'])}?uploadType=resumable"
            )
            method = "PATCH"
            metadata: dict[str, Any] = {}
        else:
            endpoint = f"{GOOGLE_UPLOAD_ROOT}/files?uploadType=resumable&fields=id"
            method = "POST"
            metadata = {"name": name, "parents": [parent_id]}
        request = urllib.request.Request(
            endpoint,
            data=json.dumps(metadata).encode(),
            headers={
                "Authorization": f"Bearer {self.token()}",
                "Content-Type": "application/json; charset=UTF-8",
                "X-Upload-Content-Length": str(size),
                "X-Upload-Content-Type": content_type,
            },
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=300) as response:
                upload_url = response.headers.get("Location")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            raise RuntimeError(
                f"Could not create Google Drive upload session ({exc.code}): {detail}"
            ) from exc
        if not upload_url:
            raise RuntimeError("Google Drive did not return an upload URL")
        return upload_url

    @staticmethod
    def _upload_stream(upload_url: str, source: Path, size: int, content_type: str) -> None:
        # Google requires non-final chunks to be a multiple of 256 KiB.
        chunk_size = 10 * 1024 * 1024
        with source.open("rb") as stream:
            start = 0
            while start < size:
                chunk = stream.read(chunk_size)
                end = start + len(chunk) - 1
                request = urllib.request.Request(
                    upload_url,
                    data=chunk,
                    headers={
                        "Content-Length": str(len(chunk)),
                        "Content-Range": f"bytes {start}-{end}/{size}",
                        "Content-Type": content_type,
                    },
                    method="PUT",
                )
                try:
                    with urllib.request.urlopen(request, timeout=900):
                        pass
                except urllib.error.HTTPError as exc:
                    if exc.code != 308:
                        detail = exc.read().decode("utf-8", errors="replace")[:500]
                        raise RuntimeError(
                            f"Google Drive upload failed at byte {start} ({exc.code}): {detail}"
                        ) from exc
                start = end + 1

    def _resolve_folder(self, path: str) -> str:
        parent_id = self.root_folder_id
        for name in filter(None, path.split("/")):
            item = self._find_child(parent_id, name, self.folder_mime_type)
            if item is None:
                raise RuntimeError(f"Google Drive folder not found: {name}")
            parent_id = item["id"]
        return parent_id

    def _resolve_file(self, path: str) -> dict[str, Any]:
        parts = path.split("/")
        name = parts.pop()
        parent_id = self._resolve_folder("/".join(parts))
        item = self._find_child(parent_id, name)
        if item is None:
            raise RuntimeError(f"Google Drive file not found: {name}")
        return item

    def _find_child(
        self, parent_id: str, name: str, mime_type: str | None = None
    ) -> dict[str, Any] | None:
        clauses = [
            f"'{google_query_value(parent_id)}' in parents",
            f"name = '{google_query_value(name)}'",
            "trashed = false",
        ]
        if mime_type:
            clauses.append(f"mimeType = '{google_query_value(mime_type)}'")
        query = urllib.parse.urlencode(
            {
                "q": " and ".join(clauses),
                "fields": "files(id,name,size,mimeType)",
                "pageSize": "2",
            }
        )
        result = self.request("GET", f"/files?{query}")
        items = result.get("files", [])
        if len(items) > 1:
            raise RuntimeError(f"Google Drive contains duplicate items named {name}")
        return items[0] if items else None


def create_storage_client() -> GraphClient | GoogleDriveClient:
    provider = os.environ.get("STORAGE_PROVIDER", "sharepoint").strip().lower()
    if provider in {"google", "google-drive"}:
        return GoogleDriveClient()
    if provider == "sharepoint":
        return GraphClient()
    raise WorkerConfigurationError(f"Unsupported STORAGE_PROVIDER: {provider}")


def validate_version_contract(
    payload: dict[str, Any], installed_openreef_version: str
) -> dict[str, str]:
    expected_openreef = required_env("OPENREEF_VERSION")
    expected_gpu = required_env("OPENREEF_GPU_VERSION")
    requested_openreef = str(payload.get("openreef_version", ""))
    requested_gpu = str(payload.get("openreef_gpu_version", ""))
    if installed_openreef_version != expected_openreef:
        raise WorkerConfigurationError(
            "RunPod image contains OpenReef "
            f"{installed_openreef_version}; expected {expected_openreef}"
        )
    if requested_openreef != expected_openreef or requested_gpu != expected_gpu:
        raise ValueError(
            "Job version does not match the RunPod image "
            f"(job OpenReef {requested_openreef or 'missing'}, "
            f"openreefGPU {requested_gpu or 'missing'}; image expects "
            f"OpenReef {expected_openreef}, openreefGPU {expected_gpu})"
        )
    return {"openreef": expected_openreef, "openreefGPU": expected_gpu}


def handler(event: dict[str, Any]) -> dict[str, Any]:
    """Process one Runpod event and persist durable state in cloud storage."""
    payload = event.get("input") or {}
    job_id = str(payload.get("job_id", ""))
    if not JOB_ID_PATTERN.fullmatch(job_id):
        raise ValueError("input.job_id must be a UUID")
    if payload.get("schema_version") != JOB_SCHEMA_VERSION:
        raise ValueError("Unsupported job schema version")
    try:
        import openreef
    except ImportError as exc:  # pragma: no cover - container configuration
        raise WorkerConfigurationError("The pinned OpenReef package is not installed") from exc
    versions = validate_version_contract(payload, openreef.__version__)

    storage = create_storage_client()
    job_root = storage.job_path(job_id)
    job = read_job(storage, job_root)
    job["state"] = "running"
    update_job(storage, job_root, job, stage="Downloading source images", progress=3)

    try:
        with tempfile.TemporaryDirectory(prefix=f"openreef-{job_id[:8]}-") as temporary:
            dataset = Path(temporary) / safe_dataset_name(str(job.get("name", "reef-survey")))
            source_folder = job_source_folder(job, job_root)
            expected_names = [
                safe_file_name(str(item.get("name", "")))
                for item in job.get("files", [])
                if isinstance(item, dict)
            ]
            image_count = storage.download_folder(source_folder, dataset / "images", expected_names)
            update_job(
                storage,
                job_root,
                job,
                stage=f"Preparing {image_count} source images",
                progress=5,
            )
            run_pipeline(
                dataset,
                lambda stage, progress: update_job(
                    storage, job_root, job, stage=stage, progress=progress
                ),
            )

            artifacts = select_result_artifacts(dataset)
            update_job(storage, job_root, job, stage="Uploading compact web model", progress=96)
            output_root = f"{job_root}/output"
            uploaded: list[dict[str, Any]] = []
            for role, source in artifacts.items():
                target_name = result_file_name(role, source)
                storage.put_file(f"{output_root}/{target_name}", source)
                uploaded.append(
                    {
                        "role": role,
                        "name": target_name,
                        "bytes": source.stat().st_size,
                        "contentType": mimetypes.guess_type(target_name)[0]
                        or "application/octet-stream",
                    }
                )
            manifest = {
                "schemaVersion": 1,
                "jobId": job_id,
                "dataset": job["name"],
                "createdAt": utc_now(),
                "versions": versions,
                "assets": uploaded,
            }
            storage.put_bytes(
                f"{output_root}/manifest.json",
                (json.dumps(manifest, indent=2) + "\n").encode(),
                "application/json",
            )
            model_name = next(item["name"] for item in uploaded if item["role"] == "model")
            job["state"] = "completed"
            job["result"] = {"model": model_name, "manifest": "manifest.json"}
            update_job(storage, job_root, job, stage="Ready to view", progress=100)
            return {
                "job_id": job_id,
                "state": "completed",
                "versions": versions,
                "result": job["result"],
            }
    except Exception as exc:
        job["state"] = "failed"
        job["error"] = str(exc)[:1_000]
        try:
            update_job(
                storage,
                job_root,
                job,
                stage="Processing failed",
                progress=job.get("progress", 0),
            )
        except Exception as update_error:  # pragma: no cover - last-resort logging
            print(f"Could not persist failure state: {update_error}", flush=True)
        raise


def run_pipeline(dataset: Path, progress: Callable[[str, int], None]) -> None:
    command = [
        "openreef-pipeline",
        str(dataset),
        "--dense-compact",
        "--no-dense-original",
        "--max-image-size",
        os.environ.get("OPENREEF_MAX_IMAGE_SIZE", "3200"),
        "--max-resolution",
        os.environ.get("OPENREEF_MAX_RESOLUTION", "2560"),
        "--max-texture-size",
        os.environ.get("OPENREEF_MAX_TEXTURE_SIZE", "4096"),
        "--texture-resolution-level",
        os.environ.get("OPENREEF_TEXTURE_RESOLUTION_LEVEL", "1"),
        "--cores",
        str(max(1, (os.cpu_count() or 2) - 1)),
    ]
    print("Starting OpenReef compact pipeline", flush=True)
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    assert process.stdout is not None
    last_stage = ""
    for line in process.stdout:
        print(line, end="", flush=True)
        stage = stage_from_output(line)
        if stage and stage != last_stage:
            last_stage = stage
            progress(stage, STAGE_PROGRESS[stage])
    return_code = process.wait()
    if return_code:
        raise RuntimeError(f"OpenReef pipeline exited with code {return_code}")


def stage_from_output(line: str) -> str | None:
    return next((stage for stage in STAGE_PROGRESS if stage in line), None)


def select_result_artifacts(dataset: Path) -> dict[str, Path]:
    compact = dataset / "openmvs" / "scene_mesh_compact_textured.glb"
    candidates = [compact, *sorted((dataset / "models").glob("*_textured_mesh_compact.glb"))]
    model = next((path for path in candidates if path.is_file()), None)
    if model is None:
        raise RuntimeError("Pipeline completed without a compact textured GLB")
    artifacts = {"model": model}
    surface = dataset / "openmvs" / "scene_mesh_compact.ply"
    if surface.is_file():
        artifacts["flow_surface"] = surface
    sparse_candidates = sorted((dataset / "models").glob("*_sparse_cloud_compact.ply"))
    if sparse_candidates:
        artifacts["sparse"] = sparse_candidates[0]
    return artifacts


def result_file_name(role: str, source: Path) -> str:
    names = {
        "model": "model.glb",
        "flow_surface": "flow-surface.ply",
        "sparse": "sparse.ply",
    }
    return names.get(role, safe_file_name(source.name))


def read_job(storage: GraphClient | GoogleDriveClient, job_root: str) -> dict[str, Any]:
    payload = storage.get_json(f"{job_root}/job.json")
    if not isinstance(payload, dict) or payload.get("schemaVersion") != 1:
        raise RuntimeError("Cloud job metadata is missing or incompatible")
    return payload


def update_job(
    storage: GraphClient | GoogleDriveClient,
    job_root: str,
    job: dict[str, Any],
    *,
    stage: str,
    progress: int,
) -> None:
    job["stage"] = stage
    job["progress"] = max(0, min(100, int(progress)))
    job["updatedAt"] = utc_now()
    storage.put_bytes(
        f"{job_root}/job.json",
        (json.dumps(job, indent=2) + "\n").encode(),
        "application/json",
    )


def validate_job_root(root: str, variable: str) -> None:
    if not root or ".." in root.split("/"):
        raise WorkerConfigurationError(f"{variable} is invalid")


def job_source_folder(job: dict[str, Any], job_root: str) -> str:
    source = job.get("source")
    if not isinstance(source, dict) or source.get("kind") != "storage-folder":
        return f"{job_root}/input"
    folder = str(source.get("folder", "")).strip("/")
    if not folder or any(part in {"", ".", ".."} for part in folder.split("/")):
        raise RuntimeError("Cloud job contains an invalid source folder")
    return folder


def select_expected_files(
    items: list[dict[str, Any]], expected_names: list[str] | None
) -> list[dict[str, Any]]:
    if expected_names is None:
        return items
    expected = {name.casefold() for name in expected_names}
    selected = [item for item in items if str(item.get("name", "")).casefold() in expected]
    found = {str(item.get("name", "")).casefold() for item in selected}
    missing = sorted(expected - found)
    if missing:
        raise RuntimeError(f"Cloud source folder is missing {len(missing)} expected file(s)")
    return selected


def required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise WorkerConfigurationError(f"{name} is required")
    return value


def safe_file_name(value: str) -> str:
    name = Path(value).name
    if name != value or not name or name in {".", ".."}:
        raise RuntimeError(f"Unsafe cloud file name: {value!r}")
    return name


def safe_dataset_name(value: str) -> str:
    result = re.sub(r"[^A-Za-z0-9._ -]+", "-", value).strip(" .-")
    result = re.sub(r"\s*-\s*", "-", result)
    return result[:80] or "reef-survey"


def encode_graph_path(path: str) -> str:
    return "/".join(urllib.parse.quote(part, safe="") for part in path.split("/"))


def google_query_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def main() -> None:
    try:
        import runpod
    except ImportError as exc:  # pragma: no cover - container-only dependency
        raise SystemExit("The runpod package is required to start the serverless worker") from exc
    runpod.serverless.start({"handler": handler})


if __name__ == "__main__":
    main()
