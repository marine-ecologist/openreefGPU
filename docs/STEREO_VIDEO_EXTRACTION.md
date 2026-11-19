# How-to: synced still pairs from paired GoPro video

Status: how-to and method spec for inclusion in OpenReef. Reference implementation: CCMI scripts (`gpmf.py`, `scan.py`, and the 12-11-26 / 18-11-26 extraction runs).

## Summary

The two cameras' clocks are aligned with the **gyroscope telemetry** (GPMF) embedded in each MP4. Frames are then selected by **video presentation timestamps (PTS)**, using that alignment:

1. Extract the GYRO stream from each video.
2. Cross-correlate the two |ω| signals to get the offset model L(t) = L0 + d·t.
3. For each reference frame, compute the matching fractional frame in the other camera, and take the nearest frame.
4. Decode those exact frames, and write them with capture times on a common clock.

Three other sync sources are rejected:

| source | why not |
|---|---|
| File or creation timestamps | 1 s resolution, and the camera clocks are not synced to each other |
| Shake-to-start, QR start or button start | the start offset is unknown and different every time. Measured: 196 ms (18-11-26 calibration), 125 ms (seagrass), and -68, +62 and +8 ms on the manual-start dives of 12-11-26 |
| Audio | latency differed between the two cameras by up to about 38 ms |

The gyro sync reaches correlation r ≥ 0.999 with sub-millisecond peak precision. The rig is rigid, so both cameras feel the same rotation. Using the magnitude |ω| makes the comparison independent of how each camera is mounted, because the norm of the angular velocity is the same in any rotated frame.

## Camera settings

- Both cameras identical: same mode, resolution and frame rate, 5.3K 59.94 fps Wide.
- **HyperSmooth OFF.** Stabilisation crops and rotates each frame independently, which breaks the fixed geometry between the two cameras.
- GPMF telemetry ON (the default).
- Start both cameras before the dive. The extra footage at the start doesn't matter, as long as both recordings overlap during motion.
- Optional but recommended: shake or twist the rig sharply for 2 to 3 s at the start and end. This gives an unambiguous correlation peak, and the end shake pins the drift.

## Step 1. Extract the gyro

Find the GPMF stream. It's the data track whose handler name is `GoPro MET`:

```bash
ffprobe -v error -show_entries stream=index,codec_tag_string:stream_tags=handler_name X.MP4
# typically stream 3, codec tag 'gpmd'
ffmpeg -v error -i X.MP4 -map 0:3 -c copy -f rawvideo X_gpmf.bin
```

Parse the KLV structure:
- Each entry is 4-byte FourCC, 1-byte type, 1-byte size, 2-byte repeat, then the payload, padded to 4 bytes.
- Recurse into nested containers (type `\0`).
- Read the `SCAL` that precedes each `GYRO`, and divide the int16 triplets by it to get rad/s.
- The HERO13 gyro rate is about 198 Hz.

Timestamp the samples from the metadata track itself, not from a nominal rate:
- Each GPMF payload is one MP4 sample, about 1.001 s long, with a start time and duration from the track's sample table (`ffprobe -show_packets -select_streams 3` gives pts_time and duration_time).
- Spread each payload's samples evenly across its duration.

Any fixed offset between the telemetry clock and the video clock inside a camera is common to both cameras when they're the same model with the same settings. It cancels in the relative sync.

Also record `SROT` (rolling-shutter readout, 15.45 ms at 5.3K60) and `EISE` (HyperSmooth on or off). Refuse to run, or warn, if EISE is on.

## Step 2. Fit the offset model

1. Compute |ω| for each camera and resample to 1 kHz with linear interpolation.
2. High-pass with a 0.1 Hz cutoff, then normalise each signal.
3. **Coarse:** cross-correlate the full overlap with FFT, searching ±10 s, to get L0.
4. **Fine:** in windows (20 s long, every 30 s), cross-correlate within ±50 ms of the coarse estimate. Fit a parabola to the peak for a sub-sample estimate. Record r for each window.
5. Keep windows with r ≥ 0.99, which drops still periods with no signal. Fit L(t) = L0 + d·t with a robust line fit (Huber or Theil-Sen) over the window centres.
6. QC:
   - the residual RMS of the window lags should be under 0.5 ms
   - at least 5 good windows, spread across the recording
   - report L0, d, the window count and the minimum r

Convention: an event at reference-video time t appears in the other video at t − L(t).

The two camera clocks drift slightly apart:
- 18-11-26 seagrass: L0 = 125 ms, drift 2 ms per 240 s, so 127 ms by the end.
- Over a 20-minute dive, that drift is about 10 ms. Ignoring it would cost more than a full frame period of error at 60 fps by the end of the dive.

## Step 3. Choose frames and map them

- **Reference camera.** Pick one camera as the time reference, conventionally the OpenReef left camera.
- **Sampling.** Choose reference frames at the required interval, for example 1 Hz for photogrammetry. Anchor them on keyframes when using seek extraction, because it's faster.
- **Frame times** come from PTS, never from n/60. HERO13 records at 59.94 fps (60000/1001), and a nominal-rate assumption drifts by 1 frame every 16.7 s.
- **Mapping.** For reference frame n with time t_ref:

      f_other = (t_ref − L(t_ref)) · fps_other      (fractional frame index)
      k       = round(f_other)
      residual_ms = (f_other − k) / fps_other · 1000

