"""Verify temporary endpoint lifecycle without calling the RunPod API."""

import importlib.util
import json
from pathlib import Path


def test_temporary_endpoint_keeps_reference_and_cleans_up(tmp_path, monkeypatch, capsys):
    script = Path(__file__).resolve().parents[1] / "scripts/submit-paired-check.py"
    spec = importlib.util.spec_from_file_location("submit_paired_check", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    calls = []

    class Response:
        def __init__(self, value):
            self.value = value

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps(self.value).encode() if self.value is not None else b""

    def request(req, timeout):
        body = json.loads(req.data) if req.data else None
        calls.append((req.get_method(), req.full_url, body))
        if req.get_method() == "GET" and req.full_url.endswith("/93jkogjuc9l6pu"):
            return Response(
                {
                    "gpu": {
                        "pools": ["AMPERE_48"],
                        "excludedTypes": ["example-exclusion"],
                        "count": 2,
                    }
                }
            )
        if req.get_method() == "POST" and req.full_url.endswith("/serverless"):
            assert body["workers"] == {"min": 0, "max": 1, "idleTimeout": 5}
            assert body["disk"] == 500
            assert body["gpu"]["count"] == 1
            assert body["gpu"]["excludedTypes"] == ["example-exclusion"]
            assert body["mounts"]["network"] == [{"volumeId": "s8baqr2m12"}]
            return Response({"id": "temporary123"})
        if req.get_method() == "POST" and req.full_url.endswith("/temporary123/run"):
            assert body["input"]["paired_native_check"]["source"] == "paired-native-check/example"
            return Response({"id": "job123", "status": "IN_QUEUE"})
        if req.get_method() == "GET" and req.full_url.endswith("/temporary123/status/job123"):
            return Response(
                {
                    "id": "job123",
                    "status": "COMPLETED",
                    "output": {"run": "/runpod-volume/openreef-runs/test"},
                }
            )
        if req.get_method() == "DELETE" and req.full_url.endswith("/temporary123"):
            return Response(None)
        raise AssertionError((req.get_method(), req.full_url))

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RUNPOD_API_KEY", "test-secret-never-print")
    monkeypatch.setattr(
        "sys.argv", [str(script), "--source", "paired-native-check/example", "--wait"]
    )
    monkeypatch.setattr(module.urllib.request, "urlopen", request)
    assert module.main() == 0
    assert all(method == "GET" for method, url, _ in calls if url.endswith("/93jkogjuc9l6pu"))
    assert calls[-1][0] == "DELETE"
    assert (
        json.loads((tmp_path / "paired-native-job123.json").read_text())["result"]["status"]
        == "COMPLETED"
    )
    assert "test-secret-never-print" not in capsys.readouterr().out


def test_denied_configuration_reports_stage_without_key(tmp_path, monkeypatch):
    import urllib.error

    import pytest

    script = Path(__file__).resolve().parents[1] / "scripts/submit-paired-check.py"
    spec = importlib.util.spec_from_file_location("submit_paired_denied", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    calls = []

    def denied(req, timeout):
        calls.append(req.get_method())
        raise urllib.error.HTTPError(req.full_url, 403, "Forbidden secret-body", {}, None)

    monkeypatch.setenv("RUNPOD_API_KEY", "secret-test-key")
    monkeypatch.setattr("sys.argv", [str(script), "--source", "example"])
    monkeypatch.setattr(module.urllib.request, "urlopen", denied)
    with pytest.raises(module.RunPodRequestError) as error:
        module.main()
    assert "reading reference endpoint configuration (HTTP 403)" in str(error.value)
    assert "secret-test-key" not in str(error.value)
    assert "secret-body" not in str(error.value)
    assert calls == ["GET"]


def test_diagnostic_is_read_only_and_redacts_server_detail(monkeypatch, capsys):
    import io
    import urllib.error

    script = Path(__file__).resolve().parents[1] / "scripts/submit-paired-check.py"
    spec = importlib.util.spec_from_file_location("submit_paired_diagnostic", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    calls = []

    def rejected(req, timeout):
        calls.append(req.get_method())
        assert req.get_header("User-agent").startswith("Mozilla/5.0")
        assert req.get_header("Authorization") == "Bearer test-secret"
        raw = json.dumps(
            {"title": "Forbidden", "detail": "test-secret rpa_other-secret scope missing"}
        ).encode()
        raise urllib.error.HTTPError(req.full_url, 403, "Forbidden", {}, io.BytesIO(raw))

    monkeypatch.setenv("RUNPOD_API_KEY", "  test-secret  ")
    monkeypatch.setattr("sys.argv", [str(script), "--diagnose"])
    monkeypatch.setattr(module.urllib.request, "urlopen", rejected)
    assert module.main() == 1
    assert calls == ["GET", "GET", "GET"]
    output = capsys.readouterr().out
    assert "scope missing" in output
    assert "test-secret" not in output
    assert "rpa_other-secret" not in output
