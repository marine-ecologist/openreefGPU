import io
import urllib.error
from pathlib import Path

import pytest

from worker.handler import (
    GoogleDriveClient,
    create_storage_client,
    google_query_value,
    job_source_folder,
    pipeline_core_count,
    result_file_name,
    safe_dataset_name,
    safe_file_name,
    select_expected_files,
    select_result_artifacts,
    source_cache_folder,
    stage_from_output,
    validate_version_contract,
)


def test_stage_from_pipeline_output() -> None:
    assert stage_from_output("[3/8] Sparse reconstruction") == "Sparse reconstruction"
    assert stage_from_output("ordinary tool output") is None


def test_pipeline_threads_are_capped_for_large_cloud_hosts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("worker.handler.os.cpu_count", lambda: 128)
    monkeypatch.setenv("OPENREEF_MAX_CORES", "32")

    assert pipeline_core_count() == 32


def test_pipeline_thread_cap_rejects_invalid_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENREEF_MAX_CORES", "many")

    with pytest.raises(RuntimeError, match="must be an integer"):
        pipeline_core_count()


def test_result_selection_prefers_compact_textured_glb(tmp_path: Path) -> None:
    output = tmp_path / "openmvs" / "scene_mesh_compact_textured.glb"
    output.parent.mkdir()
    output.write_bytes(b"glb")
    surface = output.with_name("scene_mesh_compact.ply")
    surface.write_bytes(b"ply")

    artifacts = select_result_artifacts(tmp_path)

    assert artifacts == {"model": output, "flow_surface": surface}
    assert result_file_name("model", output) == "model.glb"


def test_cloud_names_are_safe() -> None:
    assert safe_dataset_name("Heron / Reef: 4") == "Heron-Reef-4"
    assert safe_file_name("DSC_0001.JPG") == "DSC_0001.JPG"
    with pytest.raises(RuntimeError, match="Unsafe"):
        safe_file_name("../secret.jpg")


def test_existing_drive_folder_is_selected_without_moving_images() -> None:
    job = {"source": {"kind": "storage-folder", "folder": "images"}}
    assert job_source_folder(job, "jobs/job-id") == "images"
    assert job_source_folder({}, "jobs/job-id") == "jobs/job-id/input"

    items = [{"name": "IMG_1.JPG"}, {"name": "IMG_2.JPG"}]
    assert select_expected_files(items, ["img_2.jpg"]) == [items[1]]
    with pytest.raises(RuntimeError, match="missing 1"):
        select_expected_files(items, ["IMG_3.JPG"])


def test_google_drive_provider_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STORAGE_PROVIDER", "google-drive")
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "client-id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("GOOGLE_REFRESH_TOKEN", "refresh-token")
    monkeypatch.setenv("GOOGLE_DRIVE_ROOT_FOLDER_ID", "folder-id")
    monkeypatch.setenv("GOOGLE_DRIVE_JOB_ROOT", "jobs")

    storage = create_storage_client()

    assert isinstance(storage, GoogleDriveClient)
    assert storage.job_path("job-id") == "jobs/job-id"
    assert google_query_value("O'Reef\\2026") == "O\\'Reef\\\\2026"