- **Residual.** The pairing error is at most half a frame period, 8.3 ms at 60 fps. It changes only slowly through the drift, so a recording with an offset of x.5 frames sits near the worst case throughout. The seagrass pair was B ≈ A − 7.49 frames, which is the worst case.
- **Effect on stills:**
  - a translation of v · Δt: at 0.3 m/s and 8 ms, 2.5 mm
  - a rotation of ω · Δt: at 0.33 rad/s, about 0.15°, which is about 5 mm at 2 m
- **When the residual matters.** This is fine for SfM with independent cameras and pair scale bars. It is not fine for direct stereo measurement. For fish sizing, interpolate between the bracketing frames using rolling-shutter row timing (see `STEREO_VALIDATION.md`) rather than taking the nearest frame.

## Step 4. Decode exact frames

There are two methods. Both must check that the decoded frame's PTS equals the target PTS.

**A. Sequential decode (recommended for batch):** decode with PyAV or an ffmpeg pipe in a single pass, and keep the frames whose index is in the target set. This is exact and needs no seeking, and it's efficient when you want one or more frames per second.

**B. Seek (for sparse frames):**

```bash
# keyframe target: seek to the exact keyframe time
ffmpeg -v error -ss T_EXACT -i X.MP4 -frames:v 1 -q:v 2 out.jpg
# non-keyframe target: seek slightly before, so the decoder lands on the target
ffmpeg -v error -ss $(T - 0.25/fps) -i X.MP4 -frames:v 1 -q:v 2 out.jpg
```

- Put `-ss` before `-i` for fast seeking.
- On ffmpeg 4.4, use `-vsync 0`; `-fps_mode` doesn't exist in that version.
- Don't seek to a time just before a keyframe. The decoder would decode the whole previous group of pictures for nothing.
- Keyframes fall every 1.001 s at 5.3K60.

Write JPEG at quality 2 (`-q:v 2`) or better, or PNG or TIFF for archive. Keep full resolution, and don't scale or crop.

## Step 5. Write outputs

```
<site>/
  left/  <site>_00001.jpg ...
  right/ <site>_00001.jpg ...
  pairs.csv
  sync.json
  timestamps.json
```

- **pairs.csv:** pair, file, frame_left, frame_right, time_left_s, time_right_s, sync_residual_ms.
- **sync.json:**
  - source files and reference camera
  - L0, d, window count, minimum r, residual RMS
  - fps, SROT, EISE
- **EXIF on each JPEG:** DateTimeOriginal + SubSecTimeOriginal on the common reference clock. Set both images of a pair to the reference time, and record the true right-camera capture time in pairs.csv. Use `io/image_timestamps.replace_jpeg_exif`.
- **timestamps.json:** the existing OpenReef timestamps format, for `pair_captures`. Its 30 ms tolerance comfortably exceeds the 8.3 ms worst-case residual.

Name folders by role (left and right), and record which physical camera is which in sync.json. In the CCMI rig, left = B and right = A.

## Step 6. Verify

1. **Image-motion check:** compute frame-to-frame optical-flow magnitude for each camera, and cross-correlate it like the gyro. It should give the same lag to within 1 frame. A mismatch means the gyro or video clock mapping is wrong.
2. **Spot check:** at a sharp event (a fin kick or a tag passing the frame edge), view the paired frames side by side.
3. **Run the stereo profile** on a few pairs. Median epipolar residual on matched features should be under about 1 px. A rising residual with |ω| means the sync is off.

## Using the pairs

- **Metashape:** use independent cameras (single cameras, all images in one chunk) plus camera-pair scale bars at the calibrated baseline. If HyperSmooth was off and the calibration is good, a multi-camera rig with fixed offsets is also valid.
- **OpenReef:** import with the stereo profile. Pairing uses EXIF time or timestamps.json, and the profile fixes intrinsics and the rig in COLMAP. COLMAP has no rolling-shutter model, so keep motion slow during capture, about 0.3 m/s and gentle turns.
- **Fish or dumbbell measurement:** use rolling-shutter interpolation, not still pairs (see `STEREO_VALIDATION.md`).

## Reference results (CCMI)

| recording | start | L0 | drift | min r |
|---|---|---|---|---|
| 12-11-26 BoobyPass | manual | -68.5 ms | - | 0.999 |
| 12-11-26 BushGardens | manual | +62 ms | - | 0.999 |
| 12-11-26 Coconut | manual | +8 ms | - | 0.999 |
| 18-11-26 calibration | shake | 196 ms (B = A + 12 fr) | - | ≥ 0.999 |
| 18-11-26 seagrass | shake | 125 ms | +2 ms / 240 s | ≥ 0.999 |

The 12-11-26 dives were HyperSmooth ON at 29.97 fps, with mixed resolutions (A 5.3K, B 4K). The sync was valid, but the fixed rig geometry was not.
