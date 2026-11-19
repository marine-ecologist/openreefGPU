"""GoPro gyro synchronization and exact-PTS image extraction."""

import bisect
import csv
import json
import re
import struct
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from fractions import Fraction
from pathlib import Path

import numpy as np

from worker.image_timestamps import write_capture_time

RATE = 1000


@dataclass(frozen=True)
class Video:
    path: Path
    stream: dict
    pts: list[int]
    times: np.ndarray
    gyro_times: np.ndarray
    gyro_magnitude: np.ndarray
    settings: dict
    tags: dict

    @property
    def fps(self):
        return Fraction(self.stream["avg_frame_rate"])


def probe(path, *options):
    return json.loads(
        subprocess.check_output(["ffprobe", "-v", "error", *options, "-of", "json", str(path)])
    )


def klv(data):
    offset = 0
    while offset + 8 <= len(data):
        key, kind, size, repeat = struct.unpack_from(">4scBH", data, offset)
        offset += 8
        length = size * repeat
        if offset + length > len(data):
            raise ValueError("Truncated GoPro metadata")
        yield key, kind, size, data[offset : offset + length]
        offset += (length + 3) & ~3


def numeric(kind, payload):
    dtype = {
        b"b": "i1",
        b"B": "u1",
        b"s": ">i2",
        b"S": ">u2",
        b"l": ">i4",
        b"L": ">u4",
        b"f": ">f4",
        b"d": ">f8",
    }.get(kind)
    return np.frombuffer(payload, dtype).astype(float) if dtype else np.array([])


def gyro_samples(data, settings):
    scale = np.array([])
    for key, kind, _, payload in klv(data):
        if kind == b"\0":
            yield from gyro_samples(payload, settings)
        elif key == b"SCAL":
            scale = numeric(kind, payload)
        elif key == b"GYRO":
            if len(scale) not in (1, 3) or np.any(scale <= 0):
                raise ValueError("Invalid GPMF gyro scale")
            yield np.linalg.norm(numeric(kind, payload).reshape(-1, 3) / scale, axis=1)
        elif key in (b"SROT", b"EISE"):
            values = numeric(kind, payload)
            value = (
                values[0].item() if len(values) else payload.decode("ascii", "replace").strip("\0 ")
            )
            settings.setdefault(key.decode(), set()).add(value)


def gyro(path, stream):
    packets = probe(
        path,
        "-select_streams",
        str(stream),
        "-show_packets",
        "-show_entries",
        "packet=pts_time,duration_time,pos,size",
    ).get("packets", [])
    times, magnitudes, settings = [], [], {}
    with path.open("rb") as source:
        for packet in packets:
            source.seek(int(packet["pos"]))
            groups = list(gyro_samples(source.read(int(packet["size"])), settings))
            if groups and float(packet.get("duration_time", 0)) > 0:
                samples = np.concatenate(groups)
                if len(samples):
                    times.append(
                        float(packet["pts_time"])
                        + np.arange(len(samples)) * float(packet["duration_time"]) / len(samples)
                    )
                    magnitudes.append(samples)
    if not times:
        raise ValueError(f"{path.name} has no usable GPMF gyroscope samples")
    t = np.concatenate(times)
    if np.any(np.diff(t) <= 0):
        raise ValueError(f"{path.name} has nonmonotonic gyro timestamps")
    if np.max(np.diff(t), initial=0) > 0.25:
        raise ValueError(f"{path.name} has gaps in its gyro timestamps")
    return (
        t,
        np.concatenate(magnitudes),
        {key: sorted(values, key=str) for key, values in settings.items()},
    )


def resample(t, values):
    start = int(np.ceil(t[0] * RATE))
    grid = np.arange(start, int(np.floor(t[-1] * RATE)) + 1)
    if len(grid) < RATE * 10:
        raise ValueError("Each video needs at least 10 seconds of gyro telemetry")
    uniform = np.interp(grid / RATE, t, values)
    # Reflect edges before applying a zero-phase second-order Butterworth response.
    pad = min(len(uniform) - 1, 10 * RATE)
    padded = np.pad(uniform, pad, mode="reflect")
    frequency = np.fft.rfftfreq(len(padded), 1 / RATE)
    gain = frequency**4 / (frequency**4 + 0.1**4)
    filtered = np.fft.irfft(np.fft.rfft(padded) * gain, n=len(padded))[pad:-pad]
    if np.std(filtered) < 1e-6:
        raise ValueError("Insufficient rotation to synchronize the videos")
    return start, (filtered - filtered.mean()) / filtered.std()


