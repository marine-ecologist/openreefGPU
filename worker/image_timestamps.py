"""Capture-time EXIF editing without recompressing JPEG image data."""

from __future__ import annotations

import struct
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image


def replace_jpeg_exif(path: Path, exif: Image.Exif) -> None:
    """Replace only the Exif APP1 segment; preserve compressed pixels and other markers."""
    data = path.read_bytes()
    if not data.startswith(b"\xff\xd8"):
        raise ValueError(f"Not a JPEG: {path}")
    payload = exif.tobytes()
    if len(payload) + 2 > 65535:
        raise ValueError(f"EXIF metadata is too large: {path}")
    pieces = [b"\xff\xd8", b"\xff\xe1" + struct.pack(">H", len(payload) + 2) + payload]
    offset = 2
    while offset < len(data):
        start = offset
        if data[offset] != 255:
            raise ValueError(f"Invalid JPEG header: {path}")
        while offset < len(data) and data[offset] == 255:
            offset += 1
        marker = data[offset]
        offset += 1
        if marker in (0xDA, 0xD9):
            pieces.append(data[start:])
            break
        if marker == 0x01 or 0xD0 <= marker <= 0xD8:
            pieces.append(data[start:offset])
            continue
        length = struct.unpack(">H", data[offset : offset + 2])[0]
        end = offset + length
        if length < 2 or end > len(data):
            raise ValueError(f"Invalid JPEG segment: {path}")
        if not (marker == 0xE1 and data[offset + 2 : end].startswith(b"Exif\x00\x00")):
            pieces.append(data[start:end])
        offset = end
    path.write_bytes(b"".join(pieces))


def write_capture_time(path: Path, capture_time: datetime) -> None:
    if capture_time.tzinfo is None:
        raise ValueError("Capture time must include a timezone")
    capture_time = capture_time.astimezone(timezone.utc)
    with Image.open(path) as image:
        exif = image.getexif()
        details = dict(exif.get_ifd(34665)) if 34665 in exif else {}
    date = capture_time.strftime("%Y:%m:%d %H:%M:%S")
    subseconds = f"{capture_time.microsecond:06d}"
    exif[306] = date
    details.update(
        {
            36867: date,
            36868: date,
            36880: "+00:00",
            36881: "+00:00",
            36882: "+00:00",
            37520: subseconds,
            37521: subseconds,
            37522: subseconds,
        }
    )
    exif[34665] = details
    replace_jpeg_exif(path, exif)
