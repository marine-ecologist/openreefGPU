import csv
import json
import shutil
import struct
import subprocess

import numpy as np
import pytest
from PIL import Image

from worker import stereo_video
from worker.handler import stage_video_source


def entry(key, kind, sample_size, payload):
    return (
        struct.pack(">4scBH", key, kind, sample_size, len(payload) // sample_size)
        + payload
        + b"\0" * (-len(payload) % 4)
    )


def test_gyro_scaling_and_orientation():
    data = entry(b"SCAL", b"s", 2, struct.pack(">h", 2))
    data += entry(b"GYRO", b"s", 6, struct.pack(">6h", 6, 8, 0, 0, 6, 8))
    settings = {}
    data += entry(b"EISE", b"c", 1, b"N")
    groups = list(stereo_video.gyro_samples(entry(b"STRM", b"\0", 1, data), settings))
    np.testing.assert_equal(groups[0], [5, 5])
    assert settings == {"EISE": {"N"}}


def test_gyro_clock_offset_and_drift():
    rng = np.random.default_rng(5)
    grid = np.arange(0, 191, 0.032)
    rotations = rng.random(len(grid))
    t = np.arange(1, 181, 1 / 198)
    offset, drift = 0.125, 0.000008
    a = np.interp(t, grid, rotations)
    b = np.interp((t + offset) / (1 - drift), grid, rotations)
    sync = stereo_video.fit_sync((t, a), (t, b))
    assert sync["L0_s"] == pytest.approx(offset, abs=0.0005)
    assert sync["drift_s_per_s"] == pytest.approx(drift, abs=2e-6)
    assert sync["window_count"] >= 5
    assert sync["min_r"] >= 0.99
    assert sync["residual_rms_ms"] < 0.5
    assert not sync["warnings"]


def test_pairing_uses_actual_pts_and_signed_residual():
    sync = {"L0_s": 0.15, "drift_s_per_s": 0}
    pairs = stereo_video.choose_pairs([0, 1, 2.02, 3.03], [0, 0.84, 1.85, 2.89], sync, 1, 3)
    assert [(a, b) for a, b, *_ in pairs] == [(1, 1), (2, 2), (3, 3)]
    assert pairs[0][-1] == pytest.approx(10)
    assert pairs[1][-1] == pytest.approx(20)


@pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="needs FFmpeg")
def test_native_exact_decode_and_sidecars(tmp_path, monkeypatch):
    video = tmp_path / "test.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=160x96:rate=30000/1001",
            "-t",
            "3",
            "-c:v",
            "mpeg4",
            "-metadata",
            "creation_time=2026-11-18T12:00:00Z",
            str(video),
        ],
        check=True,
    )
    metadata = stereo_video.probe(video, "-show_streams")["streams"][0]
    frames = stereo_video.probe(
        video,
        "-select_streams",
        "v:0",
        "-show_frames",
        "-show_entries",
        "frame=best_effort_timestamp,best_effort_timestamp_time",
    )
    pts = [int(frame["best_effort_timestamp"]) for frame in frames["frames"]]
    times = np.asarray(pts) / int(metadata["time_base"].split("/")[1])
    monkeypatch.setattr(
        stereo_video,
        "inspect_video",
        lambda _: stereo_video.Video(
            video,
            metadata,
            pts,
            times,
            np.array([]),
            np.array([]),
            {"EISE": ["N"]},
            {"creation_time": "2026-11-18T12:00:00Z"},
        ),
    )
    monkeypatch.setattr(
        stereo_video, "fit_sync", lambda *_: {"L0_s": 0.04, "drift_s_per_s": 0, "warnings": []}
    )
    output = tmp_path / "paired"
    stamps = stereo_video.extract(
        video,
        video,
        output,
        {
            "referenceCamera": "B",
            "intervalSeconds": 1,
            "maxPairs": 2,
            "jpegQuality": 2,
            "site": "reef",
        },
    )
    assert stamps["left"] == stamps["right"]
    with (output / "pairs.csv").open() as file:
        pairs = list(csv.DictReader(file))
    assert len(pairs) == 2
    assert int(pairs[0]["frame_left"]) == 2
    assert int(pairs[0]["frame_right"]) == 1
    assert json.loads((output / "sync.json").read_text())["reference_camera"] == "B"
    with Image.open(output / "right" / "reef_00001.jpg") as image:
        assert image.size == (160, 96)
        details = image.getexif().get_ifd(34665)
        assert details[36867] == "2026:11:18 12:00:00"
        assert details[37521] == "066733"
    with pytest.raises(ValueError, match="verified target frames"):
        stereo_video.decode(video, 0, [value + 1 for value in pts], [0, 10], tmp_path / "bad", 2)


