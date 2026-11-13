"""Exercise the real terminal transport with fake data and a local shell."""

import importlib.util
import io
import json
import os
import sys
import tarfile
import tempfile
from pathlib import Path

spec = importlib.util.spec_from_file_location("setup", sys.argv[1])
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)
with tempfile.TemporaryDirectory(prefix="openreef-pty-test-") as temporary:
    root = Path(temporary)
    buffer = io.BytesIO()
    fake_program = (
        b"import os\n"
        b'assert os.environ["TEST_ONLY_TOKEN"] == "test-not-a-real-secret"\n'
        b'print("FAKE_DIAGNOSTIC_PASSED",flush=True)\n'
    )
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        files = [
            ("drive.json", json.dumps({"TEST_ONLY_TOKEN": "test-not-a-real-secret"}).encode()),
            ("scripts/diagnose-runpod.py", fake_program),
            ("padding.bin", os.urandom(100000)),
        ]
        for name, data in files:
            item = tarfile.TarInfo(name)
            item.size = len(data)
            item.mode = 0o600
            archive.addfile(item, io.BytesIO(data))
    result = setup.run_pty_session(
        ["/bin/bash", "--noprofile", "--norc", "-i"],
        buffer.getvalue(),
        str(root / "remote"),
        timeout=12,
    )
    assert result == 0
    assert not (root / "remote/drive.json").exists()
    print("PTY_TRANSPORT_PASSED")
try:
    setup.run_pty_session(
        [sys.executable, "-c", 'print("Error: Your SSH client does not support PTY")'],
        b"",
        "/tmp/unused-diagnostic-test",
        timeout=5,
    )
except RuntimeError as error:
    assert "did not confirm" in str(error)
    print("FALSE_SUCCESS_GUARD_PASSED")
else:
    raise AssertionError("Transport incorrectly reported success")
