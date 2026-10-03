from pathlib import Path

import pytest

from worker.handler import (
    GoogleDriveClient,
    create_storage_client,
    google_query_value,
    result_file_name,
    safe_dataset_name,
    safe_file_name,
    select_result_artifacts,
    stage_from_output,
    validate_version_contract,
)


def test_stage_from_pipeline_output() -> None:
    assert stage_from_output("[3/8] Sparse reconstruction") == "Sparse reconstruction"
    assert stage_from_output("ordinary tool output") is None


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


def test_worker_rejects_a_mismatched_openreef_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENREEF_VERSION", "0.6.3")
    monkeypatch.setenv("OPENREEF_GPU_VERSION", "0.6.3-gpu.1")
    payload = {
        "openreef_version": "0.6.4",
        "openreef_gpu_version": "0.6.3-gpu.1",
    }

    with pytest.raises(ValueError, match="does not match"):
        validate_version_contract(payload, "0.6.3")


def test_worker_accepts_the_pinned_version(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENREEF_VERSION", "0.6.3")
    monkeypatch.setenv("OPENREEF_GPU_VERSION", "0.6.3-gpu.1")
    payload = {
        "openreef_version": "0.6.3",
        "openreef_gpu_version": "0.6.3-gpu.1",
    }

    assert validate_version_contract(payload, "0.6.3") == {
        "openreef": "0.6.3",
        "openreefGPU": "0.6.3-gpu.1",
    }
