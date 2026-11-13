"""Portable checks for the Mac setup launcher's credential and SSH handling."""

import importlib.util
import io
import json
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    "openreef_setup", Path(__file__).resolve().parents[1] / "setup/setup.py"
)
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)


class SetupTests(unittest.TestCase):
    def test_ssh_options_are_normalized_without_shell_evaluation(self):
        result = setup.ssh_connection("ssh root@host.example -p 1234 -i ~/.ssh/runpod_key")
        self.assertEqual(result[:3], ["ssh", "-p", "1234"])
        self.assertEqual(result[-1], "root@host.example")
        self.assertTrue(result[-2].endswith("/.ssh/runpod_key"))
        for command in ("ssh host ls", "ssh host;touch /tmp/unsafe", "ssh -p", ""):
            with self.assertRaises(RuntimeError):
                setup.ssh_connection(command)

    def test_env_roundtrip_retains_quotes_and_does_not_execute_values(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "drive.env"
            values = {
                "GOOGLE_REFRESH_TOKEN": 'quotes " and $(touch /tmp/unsafe)',
                "GOOGLE_CLIENT_ID": "example",
            }
            setup.write_env(path, values)
            self.assertEqual(setup.read_env(path), values)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_diagnostic_does_not_reinstall_web_dependencies(self):
        source = Path(__file__).resolve().parents[1]
        fake_drive = {key: "test-value" for key in setup.drive_keys()}
        with (
            patch.object(setup, "diagnostic_source", return_value=source),
            patch.object(setup, "drive_settings", return_value=fake_drive),
            patch.object(setup, "require_install", side_effect=AssertionError("reinstall")),
            patch.object(setup, "run_pty_session") as execute,
        ):
            setup.diagnose("ssh worker@example.org -i ~/.ssh/id_ed25519")
        self.assertEqual(execute.call_count, 1)
        command, payload, remote = execute.call_args.args
        self.assertEqual(command[:2], ["ssh", "-tt"])
        self.assertEqual(command[-1], "worker@example.org")
        self.assertTrue(remote.startswith("/tmp/openreef-diagnostic-"))
        with tarfile.open(fileobj=io.BytesIO(payload)) as archive:
            self.assertIn("scripts/diagnose-runpod.py", archive.getnames())
            self.assertIn("drive.json", archive.getnames())

    def test_collection_downloads_checked_report_without_drive_credentials(self):
        import base64
        import gzip
        import hashlib

        report = {"runs": {"sparse-8": {"seconds": 459.423}}}
        data = gzip.compress(json.dumps(report).encode())

        def transport(command, payload, remote, **kwargs):
            with tarfile.open(fileobj=io.BytesIO(payload)) as archive:
                self.assertEqual(archive.extractfile("drive.json").read(), b"{}")
                code = archive.extractfile("scripts/diagnose-runpod.py").read().decode()
                self.assertIn("summarize", code)
                self.assertNotIn("openreef-pipeline", code)
            receive = kwargs["on_line"]
            receive("OPENREEF_REPORT:" + base64.b64encode(data).decode())
            receive("OPENREEF_REPORT_SHA256:" + hashlib.sha256(data).hexdigest())
            evidence = gzip.compress(b"test-only evidence")
            receive("OPENREEF_EVIDENCE:" + base64.b64encode(evidence).decode())
            receive("OPENREEF_EVIDENCE_SHA256:" + hashlib.sha256(evidence).hexdigest())

        with (
            tempfile.TemporaryDirectory() as temporary,
            patch.object(setup, "BASE", Path(temporary)),
            patch.object(setup, "run_pty_session", side_effect=transport),
            patch.object(setup, "drive_settings", side_effect=AssertionError("credentials")),
        ):
            setup.collect_results("ssh worker@example.org", "/tmp/diagnostic/results")
            paths = list((Path(temporary) / "diagnostic-reports").glob("*.json"))
            self.assertEqual(len(paths), 1)
            self.assertEqual(json.loads(paths[0].read_text()), report)

    def test_new_bundle_is_preferred_over_cached_installed_source(self):
        names = (
            "worker/handler.py",
            "scripts/diagnose-runpod.py",
            "versions.json",
            "scripts/analyze-diagnostic.py",
            "scripts/profile-sparse.py",
            "benchmarks/reefplot-248.sha256",
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundle = root / "bundle"
            bundle.mkdir()
            installed = root / "installed"
            for name in names:
                path = installed / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("old")
            with tarfile.open(bundle / "source.tar.gz", "w:gz") as archive:
                for name in names:
                    item = tarfile.TarInfo(name)
                    item.size = 3
                    archive.addfile(item, io.BytesIO(b"new"))
            with (
                patch.object(setup, "HERE", bundle),
                patch.object(setup, "APP", installed),
                patch.object(setup, "BASE", root),
            ):
                source = setup.diagnostic_source()
            self.assertNotEqual(source, installed)
            self.assertEqual((source / "scripts/profile-sparse.py").read_text(), "new")

    def test_drive_collection_needs_no_worker_and_checks_integrity(self):
        import hashlib

        payload = io.BytesIO()
        with tarfile.open(fileobj=payload, mode="w:gz") as archive:
            content = b'{"cpu": "fixture"}'
            item = tarfile.TarInfo("hardware.json")
            item.size = len(content)
            archive.addfile(item, io.BytesIO(content))
        data = payload.getvalue()

        class Storage:
            def _resolve_file(self, path):
                return {"id": "report"}

            def request(self, method, path):
                return {
                    "id": "report",
                    "name": "reports.tar.gz",
                    "size": len(data),
                    "md5Checksum": hashlib.md5(data).hexdigest(),
                }

            def _download_file(self, item, destination):
                destination.write_bytes(data)

        with (
            tempfile.TemporaryDirectory() as temporary,
            patch.object(setup, "BASE", Path(temporary)),
            patch.object(setup, "diagnostic_drive_client", return_value=Storage()),
            patch.object(setup, "run_pty_session", side_effect=AssertionError("SSH")),
        ):
            folder = setup.collect_drive("diagnostics/sparse-profile-" + "a" * 32)
            self.assertTrue((folder / "raw/hardware.json").exists())
            self.assertTrue((folder / "summary.json").exists())

    def test_real_pty_upload_and_proxy_failure_guard(self):
        probe = Path(__file__).resolve().parent / "fixtures/pty_diagnostic_probe.py"
        result = subprocess.run(
            [sys.executable, str(probe), str(setup.__file__)],
            capture_output=True,
            text=True,
            timeout=25,
            check=True,
        )
        self.assertIn("PTY_TRANSPORT_PASSED", result.stdout)
        self.assertIn("FALSE_SUCCESS_GUARD_PASSED", result.stdout)
        self.assertNotIn("test-not-a-real-secret", result.stdout)

    def test_unpack_rejects_path_traversal(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "source.tar.gz"
            with tarfile.open(path, "w:gz") as archive:
                content = b"bad"
                item = tarfile.TarInfo("../outside")
                item.size = len(content)
                archive.addfile(item, io.BytesIO(content))
            with self.assertRaises(RuntimeError):
                setup.unpack(path, root / "install")
            self.assertFalse((root / "outside").exists())


if __name__ == "__main__":
    unittest.main()
