"""Package the reviewed core stereo changes for the pinned GPU worker source."""

import argparse
import json
import subprocess
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("core", type=Path)
args = parser.parse_args()
core = args.core.resolve()
root = Path(__file__).resolve().parents[1]
paths = [
    "pyproject.toml",
    "src/openreef/pipeline/cli.py",
    "src/openreef/pipeline/stages.py",
    "src/openreef/pipeline/tasks.py",
    "src/openreef/pipeline/stereo.py",
]
expected = json.loads((root / "versions.json").read_text())["openreef"]["commit"]
actual = subprocess.check_output(["git", "-C", str(core), "rev-parse", "HEAD"], text=True).strip()
if actual != expected:
    raise SystemExit("Core checkout must be based on the worker-pinned commit")
parts = []
for path in paths:
    tracked = (
        subprocess.run(
            ["git", "-C", str(core), "ls-files", "--error-unmatch", path], capture_output=True
        ).returncode
        == 0
    )
    command = (
        ["git", "diff", "--binary", "HEAD", "--", path]
        if tracked
        else ["git", "diff", "--no-index", "--binary", "--", "/dev/null", path]
    )
    result = subprocess.run(command, cwd=core, capture_output=True)
    if result.returncode not in (0, 1):
        raise RuntimeError(result.stderr.decode())
    parts.append(result.stdout)
output = root / "worker/patches/openreef-stereo.patch"
output.write_bytes(b"".join(parts))
print(output)
