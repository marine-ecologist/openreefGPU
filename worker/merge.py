"""Conservative component merging through shared, geometrically verified images."""

from __future__ import annotations

import json
import struct
import subprocess
from pathlib import Path


def image_names(model: Path) -> set[str]:
    """Read names without loading hundreds of megabytes of 2D observations."""
    binary = model / "images.bin"
    size = binary.stat().st_size
    names = set()
    with binary.open("rb") as stream:
        (count,) = struct.unpack("<Q", stream.read(8))
        for _ in range(count):
            if stream.tell() + 64 > size:
                raise ValueError("Truncated image record")
            stream.seek(64, 1)
            name = bytearray()
            while True:
                char = stream.read(1)
                if not char:
                    raise ValueError("Truncated image name")
                if char == b"\0":
                    break
                name.extend(char)
            (observations,) = struct.unpack("<Q", stream.read(8))
            if stream.tell() + observations * 24 > size:
                raise ValueError("Truncated image observations")
            stream.seek(observations * 24, 1)
            names.add(name.decode("utf-8"))
        if stream.tell() != size or len(names) != count:
            raise ValueError("Invalid image model length or duplicate names")
    return names


def merge_components(models: list[Path], attempts: Path) -> tuple[Path, dict]:
    """Never force an alignment: require shared images and COLMAP's geometric check."""
    attempts.mkdir()
    names = {model: image_names(model) for model in models}
    original_largest = max(len(value) for value in names.values())
    active = list(models)
    report = {
        "acceptedMerges": 0,
        "attempts": [],
        "uniqueSourceImages": len(set.union(*names.values())),
    }
    failed = set()
    while True:
        options = [
            (a, b)
            for i, a in enumerate(active)
            for b in active[i + 1 :]
            if len(names[a] & names[b]) >= 3
            and names[a] != names[b]
            and (str(a), str(b)) not in failed
        ]
        if not options:
            break
        first, second = max(
            options,
            key=lambda pair: (
                len(names[pair[0]] & names[pair[1]]),
                len(names[pair[0]] | names[pair[1]]),
            ),
        )
        output = attempts / str(len(report["attempts"]))
        output.mkdir()
        command = [
            "colmap",
            "model_merger",
            "--input_path1",
            str(first),
            "--input_path2",
            str(second),
            "--output_path",
            str(output),
            "--max_reproj_error",
            "8",
        ]
        print("Command: " + json.dumps(command), flush=True)
        result = subprocess.run(command, check=False)
        expected = names[first] | names[second]
        valid = result.returncode == 0 and (output / "images.bin").is_file()
        if valid:
            valid = image_names(output) == expected
        report["attempts"].append(
            {
                "first": str(first),
                "second": str(second),
                "sharedImages": len(names[first] & names[second]),
                "accepted": valid,
                "exitCode": result.returncode,
            }
        )
        if valid:
            names[output] = expected
            active.remove(first)
            active.remove(second)
            active.append(output)
            report["acceptedMerges"] += 1
        else:
            failed.add((str(first), str(second)))
    current = max(active, key=lambda model: len(names[model]))
    report.update(
        registeredImages=len(names[current]),
        improvedLargest=len(names[current]) > original_largest,
        components=[
            {"path": str(model), "registeredImages": len(names[model])} for model in active
        ],
        sharedImageCounts={
            f"{a.name}:{b.name}": len(names[a] & names[b])
            for i, a in enumerate(active)
            for b in active[i + 1 :]
        },
    )
    return current, report