def test_review_can_resume_and_exclude_pairs(tmp_path, monkeypatch):
    import hashlib

    class Storage:
        def __init__(self):
            self.assets = {}
            self.downloads = 0

        def download_folder(self, _, folder, files, report):
            self.downloads += 1
            folder.mkdir()
            for file in files:
                (folder / file["name"]).write_bytes(b"source")

        def put_file(self, key, path):
            self.assets[key] = path.read_bytes()

        def put_bytes(self, key, data, _):
            self.assets[key] = data

    def fake_extract(_, __, output, ___):
        stamps = {"left": {}, "right": {}}
        for side in stamps:
            (output / side).mkdir(parents=True)
            for index in range(1, 4):
                name = f"reef_{index:05d}.jpg"
                stamps[side][name] = index
                (output / side / name).write_bytes(b"jpeg")
        (output / "timestamps.json").write_text(json.dumps(stamps))
        (output / "sync.json").write_text('{"warnings":[]}')
        (output / "pairs.csv").write_text("pairs")

    monkeypatch.setattr(stereo_video, "extract", fake_extract)
    storage = Storage()
    job = {
        "stereoVideo": {"reviewPairs": True},
        "files": [
            {
                "name": f"camera-{camera}.mp4",
                "size": 6,
                "sourceMd5": hashlib.md5(b"source").hexdigest(),
            }
            for camera in "AB"
        ],
    }
    arguments = (storage, "jobs/id", job, tmp_path / "reef", "jobs/id/input", lambda *_: None)
    assert stage_video_source(*arguments) is None
    assert "jobs/id/output/stage-0.tar.gz" in storage.assets
    assert not (tmp_path / "reef" / "images").exists()
    job.update(reviewed=True, excludedPairs=[2])
    assert stage_video_source(*arguments) == 4
    images = tmp_path / "reef" / "images"
    assert sorted(path.name for path in images.glob("*.jpg")) == [
        "00001_left.jpg",
        "00001_right.jpg",
        "00003_left.jpg",
        "00003_right.jpg",
    ]
    assert all(path.read_bytes() == b"jpeg" for path in images.glob("*.jpg"))
    assert storage.downloads == 1


@pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="needs FFmpeg")
def test_real_mp4_packet_timestamps_and_gpmf_extraction(tmp_path):
    """Mux timed data samples using a length-preserving MP4 sample entry rewrite."""
    rate, seconds = 198, 14
    rotations = np.random.default_rng(8).integers(200, 12000, size=1000)
    reference = np.linspace(-1, seconds + 1, len(rotations))
    times = np.arange(rate * seconds) / rate
    cameras = []
    for camera, offset in (("A", 0), ("B", 0.085)):
        samples = np.interp(times + offset, reference, rotations).astype(int)
        packets = []
        for group in samples.reshape(seconds, rate):
            xyz = [(int(value), 0, 0) for value in group]
            strm = entry(b"SCAL", b"s", 2, struct.pack(">h", 100))
            strm += entry(b"GYRO", b"s", 6, b"".join(struct.pack(">3h", *v) for v in xyz))
            strm += entry(b"EISE", b"c", 1, b"N")
            strm += entry(b"SROT", b"f", 4, struct.pack(">f", 0.01545))
            packets.append(entry(b"STRM", b"\0", 1, strm))
        timing = tmp_path / f"{camera}.srt"
        timing.write_text(
            "\n".join(
                f"{i + 1}\n00:00:{i:02},000 --> 00:00:{i + 1:02},000\n"
                + "x" * (len(packet) - 2)
                + "\n"
                for i, packet in enumerate(packets)
            )
        )
        video = tmp_path / f"{camera}.mp4"
        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                "testsrc2=size=160x96:rate=30",
                "-i",
                str(timing),
                "-t",
                str(seconds),
                "-c:v",
                "mpeg4",
                "-c:s",
                "mov_text",
                str(video),
            ],
            check=True,
        )
        metadata = stereo_video.probe(
            video, "-select_streams", "s:0", "-show_packets", "-show_entries", "packet=pos,size"
        )["packets"]
        content = bytearray(video.read_bytes())
        for packet, sample in zip(packets, metadata, strict=True):
            assert len(packet) == int(sample["size"])
            start = int(sample["pos"])
            content[start : start + len(packet)] = packet
        video.write_bytes(
            content.replace(b"sbtl", b"meta")
            .replace(b"tx3g", b"gpmd")
            .replace(b"SubtitleHandler", b"GoPro MET" + b"\0" * 6)
        )
        cameras.append(video)

    output = tmp_path / "paired"
    stereo_video.extract(
        *cameras,
        output,
        {
            "referenceCamera": "A",
            "intervalSeconds": 1,
            "maxPairs": 4,
            "jpegQuality": 2,
            "site": "reef",
        },
    )
    sync = json.loads((output / "sync.json").read_text())
    assert sync["L0_s"] == pytest.approx(0.085, abs=0.0007)
    assert sync["video"][0]["EISE"] == ["N"]
    assert sync["video"][0]["SROT"] == pytest.approx([0.01545])
    assert sync["window_count"] >= 1
    assert len(list((output / "right").glob("*.jpg"))) == 4


