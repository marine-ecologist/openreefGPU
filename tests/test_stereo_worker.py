import json

from PIL import Image

from worker.handler import stage_stereo_source


def test_worker_separates_camera_downloads_and_preserves_missed_frames(tmp_path):
    profile = {
        "schemaVersion": 1,
        "translationUnit": "metres",
        "cameras": [
            {
                "id": c,
                "model": "PINHOLE",
                "width": 640,
                "height": 480,
                "params": [500, 500, 320, 240],
            }
            for c in ("left", "right")
        ],
        "rightFromLeft": {
            "rotation": [[1, 0, 0], [0, 1, 0], [0, 0, 1]],
            "translation": [-0.15, 0, 0],
        },
    }
    calls, updates = [], []

    class Storage:
        def download_folder(self, folder, target, selected, callback):
            calls.append((folder, [f["sourceId"] for f in selected]))
            target.mkdir(parents=True)
            for i, file in enumerate(selected):
                Image.new("RGB", (640, 480)).save(target / file["name"])
                callback(i + 1, len(selected), (i + 1) * 10, len(selected) * 10, file["name"], 0)
            return len(selected)

    files = [
        {"name": f"GOPR{i}.jpg", "size": 10, "cameraId": camera, "sourceId": f"{camera}-{i}"}
        for camera, sequence in [("left", [0, 1, 2]), ("right", [0, 2])]
        for i in sequence
    ]
    timestamps = {
        camera: {f["name"]: float(f["name"][4]) for f in files if f["cameraId"] == camera}
        for camera in ("left", "right")
    }
    job = {
        "files": files,
        "stereo": {
            "leftFolder": "camera-a",
            "rightFolder": "camera-b",
            "profile": profile,
            "timestamps": timestamps,
        },
    }
    dataset = tmp_path / "survey"
    assert stage_stereo_source(Storage(), job, dataset, lambda *a: updates.append(a)) == 5
    assert calls == [
        ("camera-a", ["left-0", "left-1", "left-2"]),
        ("camera-b", ["right-0", "right-2"]),
    ]
    assert updates[-1][:4] == (5, 5, 50, 50)
    frames = json.loads((dataset / "stereo/frames.json").read_text())
    assert [len(frame["images"]) for frame in frames] == [2, 1, 2]