def correlate(a, b):
    size = len(a) + len(b) - 1
    fft_size = 1 << (size - 1).bit_length()
    return np.fft.irfft(np.fft.rfft(a, fft_size) * np.fft.rfft(b[::-1], fft_size), fft_size)[:size]


def fit_sync(left, right):
    a0, a = resample(*left)
    b0, b = resample(*right)
    # Coarse FFT on a decimated 100 Hz grid, then fit at the full 1 kHz rate.
    coarse_a = a[: len(a) // 10 * 10].reshape(-1, 10).mean(axis=1)
    coarse_b = b[: len(b) // 10 * 10].reshape(-1, 10).mean(axis=1)
    lags = np.arange(1 - len(coarse_b), len(coarse_a))
    allowed = np.abs(a0 - b0 + lags * 10) <= 10 * RATE
    scores = correlate(coarse_a, coarse_b)
    if not np.any(allowed):
        raise ValueError("GoPro metadata clocks have no overlap within the 10-second search")
    coarse = int(lags[allowed][np.argmax(scores[allowed])] * 10)
    windows = []
    search = 50
    start = max(0, coarse + search)
    end = min(len(a), len(b) + coarse - search)
    width = min(20 * RATE, end - start)
    if width < 5 * RATE:
        raise ValueError("Less than five seconds of usable synchronized overlap")
    anchors = list(range(start, end - width + 1, 30 * RATE))
    if anchors[-1] < end - width:
        anchors.append(end - width)
    for first in anchors:
        x = a[first : first + width]
        y = b[first - coarse - search : first - coarse + width + search]
        x = x - x.mean()
        dot = correlate(y, x)[width - 1 : len(y)]
        sums = np.r_[0, np.cumsum(y)]
        squares = np.r_[0, np.cumsum(y * y)]
        variance = squares[width:] - squares[:-width] - (sums[width:] - sums[:-width]) ** 2 / width
        denominator = np.linalg.norm(x) * np.sqrt(np.maximum(variance, 0))
        scores = np.divide(dot, denominator, out=np.zeros_like(dot), where=denominator > 1e-9)
        peak = int(np.argmax(scores))
        if scores[peak] < 0.99 or peak in (0, len(scores) - 1):
            continue
        before, best, after = scores[peak - 1 : peak + 2]
        curvature = before - 2 * best + after
        correction = 0.5 * (before - after) / curvature if curvature < 0 else 0
        windows.append(
            (
                (a0 + first + width / 2) / RATE,
                (a0 - b0 + coarse + search - peak - correction) / RATE,
                float(np.clip(best, 0, 1)),
            )
        )
    if not windows:
        raise ValueError("Gyro synchronization failed: no correlation window reached r ≥ 0.99")
    t, lags, correlations = np.asarray(windows).T
    # Short recordings cannot constrain drift reliably.
    drift = 0.0
    if len(t) >= 3:
        first, second = np.triu_indices(len(t), k=1)
        drift = np.median((lags[second] - lags[first]) / (t[second] - t[first]))
    offset = np.median(lags - drift * t)
    rms_ms = float(np.sqrt(np.mean((lags - offset - drift * t) ** 2)) * 1000)
    warnings = []
    if len(windows) < 5 or np.ptp(t) < 0.5 * (end - start) / RATE:
        warnings.append("Fewer than five well-distributed gyro correlation windows")
    if rms_ms >= 0.5:
        warnings.append("Gyro lag fit residual RMS exceeds 0.5 ms")
    return {
        "L0_s": float(offset),
        "drift_s_per_s": float(drift),
        "window_count": len(windows),
        "min_r": float(min(correlations)),
        "residual_rms_ms": rms_ms,
        "windows": windows,
        "warnings": warnings,
    }


def inspect_video(path: Path) -> Video:
    metadata = probe(path, "-show_streams", "-show_format")
    video = next((s for s in metadata["streams"] if s["codec_type"] == "video"), None)
    gyro_stream = next(
        (
            s
            for s in metadata["streams"]
            if s.get("codec_tag_string") == "gpmd"
            or s.get("tags", {}).get("handler_name") == "GoPro MET"
        ),
        None,
    )
    if not video or not gyro_stream:
        raise ValueError(f"{path.name} requires a video stream and GoPro MET telemetry")
    # GoPro MP4 has one video sample per frame. Read sample timestamps without decoding
    # the entire clip twice. Decode still verifies the selected frames against those PTS.
    packets = probe(
        path,
        "-select_streams",
        str(video["index"]),
        "-show_packets",
        "-show_entries",
        "packet=pts",
    ).get("packets", [])
    pts = sorted(int(packet["pts"]) for packet in packets if "pts" in packet)
    if (
        len(pts) != len(packets)
        or len(pts) != int(str(video.get("nb_frames", "0")).replace("N/A", "0"))
        or len(set(pts)) != len(pts)
        or any(pts_value < 0 for pts_value in pts)
    ):
        frames = probe(
            path,
            "-select_streams",
            str(video["index"]),
            "-show_frames",
            "-show_entries",
            "frame=best_effort_timestamp",
        ).get("frames", [])
        pts = [int(frame["best_effort_timestamp"]) for frame in frames]
    numerator, denominator = map(int, video["time_base"].split("/"))
    times = np.asarray(pts) * (numerator / denominator)
    if len(pts) < 2 or np.any(np.diff(times) <= 0):
        raise ValueError(f"{path.name} needs strictly increasing video PTS")
    gyro_times, gyro_magnitude, settings = gyro(path, gyro_stream["index"])
    tags = metadata.get("format", {}).get("tags", {})
    return Video(path, video, pts, times, gyro_times, gyro_magnitude, settings, tags)


def choose_pairs(left, right, sync, interval, limit):
    pairs = []
    last_right = -1
    next_time = float(left[0])
    for i, t in enumerate(left):
        if t < next_time - 1e-9:
            continue
        mapped = t - sync["L0_s"] - sync["drift_s_per_s"] * t
        if not right[0] <= mapped <= right[-1]:
            continue
        index = bisect.bisect_left(right, mapped)
        j = min((max(0, index - 1), index), key=lambda k: abs(right[k] - mapped))
        if j <= last_right:
            continue
        pairs.append((i, j, float(t), float(right[j]), float((mapped - right[j]) * 1000)))
        last_right = j
        next_time += (int(max(0, t - next_time) / interval) + 1) * interval
        if len(pairs) == limit:
            break
    if len(pairs) < 2:
        raise ValueError("Less than two frame pairs overlap at this sampling interval")
    return pairs


def decode(path, stream, pts, indices, folder, quality):
    folder.mkdir(parents=True)
    selection = "+".join(f"eq(n,{index})" for index in indices)
    run = subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "info",
            "-copyts",
            "-noautorotate",
            "-i",
            str(path),
            "-map",
            f"0:{stream}",
            "-vf",
            f"select='{selection}',showinfo",
            "-fps_mode",
            "passthrough",
            "-frames:v",
            str(len(indices)),
            "-q:v",
            str(quality),
            str(folder / "%05d.jpg"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    actual = [int(value) for value in re.findall(r"\bn:\s*\d+\s+pts:\s*(-?\d+)", run.stderr)]
    expected = [pts[index] for index in indices]
    if run.returncode or actual != expected or len(list(folder.glob("*.jpg"))) != len(indices):
        raise ValueError(
            f"Could not decode the verified target frames from {path.name}: {run.stderr[-500:]}"
        )


def motion_trace(video: Video, start, duration, fps):
    """Track image motion through a small, uniformly sampled QC window."""
    import cv2

    width, height = 192, 108
    raw = subprocess.check_output(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-ss",
            f"{start:.6f}",
            "-noautorotate",
            "-i",
            str(video.path),
            "-map",
            f"0:{video.stream['index']}",
            "-t",
            str(duration),
            "-vf",
            f"scale={width}:{height},fps={fps}",
            "-pix_fmt",
            "gray",
            "-threads:v",
            "1",
            "-f",
            "rawvideo",
            "-",
        ]
    )
    frames = np.frombuffer(raw, dtype=np.uint8).reshape(-1, height, width)
    trace = []
    for previous, current in zip(frames[:-1], frames[1:], strict=True):
        flow = cv2.calcOpticalFlowFarneback(previous, current, None, 0.5, 3, 13, 2, 5, 1.1, 0)
        trace.append(np.median(np.linalg.norm(flow, axis=2)))
    return np.asarray(trace)


def check_image_motion(left: Video, right: Video, sync):
    """Compare motion after gyro alignment; a consistent extra frame signals a clock mismatch."""
    fps = min(left.fps, right.fps)
    if not fps:
        return []
    scores = []
    candidates = sync.get("windows", [])
    for center, _, _ in sorted(candidates, key=lambda entry: -entry[2])[:3]:
        a_start = center - 3
        b_start = a_start - sync["L0_s"] - sync["drift_s_per_s"] * a_start
        if (
            a_start < left.times[0]
            or b_start < right.times[0]
            or a_start + 6 > left.times[-1]
            or b_start + 6 > right.times[-1]
        ):
            continue
        a = motion_trace(left, a_start - left.times[0], 6, fps)
        b = motion_trace(right, b_start - right.times[0], 6, fps)
        width = min(len(a), len(b))
        search = max(2, int(fps / 4))
        if width < search * 4:
            continue
        fixed = a[search : width - search]
        if np.std(fixed) < 0.015:
            continue
        values = []
        for shift in range(-search, search + 1):
            other = b[search + shift : width - search + shift]
            values.append(np.corrcoef(fixed, other)[0, 1] if np.std(other) >= 0.015 else -1)
        best = int(np.argmax(values))
        if values[best] >= 0.5 and 0 < best < len(values) - 1:
            scores.append(
                {
                    "reference_time_s": center,
                    "residual_frames": best - search,
                    "r": float(values[best]),
                    "sample_fps": str(fps),
                }
            )
    return scores


def check_epipolar(output, profile):
    """Measure symmetric point-to-epipolar-line distance on a few matched pairs."""
    import cv2

    def geometry(camera):
        fx, fy, cx, cy, *distortion = camera["params"]
        intrinsics = np.array([[fx, 0, cx], [0, fy, cy], [0, 0, 1]], dtype=float)
        return intrinsics, np.array(distortion or [0.0] * 4, dtype=float)

    matrices = list(map(geometry, profile["cameras"]))
    pose = profile["rightFromLeft"]
    tx, ty, tz = pose["translation"]
    cross = np.array([[0, -tz, ty], [tz, 0, -tx], [-ty, tx, 0]])
    fundamental = (
        np.linalg.inv(matrices[1][0]).T @ cross @ pose["rotation"] @ np.linalg.inv(matrices[0][0])
    )
    sift = cv2.SIFT_create(nfeatures=1500)
    matcher = cv2.BFMatcher()
    paths = sorted((output / "left").glob("*.jpg"))
    results = []
    for index in np.linspace(0, len(paths) - 1, min(len(paths), 3), dtype=int):
        path = paths[index]
        features = []
        for image, camera, (matrix, distortion) in zip(
            (path, output / "right" / path.name), profile["cameras"], matrices, strict=True
        ):
            gray = cv2.imread(str(image), cv2.IMREAD_GRAYSCALE)
            if (gray.shape[1], gray.shape[0]) != (camera["width"], camera["height"]):
                raise ValueError(
                    f"{image.name} video resolution differs from calibrated intrinsics"
                )
            scale = min(1, 1600 / gray.shape[1])
            scaled = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
            keys, descriptors = sift.detectAndCompute(scaled, None)
            if descriptors is None or len(keys) < 2:
                break
            points = np.asarray([key.pt for key in keys], dtype=float).reshape(-1, 1, 2) / scale
            undistort = (
                cv2.fisheye.undistortPoints
                if camera["model"] == "OPENCV_FISHEYE"
                else cv2.undistortPoints
            )
            ideal = undistort(points, matrix, distortion, P=matrix).reshape(-1, 2)
            features.append((np.column_stack([ideal, np.ones(len(ideal))]), descriptors))
        if len(features) != 2:
            continue
        (a, desc_a), (b, desc_b) = features
        reverse = {
            x.queryIdx: x.trainIdx
            for x, y in matcher.knnMatch(desc_b, desc_a, k=2)
            if x.distance < 0.75 * y.distance
        }
        pairs = [
            (x.queryIdx, x.trainIdx)
            for x, y in matcher.knnMatch(desc_a, desc_b, k=2)
            if x.distance < 0.75 * y.distance and reverse.get(x.trainIdx) == x.queryIdx
        ]
        if len(pairs) < 8:
            continue
        i, j = np.asarray(pairs).T
        line_b = a[i] @ fundamental.T
        line_a = b[j] @ fundamental
        dots = abs(np.sum(line_b * b[j], axis=1))
        distances = (
            dots
            * 0.5
            * (
                1 / np.maximum(1e-12, np.linalg.norm(line_a[:, :2], axis=1))
                + 1 / np.maximum(1e-12, np.linalg.norm(line_b[:, :2], axis=1))
            )
        )
        finite = distances[np.isfinite(distances)]
        if len(finite) >= 8:
            results.append(
                {
                    "file": path.name,
                    "matches": len(finite),
                    "median_pixels": float(np.median(finite)),
                }
            )
    return results


def extract(camera_a, camera_b, output, config):
    reference = config["referenceCamera"]
    sources = (camera_a, camera_b) if reference == "A" else (camera_b, camera_a)
    left, right = map(inspect_video, sources)
    sync = fit_sync(
        (left.gyro_times, left.gyro_magnitude), (right.gyro_times, right.gyro_magnitude)
    )
    sync.update(
        reference_camera=reference,
        left_camera=reference,
        right_camera="B" if reference == "A" else "A",
        sources=config.get("originalNames", [camera_a.name, camera_b.name]),
        video=[
            {
                "fps": str(video.fps),
                "width": video.stream["width"],
                "height": video.stream["height"],
                **video.settings,
            }
            for video in (left, right)
        ],
    )
    if left.fps != right.fps or any(
        left.stream[key] != right.stream[key] for key in ("width", "height")
    ):
        sync["warnings"].append(
            "Camera video modes differ. Use matching modes for verified gyro-to-video timing."
        )
    for metadata in sync["video"]:
        values = metadata.get("EISE", [])
        stabilized = any(
            str(value).lower() not in ("0", "0.0", "n", "off", "false", "none") for value in values
        )
        if stabilized and config.get("profile"):
            raise ValueError(
                "HyperSmooth telemetry indicates stabilization; calibrated rig requires it off"
            )
        if stabilized or not values:
            sync["warnings"].append("Check HyperSmooth settings; fixed calibration requires it off")
    try:
        sync["image_motion"] = check_image_motion(left, right, sync)
    except (subprocess.SubprocessError, ValueError):
        sync["image_motion"] = []
    if not sync["image_motion"]:
        sync["warnings"].append("Optical-flow synchronization check was inconclusive")
    elif any(abs(check["residual_frames"]) > 1 for check in sync["image_motion"]):
        sync["warnings"].append(
            "Optical flow and gyro timing disagree by more than one video frame"
        )
    pairs = choose_pairs(
        left.times, right.times, sync, config["intervalSeconds"], config["maxPairs"]
    )
    anchor = datetime(1970, 1, 1, tzinfo=timezone.utc)
    sync["timestamp_origin"] = "synthetic UTC epoch"
    if value := left.tags.get("creation_time"):
        try:
            candidate = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if candidate.tzinfo:
                anchor = candidate
                sync["timestamp_origin"] = "reference container creation_time (label only)"
        except ValueError:
            pass

    for index, (side, video) in enumerate((("left", left), ("right", right))):
        decode(
            video.path,
            video.stream["index"],
            video.pts,
            [pair[index] for pair in pairs],
            output / side,
            config["jpegQuality"],
        )

    sync["colour_correction"] = bool(config.get("colourCorrection"))
    if sync["colour_correction"]:
        import cv2
        from openreef.io.color_correction import enhance_underwater

        quality = 100 if config["jpegQuality"] == 1 else 98
        for image in output.glob("*/*.jpg"):
            pixels = cv2.imread(str(image), cv2.IMREAD_COLOR)
            if pixels is None or not cv2.imwrite(
                str(image), enhance_underwater(pixels), [cv2.IMWRITE_JPEG_QUALITY, quality]
            ):
                raise RuntimeError(f"Could not colour-correct {image.name}")

    stamps = {"left": {}, "right": {}}
    with (output / "pairs.csv").open("w", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(
            (
                "pair",
                "file",
                "frame_left",
                "frame_right",
                "time_left_s",
                "time_right_s",
                "sync_residual_ms",
            )
        )
        for pair, row in enumerate(pairs, 1):
            path = f"{pair:05d}.jpg"
            name = f"{config['site']}_{path}"
            capture = anchor + timedelta(seconds=row[2] - left.times[0])
            for side in stamps:
                folder = output / side
                (folder / path).rename(folder / name)
                write_capture_time(folder / name, capture)
                stamps[side][name] = capture.timestamp()
            writer.writerow((pair, name, *row))
    if config.get("profile"):
        sync["epipolar"] = check_epipolar(output, config["profile"])
        if not sync["epipolar"]:
            sync["warnings"].append("Epipolar check was inconclusive on the sampled frame pairs")
        elif any(check["median_pixels"] > 1 for check in sync["epipolar"]):
            sync["warnings"].append("Sampled median epipolar residual exceeded 1 pixel")
    for name, data in (("sync", sync), ("timestamps", stamps)):
        (output / f"{name}.json").write_text(json.dumps(data, indent=2) + "\n")
    return stamps
