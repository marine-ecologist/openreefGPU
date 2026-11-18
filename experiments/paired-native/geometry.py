"""Geometry utilities for the isolated paired-video comparison."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np


def read_model(root):
    root = Path(root)
    cameras = {}
    images = {}
    points = {}
    for line in (root / "cameras.txt").read_text().splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        v = line.split()
        assert v[1] == "THIN_PRISM_FISHEYE", v[1]
        cameras[int(v[0])] = {"width": int(v[2]), "height": int(v[3]), "p": np.array(v[4:], float)}
    lines = iter((root / "images.txt").read_text().splitlines())
    for line in lines:
        if not line.strip() or line.startswith("#"):
            continue
        v = line.split()
        q = np.array(v[1:5], float)
        w, x, y, z = q / np.linalg.norm(q)
        R = np.array(
            [
                [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
            ]
        )
        observations = np.fromstring(next(lines), sep=" ").reshape(-1, 3)
        images[int(v[0])] = {
            "R": R,
            "t": np.array(v[5:8], float),
            "camera": int(v[8]),
            "name": v[9],
            "obs": observations,
        }
    for line in (root / "points3D.txt").read_text().splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        v = line.split()
        points[int(v[0])] = {"xyz": np.array(v[1:4], float), "rgb": list(map(int, v[4:7]))}
    return cameras, images, points


def quaternion(R):
    # Eigenvector method remains stable near 180-degree rotations.
    K = (
        np.array(
            [
                [
                    R[0, 0] - R[1, 1] - R[2, 2],
                    R[0, 1] + R[1, 0],
                    R[0, 2] + R[2, 0],
                    R[2, 1] - R[1, 2],
                ],
                [
                    R[0, 1] + R[1, 0],
                    R[1, 1] - R[0, 0] - R[2, 2],
                    R[1, 2] + R[2, 1],
                    R[0, 2] - R[2, 0],
                ],
                [
                    R[0, 2] + R[2, 0],
                    R[1, 2] + R[2, 1],
                    R[2, 2] - R[0, 0] - R[1, 1],
                    R[1, 0] - R[0, 1],
                ],
                [R[2, 1] - R[1, 2], R[0, 2] - R[2, 0], R[1, 0] - R[0, 1], np.trace(R)],
            ]
        )
        / 3
    )
    _, vec = np.linalg.eigh(K)
    q = vec[:, -1][[3, 0, 1, 2]]
    return q if q[0] >= 0 else -q


def write_model(root, cameras, images, points):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=False)
    tracks = {key: [] for key in points}
    with (root / "cameras.txt").open("w") as f:
        for key, c in cameras.items():
            f.write(
                f"{key} THIN_PRISM_FISHEYE {c['width']} {c['height']} "
                + " ".join(map(str, c["p"]))
                + "\n"
            )
    with (root / "images.txt").open("w") as f:
        for key, im in images.items():
            f.write(
                f"{key} "
                + " ".join(map(str, [*quaternion(im["R"]), *im["t"], im["camera"]]))
                + " "
                + im["name"]
                + "\n"
            )
            records = []
            for j, (x, y, pid) in enumerate(im["obs"]):
                pid = int(pid) if int(pid) in points else -1
                if pid != -1:
                    tracks[pid].append((key, j))
                records.extend((str(x), str(y), str(pid)))
            f.write(" ".join(records) + "\n")
    with (root / "points3D.txt").open("w") as f:
        for key, p in points.items():
            if len(tracks[key]) < 2:
                raise ValueError("Point has fewer than two observations")
            f.write(
                f"{key} "
                + " ".join(map(str, [*p["xyz"], *p["rgb"], 0]))
                + " "
                + " ".join(f"{i} {j}" for i, j in tracks[key])
                + "\n"
            )


def center(im):
    return -im["R"].T @ im["t"]


def pair_ids(images):
    groups = {}
    for key, im in images.items():
        groups.setdefault(Path(im["name"]).name, {})[Path(im["name"]).parent.name] = key
    return [
        (v["camera-A"], v["camera-B"])
        for _, v in sorted(groups.items())
        if set(v) == {"camera-A", "camera-B"}
    ]


def baseline_report(images, baseline=0.351):
    pairs = pair_ids(images)
    if len(pairs) < 3:
        raise ValueError("At least three registered A/B pairs are required")
    d = np.array([np.linalg.norm(center(images[a]) - center(images[b])) for a, b in pairs])
    median = float(np.median(d))
    if median <= 0 or not np.isfinite(d).all():
        raise ValueError("Invalid pair distances")
    relative = np.abs(d - median) / median
    return {
        "pairs": len(pairs),
        "medianMetres": median,
        "medianAbsoluteRelativeDeviation": float(np.median(relative)),
        "fractionOver10Percent": float(np.mean(relative > 0.10)),
        "medianAbsoluteErrorToMeasuredMetres": float(np.median(np.abs(d - baseline))),
        "outlierImages": [
            images[i]["name"]
            for (a, b), bad in zip(pairs, relative > 0.10, strict=True)
            if bad
            for i in (a, b)
        ],
    }


def rescale(images, points, factor):
    for im in images.values():
        im["t"] *= factor
    for p in points.values():
        p["xyz"] *= factor


def project(im, cam, xyz):
    v = np.einsum("ij,nj->ni", im["R"], xyz) + im["t"]
    rho = np.linalg.norm(v[:, :2], axis=1)
    theta = np.arctan2(rho, v[:, 2])
    uv = v[:, :2] * (theta / np.maximum(rho, 1e-15))[:, None]
    p = cam["p"]
    u, w = uv.T
    r = u * u + w * w
    rad = 1 + p[4] * r + p[5] * r * r + p[8] * r * r * r + p[9] * r**4
    dx = u * rad + 2 * p[6] * u * w + p[7] * (r + 2 * u * u) + p[10] * r
    dy = w * rad + p[6] * (r + 2 * w * w) + 2 * p[7] * u * w + p[11] * r
    return np.column_stack((p[0] * dx + p[2], p[1] * dy + p[3]))


def scores(cameras, images, points):
    ids = list(points)
    index = {key: i for i, key in enumerate(ids)}
    squared = np.zeros(len(ids))
    count = np.zeros(len(ids))
    H = np.zeros((len(ids), 3, 3))
    for im in images.values():
        obs = im["obs"]
        valid = np.array([int(pid) in index for pid in obs[:, 2]])
        o = obs[valid]
        if not len(o):
            continue
        idx = np.array([index[int(pid)] for pid in o[:, 2]])
        xyz = np.array([points[int(pid)]["xyz"] for pid in o[:, 2]])
        e = project(im, cameras[im["camera"]], xyz) - o[:, :2]
        np.add.at(squared, idx, np.sum(e * e, axis=1))
        np.add.at(count, idx, 1)
        delta = xyz - center(im)
        distance = np.linalg.norm(delta, axis=1)
        direction = delta / np.maximum(distance, 1e-12)[:, None]
        info = (np.eye(3)[None, :, :] - direction[:, :, None] * direction[:, None, :]) / np.maximum(
            distance, 1e-12
        )[:, None, None] ** 2
        np.add.at(H, idx, info)
    ev = np.linalg.eigvalsh(H)
    uncertainty = np.sqrt(np.maximum(ev[:, -1], 0) / np.maximum(ev[:, 0], 1e-20))
    return ids, np.sqrt(squared / np.maximum(count, 1)), uncertainty


def prune(points, ids, values, floor, fraction):
    eligible = [i for i, v in enumerate(values) if np.isfinite(v) and v > floor]
    n = min(len(eligible), int(len(points) * fraction))
    remove = sorted(eligible, key=lambda i: values[i], reverse=True)[:n]
    for i in remove:
        points.pop(ids[i], None)
    return n


def orient(images, points):
    xyz = np.array([p["xyz"] for p in points.values()])
    origin = np.median(xyz, axis=0)
    distance = np.linalg.norm(xyz - origin, axis=1)
    core = xyz[distance <= np.quantile(distance, 0.95)]
    _, _, axes = np.linalg.svd(core - origin, full_matrices=False)
    z = axes[-1]
    towards = np.median(np.array([center(im) for im in images.values()]), axis=0) - origin
    if np.dot(z, towards) < 0:
        z = -z
    x = axes[0]
    y = np.cross(z, x)
    Q = np.vstack((x, y, z))
    heights = (xyz - origin) @ z
    for p in points.values():
        p["xyz"] = Q @ (p["xyz"] - origin)
    for im in images.values():
        c = Q @ (center(im) - origin)
        im["R"] = im["R"] @ Q.T
        im["t"] = -im["R"] @ c
    return {
        "rotation": Q.tolist(),
        "origin": origin.tolist(),
        "heightAbsoluteP95Metres": float(np.quantile(np.abs(heights), 0.95)),
        "coordinateSystem": "local reef-plane metres; not true vertical",
        "flattened": False,
    }


def solve(binary, root, cameras, images, points, *, bars, gpu, iterations=100):
    root = Path(root)
    root.mkdir()
    source = root / "problem.tsv"
    target = root / "solution.tsv"
    with source.open("w") as f:
        for key, c in cameras.items():
            p = c["p"]
            v = [(p[0] + p[1]) / 2, p[2], p[3], p[4], p[5], p[8], p[6], p[7]]
            f.write(f"c {key} " + " ".join(map(str, v)) + "\n")
        for key, im in images.items():
            aa = cv2.Rodrigues(im["R"])[0].reshape(3)
            f.write(f"i {key} {im['camera']} " + " ".join(map(str, [*aa, *im["t"]])) + "\n")
        for key, p in points.items():
            f.write(f"p {key} " + " ".join(map(str, p["xyz"])) + "\n")
        for key, im in images.items():
            for x, y, pid in im["obs"]:
                if int(pid) in points:
                    f.write(f"o {key} {int(pid)} {x} {y}\n")
        for a, b in pair_ids(images):
            f.write(f"b {a} {b} 0.351 0.003\n")
    import subprocess

    command = (
        [str(binary), str(source), str(target), str(iterations)]
        + (["--gpu"] if gpu else [])
        + ([] if bars else ["--no-bars"])
    )
    subprocess.run(command, check=True)
    for line in target.read_text().splitlines():
        v = line.split()
        key = int(v[1]) if v[0] != "s" else None
        p = np.array(v[2:], float)
        if v[0] == "c":
            cameras[key]["p"] = np.array(
                [p[0], p[0], p[1], p[2], p[3], p[4], p[6], p[7], p[5], 0, 0, 0]
            )
        elif v[0] == "i":
            images[key]["R"] = cv2.Rodrigues(p[:3])[0]
            images[key]["t"] = p[3:]
        elif v[0] == "p":
            points[key]["xyz"] = p
        elif v[0] == "s":
            result = {
                "initialCost": float(v[1]),
                "finalCost": float(v[2]),
                "residuals": int(v[3]),
                "distanceFactors": len(pair_ids(images)) if bars else 1,
            }
    if any(not np.isfinite(c["p"]).all() for c in cameras.values()):
        raise ValueError("Nonfinite camera calibration")
    return result