def test_completed_stage_remains_downloadable_after_later_failure(tmp_path, monkeypatch):
    import sys
    import tarfile

    from worker import handler

    dataset = tmp_path / "reef"
    database = dataset / "colmap" / "database.db"
    database.parent.mkdir(parents=True)
    database.write_bytes(b"completed database")
    job = {}

    class Storage:
        def put_file(self, key, path):
            if key.endswith(".tar.gz"):
                with tarfile.open(path) as archive:
                    member = archive.extractfile("colmap/database.db")
                    assert member.read() == b"completed database"

        def put_bytes(self, key, data, _):
            self.record = json.loads(data)

    storage = Storage()
    program = [
        sys.executable,
        "-c",
        "import sys\n"
        "print('[1/8] Sequential matching')\n"
        "print('✓ Sequential matching complete')\n"
        "print('[2/8] Sparse reconstruction')\n"
        "print('ERROR: Sparse reconstruction exited with code 2')\n"
        "sys.exit(2)",
    ]
    monkeypatch.setattr(handler, "pipeline_command", lambda *args, **kwargs: program)
    with pytest.raises(RuntimeError, match="exited with code 2"):
        handler.run_pipeline(
            dataset,
            lambda *_: None,
            completed=lambda stage, reused: handler.publish_video_checkpoint(
                storage, "jobs/id", job, dataset, stage, reused
            ),
            uncalibrated=True,
        )
    assert [asset["name"] for asset in storage.record["stageAssets"]] == [
        "stage-1.tar.gz",
        "pipeline.log",
    ]
    assert (
        "Sparse reconstruction exited with code 2"
        in (dataset / "logs" / "pipeline.log").read_text()
    )
    assert program[-1] == "--no-single-camera"


def test_volume_purge_preserves_other_jobs_and_history(tmp_path, monkeypatch):
    from worker.volume_jobs import VolumeJobStorage

    monkeypatch.setenv("OPENREEF_VOLUME_ROOT", str(tmp_path))
    volume = VolumeJobStorage()
    volume.put_bytes("other/input/original.mp4", b"preserve", "video/mp4")
    for directory in ("input", "output", "workspace", "diagnostics"):
        volume.put_bytes(f"test/{directory}/data", b"purge", "application/octet-stream")
    metadata = {
        "id": "test",
        "name": "Survey",
        "state": "completed",
        "result": {"bundle": "openreef-results.tar"},
    }
    volume.put_bytes("test/job.json", json.dumps(metadata).encode(), "application/json")
    assert volume.purge("test")["state"] == "purged"
    assert volume.purge("test")["name"] == "Survey"
    assert {path.name for path in volume.path("test").iterdir()} == {"job.json", ".lock"}
    assert volume.path("other/input/original.mp4").read_bytes() == b"preserve"