def test_google_drive_download_retries_transient_server_errors(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    client = object.__new__(GoogleDriveClient)
    monkeypatch.setattr(client, "token", lambda: "access-token")
    calls = 0

    def open_request(*_args: object, **_kwargs: object) -> io.BytesIO:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise urllib.error.HTTPError(
                "https://drive.example/file",
                502,
                "Bad Gateway",
                {},
                io.BytesIO(b"temporary failure"),
            )
        return io.BytesIO(b"image bytes")

    monkeypatch.setattr("worker.handler.urllib.request.urlopen", open_request)
    monkeypatch.setattr("worker.handler.time.sleep", lambda _delay: None)
    target = tmp_path / "IMG_0001.JPG"

    client._download_file({"id": "file-id", "name": target.name}, target)

    assert calls == 2
    assert target.read_bytes() == b"image bytes"
    assert not (tmp_path / ".IMG_0001.JPG.part").exists()


def test_google_drive_request_retries_transient_server_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Headers:
        @staticmethod
        def get_content_type() -> str:
            return "application/json"

    class Response(io.BytesIO):
        headers = Headers()

    client = object.__new__(GoogleDriveClient)
    monkeypatch.setattr(client, "token", lambda: "access-token")
    calls = 0

    def open_request(*_args: object, **_kwargs: object) -> io.BytesIO:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise urllib.error.HTTPError(
                "https://drive.example/file",
                502,
                "Bad Gateway",
                {},
                io.BytesIO(b"temporary failure"),
            )
        return Response(b'{"ok": true}')

    monkeypatch.setattr("worker.handler.urllib.request.urlopen", open_request)
    monkeypatch.setattr("worker.handler.time.sleep", lambda _delay: None)

    assert client.request("PATCH", "/files/file-id", data=b"state") == {"ok": True}
    assert calls == 2


def test_google_drive_upload_resumes_after_transient_server_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class Response(io.BytesIO):
        headers: dict[str, str] = {}

    source = tmp_path / "model.glb"
    source.write_bytes(b"completed-model")
    requests: list[urllib.request.Request] = []

    def open_request(request: urllib.request.Request, **_kwargs: object) -> io.BytesIO:
        requests.append(request)
        if len(requests) == 1:
            raise urllib.error.HTTPError(
                request.full_url,
                502,
                "Bad Gateway",
                {},
                io.BytesIO(b"temporary failure"),
            )
        if len(requests) == 2:
            raise urllib.error.HTTPError(
                request.full_url,
                308,
                "Resume Incomplete",
                {"Range": "bytes=0-3"},
                io.BytesIO(),
            )
        return Response()

    monkeypatch.setattr("worker.handler.urllib.request.urlopen", open_request)
    monkeypatch.setattr("worker.handler.time.sleep", lambda _delay: None)

    GoogleDriveClient._upload_stream(
        "https://drive.example/upload", source, source.stat().st_size, "model/gltf-binary"
    )

    assert len(requests) == 3
    assert requests[1].get_header("Content-range") == f"bytes */{source.stat().st_size}"
    assert requests[2].get_header("Content-range") == (
        f"bytes 4-{source.stat().st_size - 1}/{source.stat().st_size}"
    )
    assert requests[2].data == b"leted-model"


def test_google_drive_source_cache_reuses_and_populates_files(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cache_root = tmp_path / "cache"
    destination = tmp_path / "dataset" / "images"
    monkeypatch.setenv("OPENREEF_SOURCE_CACHE_DIR", str(cache_root))
    client = object.__new__(GoogleDriveClient)
    items = [
        {"id": "cached-id", "name": "IMG_0001.JPG", "size": "6"},
        {"id": "new-id", "name": "IMG_0002.JPG", "size": "8"},
    ]
    monkeypatch.setattr(client, "list_files", lambda _source: items)
    cache_folder = source_cache_folder("reefplot/images")
    assert cache_folder is not None
    cache_folder.mkdir(parents=True)
    (cache_folder / "IMG_0001.JPG").write_bytes(b"cached")

    def download(item: dict[str, object], target: Path) -> None:
        assert item["id"] == "new-id"
        target.write_bytes(b"new-data")

    monkeypatch.setattr(client, "_download_file", download)
    progress: list[tuple[int, int]] = []

    count = client.download_folder(
        "reefplot/images",
        destination,
        on_progress=lambda completed, _total, _bytes, _bytes_total, _name, hits: (
            progress.append((completed, hits))
        ),
    )

    assert count == 2
    assert progress == [(1, 1), (2, 1)]
    assert (destination / "IMG_0001.JPG").is_symlink()
    assert (destination / "IMG_0002.JPG").is_symlink()
    assert (destination / "IMG_0001.JPG").read_bytes() == b"cached"
    assert (destination / "IMG_0002.JPG").read_bytes() == b"new-data"
    assert (cache_folder / "IMG_0002.JPG").read_bytes() == b"new-data"


def test_worker_rejects_a_mismatched_openreef_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENREEF_VERSION", "0.6.3")
    monkeypatch.setenv("OPENREEF_GPU_VERSION", "0.6.3-gpu.14")
    payload = {
        "openreef_version": "0.6.4",
        "openreef_gpu_version": "0.6.3-gpu.14",
    }

    with pytest.raises(ValueError, match="does not match"):
        validate_version_contract(payload, "0.6.3")


def test_worker_accepts_the_pinned_version(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENREEF_VERSION", "0.6.3")
    monkeypatch.setenv("OPENREEF_GPU_VERSION", "0.6.3-gpu.14")
    payload = {
        "openreef_version": "0.6.3",
        "openreef_gpu_version": "0.6.3-gpu.14",
    }

    assert validate_version_contract(payload, "0.6.3") == {
        "openreef": "0.6.3",
        "openreefGPU": "0.6.3-gpu.14",
    }
