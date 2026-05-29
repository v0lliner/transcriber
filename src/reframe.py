"""Reframe a 16:9 clip to 9:16 (1080x1920) with a tight 3.2× crop that CUTS
between speakers — interview-style hard jump cuts, no panning.

Detection is a hybrid:
  - YuNet (OpenCV FaceDetectorYN) finds + tracks faces across the WHOLE frame.
    It reliably catches the small, couch-distance faces in podcast footage that
    MediaPipe's selfie-tuned detector misses.
  - MediaPipe Face Landmarker runs on each YuNet face CROP to read a calibrated
    mouth-open signal (jawOpen blendshape). If MediaPipe finds no face in a crop,
    that box was a hand / false positive → rejected for free.

Pipeline per clip:
  1. Sample frames at SAMPLE_EVERY_N_FRAMES. YuNet → candidate boxes. MediaPipe
     on each crop → confirm face + jawOpen mouth-openness.
  2. Extract per-sample audio RMS from the clip's audio track.
  3. Greedy-track faces across samples by nearest-neighbour on box center.
  4. Speaking score per track = mean frame-to-frame MOTION of the mouth-region
     patch over a short window (talking = the mouth moves), *gated by audio energy*.
     jawOpen blendshapes are unreliable on small, side-facing couch faces, so pixel
     motion is used instead. No face-size prior. Hysteresis via sticky ratio + dwell.
  5. Group consecutive samples by chosen speaker into SEGMENTS. Each segment gets
     ONE fixed crop center (median face position). Camera CUTS between segments —
     it never pans, and stays locked within a segment.
  6. Crop each frame to its segment's fixed window, scale to 1080×1920 via ffmpeg.
"""
import argparse
import json
import math
import subprocess
import tempfile
import wave
from pathlib import Path

import cv2
import numpy as np
import mediapipe as mp
from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python import vision as mp_vision

from src.project import Project

ROOT = Path(__file__).resolve().parent.parent
YUNET_PATH = ROOT / "models" / "face_detection_yunet_2023mar.onnx"
LANDMARKER_PATH = ROOT / "models" / "face_landmarker.task"

OUT_W, OUT_H = 1080, 1920
ZOOM = 3.2
SAMPLE_EVERY_N_FRAMES = 3

# --- detection ---
FACE_CONF = 0.6                    # YuNet confidence (low — MediaPipe confirms)
NMS_THRESHOLD = 0.3
TOPK = 50
MIN_FACE_FRAC = 0.02               # min face bbox width as fraction of frame width
CROP_PAD_FRAC = 0.4                # padding around YuNet box before MediaPipe
MOUTH_PATCH_W, MOUTH_PATCH_H = 48, 32   # normalized mouth-region patch for motion signal

# --- active speaker / cuts ---
SPEAKER_WINDOW_SEC = 0.5           # window over which mouth-motion variance is measured
SPEAKER_STICKY_RATIO = 1.8         # challenger must beat current by 80% to take the cut
MIN_DWELL_SEC = 1.5                # once chosen, a speaker holds the shot at least this long
MIN_CUT_SEC = 1.0                  # segments shorter than this get merged away (no flash cuts)
TRACK_MERGE_DIST = 0.15            # max norm-distance to extend an existing track
FALLBACK_RECENCY_SEC = 1.0         # fallback only considers tracks active within ±this
SILENCE_RMS = 0.012                # RMS below this = silence, hold prev speaker
MIN_TRACK_SEC = 1.0                # drop tracks shorter than this (spurious detections)

# Where the face center sits within the 9:16 output, as a fraction from the top.
FACE_Y_FRAC = 0.30

# MediaPipe face-mesh inner-lip landmarks (upper / lower) for the fallback gap signal.
LIP_UPPER_IDX = 13
LIP_LOWER_IDX = 14


def _make_detector(src_w: int, src_h: int):
    det = cv2.FaceDetectorYN.create(
        str(YUNET_PATH), "", (320, 320), FACE_CONF, NMS_THRESHOLD, TOPK
    )
    det.setInputSize((src_w, src_h))
    return det


def _make_landmarker():
    base = mp_python.BaseOptions(model_asset_path=str(LANDMARKER_PATH))
    opts = mp_vision.FaceLandmarkerOptions(
        base_options=base,
        running_mode=mp_vision.RunningMode.IMAGE,   # independent per-crop detections
        num_faces=1,
        output_face_blendshapes=True,
        min_face_detection_confidence=0.3,
    )
    return mp_vision.FaceLandmarker.create_from_options(opts)


def _crop_mouth_open(landmarker, crop_bgr) -> float | None:
    """Run MediaPipe on a single face crop. Return 0..1 mouth-openness, or None
    if no face is found in the crop (→ reject the box as non-face)."""
    if crop_bgr.size == 0:
        return None
    rgb = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2RGB)
    res = landmarker.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb))
    if not res.face_landmarks:
        return None
    if res.face_blendshapes:
        for cat in res.face_blendshapes[0]:
            if cat.category_name == "jawOpen":
                return float(cat.score)
    # Fallback: inner-lip vertical gap normalized by face bbox height
    lmks = res.face_landmarks[0]
    try:
        up, lo = lmks[LIP_UPPER_IDX], lmks[LIP_LOWER_IDX]
        ys = [lm.y for lm in lmks]
        face_h = max(1e-6, max(ys) - min(ys))
        return float(min(1.0, abs(lo.y - up.y) / face_h * 3.0))
    except (IndexError, ValueError):
        return 0.0


def _mouth_patch(frame, x: int, y: int, w: int, h: int, src_w: int, src_h: int):
    """Normalized grayscale patch of the lower-face / mouth region of a YuNet box.

    jawOpen blendshapes are unreliable on the small, side-facing couch faces in
    podcast footage. Frame-to-frame change in this patch (computed per track) is a
    far more robust 'is this person talking' signal — the speaker's mouth moves."""
    my0 = max(0, y + int(0.55 * h))
    my1 = min(src_h, y + h)
    mx0 = max(0, x + int(0.15 * w))
    mx1 = min(src_w, x + int(0.85 * w))
    patch = frame[my0:my1, mx0:mx1]
    if patch.size == 0:
        return None
    g = cv2.resize(patch, (MOUTH_PATCH_W, MOUTH_PATCH_H))
    return cv2.cvtColor(g, cv2.COLOR_BGR2GRAY).astype(np.float32)


def _extract_audio_rms(clip_path: Path, sample_times: list[float]) -> dict[float, float]:
    """Decode clip audio to mono 16 kHz WAV via ffmpeg, return RMS at each sample time.

    Returns dict {t: rms_0_to_1}. Empty dict on failure (audio gating becomes a no-op).
    """
    if not sample_times:
        return {}
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        wav_path = Path(tmp.name)
    try:
        subprocess.run([
            "ffmpeg", "-y", "-i", str(clip_path),
            "-vn", "-ac", "1", "-ar", "16000",
            "-c:a", "pcm_s16le",
            str(wav_path),
        ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        with wave.open(str(wav_path), "rb") as wf:
            sr = wf.getframerate()
            n_frames = wf.getnframes()
            raw = wf.readframes(n_frames)
        samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
        if samples.size == 0:
            return {}
        half_win = max(1, int(SPEAKER_WINDOW_SEC * sr / 2))
        out = {}
        for t in sample_times:
            center = int(t * sr)
            a = max(0, center - half_win)
            b = min(samples.size, center + half_win)
            if b <= a:
                out[t] = 0.0
                continue
            chunk = samples[a:b]
            out[t] = float(np.sqrt(np.mean(chunk * chunk)))
        return out
    except (subprocess.CalledProcessError, wave.Error, FileNotFoundError):
        return {}
    finally:
        wav_path.unlink(missing_ok=True)


def _detect_tracks(video_path: Path):
    cap = cv2.VideoCapture(str(video_path))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    src_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    src_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

    detector = _make_detector(src_w, src_h)
    landmarker = _make_landmarker()

    # list of (t, faces=[(cx_norm, cy_norm, box_area_norm, mouth_open)])
    samples = []
    frame_idx = 0
    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                break
            if frame_idx % SAMPLE_EVERY_N_FRAMES == 0:
                t = frame_idx / fps
                _, dets = detector.detect(frame)
                faces = []
                if dets is not None:
                    for d in dets:
                        x, y, w, h = [int(round(v)) for v in d[:4]]
                        if w < MIN_FACE_FRAC * src_w or h <= 0:
                            continue
                        aspect = h / w
                        if aspect < 0.6 or aspect > 2.0:
                            continue
                        pad = int(CROP_PAD_FRAC * w)
                        x0 = max(0, x - pad)
                        y0 = max(0, y - pad)
                        x1 = min(src_w, x + w + pad)
                        y1 = min(src_h, y + h + pad)
                        mouth = _crop_mouth_open(landmarker, frame[y0:y1, x0:x1])
                        if mouth is None:
                            continue  # MediaPipe found no face here → not a face
                        patch = _mouth_patch(frame, x, y, w, h, src_w, src_h)
                        cx = (x + w / 2) / src_w
                        cy = (y + h / 2) / src_h
                        box_area = (w * h) / (src_w * src_h)
                        faces.append((float(cx), float(cy), float(box_area), float(mouth), patch))
                samples.append((t, faces))
            frame_idx += 1
    finally:
        cap.release()
        landmarker.close()

    # Greedy track building. Wider merge distance so a head turn doesn't fragment a track.
    tracks = []  # each track: list of (t, cx, cy, box_area, mouth_open, patch)
    for t, faces in samples:
        used = [False] * len(faces)
        for tr in tracks:
            last = tr[-1]
            best, best_d = -1, TRACK_MERGE_DIST
            for i, (cx, cy, *_rest) in enumerate(faces):
                if used[i]:
                    continue
                d = math.hypot(cx - last[1], cy - last[2])
                if d < best_d:
                    best, best_d = i, d
            if best >= 0:
                cx, cy, box, mouth, patch = faces[best]
                tr.append((t, cx, cy, box, mouth, patch))
                used[best] = True
        for i, (cx, cy, box, mouth, patch) in enumerate(faces):
            if not used[i]:
                tracks.append([(t, cx, cy, box, mouth, patch)])

    min_track_len = max(3, int(MIN_TRACK_SEC * fps / SAMPLE_EVERY_N_FRAMES))
    tracks = [tr for tr in tracks if len(tr) >= min_track_len]

    # Per-track speaking signal = frame-to-frame motion of the mouth patch. Replace
    # the (unreliable) jawOpen field with this motion so downstream code is unchanged.
    # Returned track rows: (t, cx, cy, box_area, mouth_motion).
    out_tracks = []
    for tr in tracks:
        seq = sorted(tr, key=lambda r: r[0])
        rows, prev = [], None
        for (t, cx, cy, box, _mouth, patch) in seq:
            if prev is not None and patch is not None and patch.shape == prev.shape:
                m = float(np.mean(np.abs(patch - prev)))
            else:
                m = 0.0
            if patch is not None:
                prev = patch
            rows.append((t, cx, cy, box, m))
        if len(rows) >= 2:  # first sample has no predecessor — inherit the next motion
            rows[0] = (rows[0][0], rows[0][1], rows[0][2], rows[0][3], rows[1][4])
        out_tracks.append(rows)

    return samples, out_tracks, src_w, src_h, fps, total_frames


def _pick_active_speaker(samples, tracks, audio_rms):
    """For each sample t, return (t, cx, cy, track_idx) — track_idx = -1 = fallback.

    Speaking score = mean mouth-region motion over ±SPEAKER_WINDOW_SEC, scaled by
    audio RMS. Hysteresis (sticky ratio + min dwell) keeps cuts from flickering.
    """
    if not tracks:
        return [(t, 0.5, 0.5, -1) for t, _ in samples]

    track_seqs = [sorted(tr, key=lambda r: r[0]) for tr in tracks]
    timestamps = [t for t, _ in samples]

    out = []
    current = None
    last_switch_t = -1e9
    for t in timestamps:
        rms = audio_rms.get(t, 1.0)  # if audio extraction failed, treat as loud (no gate)

        scores = {}
        for ti, seq in enumerate(track_seqs):
            window = [r[4] for r in seq if abs(r[0] - t) <= SPEAKER_WINDOW_SEC]
            if len(window) < 2:
                continue
            # r[4] is mouth-region motion (talking = mouth moves). Mean over the
            # window, audio-gated. NO face-size prior — that parked the camera on the
            # bigger/closer face (the listener) when the mouth signal was weak.
            speak = float(np.mean(window))
            scores[ti] = speak * (rms if rms > 0 else 1.0)

        if rms < SILENCE_RMS and current is not None:
            best_track = current             # hold current speaker through silence
        elif not scores:
            best_track = None
        else:
            best_track = max(scores, key=scores.get)
            if current is not None and current in scores:
                in_dwell = (t - last_switch_t) < MIN_DWELL_SEC
                ratio = SPEAKER_STICKY_RATIO * (1.5 if in_dwell else 1.0)
                if scores[best_track] < scores[current] * ratio:
                    best_track = current
            if best_track != current:
                last_switch_t = t
            current = best_track

        if best_track is None:
            best_box, best_pos = -1.0, (0.5, 0.5)
            for seq in track_seqs:
                if not seq:
                    continue
                rec = min(seq, key=lambda r: abs(r[0] - t))
                if abs(rec[0] - t) > FALLBACK_RECENCY_SEC:
                    continue
                if rec[3] > best_box:
                    best_box = rec[3]
                    best_pos = (rec[1], rec[2])
            out.append((t, best_pos[0], best_pos[1], -1))
        else:
            seq = track_seqs[best_track]
            rec = min(seq, key=lambda r: abs(r[0] - t))
            out.append((t, rec[1], rec[2], best_track))
    return out


def _build_segments(active, track_seqs):
    """Collapse the per-sample active-speaker list into locked-shot SEGMENTS.

    Each segment is (start_t, end_t, cx, cy) where (cx, cy) is the MEDIAN face
    position of that speaker over the segment — one fixed framing, no panning.
    A -1 (fallback) sample inherits the previous segment's speaker so the camera
    stays put rather than darting to a dead track.
    """
    if not active:
        return []

    resolved = []
    prev_idx = None
    for (t, cx, cy, idx) in active:
        if idx < 0 and prev_idx is not None:
            resolved.append((t, prev_idx))
        else:
            resolved.append((t, idx))
            if idx >= 0:
                prev_idx = idx

    runs = []  # (start_t, end_t, idx)
    for (t, idx) in resolved:
        if runs and runs[-1][2] == idx:
            runs[-1] = (runs[-1][0], t, idx)
        else:
            runs.append((t, t, idx))

    # Merge runs shorter than MIN_CUT_SEC into the previous run (kill flash cuts).
    merged = []
    for run in runs:
        dur = run[1] - run[0]
        if merged and dur < MIN_CUT_SEC:
            merged[-1] = [merged[-1][0], run[1], merged[-1][2]]
        else:
            merged.append([run[0], run[1], run[2]])
    # Coalesce neighbours that ended up with the same speaker after merging.
    coalesced = []
    for run in merged:
        if coalesced and coalesced[-1][2] == run[2]:
            coalesced[-1][1] = run[1]
        else:
            coalesced.append(list(run))

    segments = []
    for (start_t, end_t, idx) in coalesced:
        if 0 <= idx < len(track_seqs):
            seq = track_seqs[idx]
            pts = [(r[1], r[2]) for r in seq if start_t - 0.2 <= r[0] <= end_t + 0.2]
            if not pts:
                pts = [(r[1], r[2]) for r in seq]
            cx = float(np.median([p[0] for p in pts]))
            cy = float(np.median([p[1] for p in pts]))
        else:
            cx, cy = 0.5, 0.5
        segments.append((start_t, end_t, cx, cy))
    return segments


def _per_frame_xy(segments, total_frames, fps, src_w, src_h, crop_w, crop_h):
    """Step-function per-frame top-lefts from locked segments — hard cuts, no interp."""
    max_x = src_w - crop_w
    max_y = src_h - crop_h

    def center_to_xy(cx, cy):
        x = int(round(cx * src_w - crop_w / 2))
        y = int(round(cy * src_h - crop_h * FACE_Y_FRAC))
        x = max(0, min(max_x, x))
        y = max(0, min(max_y, y))
        return x, y

    if not segments:
        x0, y0 = center_to_xy(0.5, 0.5)
        return [x0] * total_frames, [y0] * total_frames

    seg_starts = [s[0] for s in segments]
    xs = [0] * total_frames
    ys = [0] * total_frames
    si = 0
    for fi in range(total_frames):
        t = fi / fps
        while si + 1 < len(segments) and t >= seg_starts[si + 1]:
            si += 1
        x, y = center_to_xy(segments[si][2], segments[si][3])
        xs[fi] = x
        ys[fi] = y
    return xs, ys


def reframe(in_clip: Path, out_clip: Path, track_json: Path, debug: bool = False) -> None:
    print(f"[reframe] analyze {in_clip.name}")
    samples, tracks, src_w, src_h, fps, total_frames = _detect_tracks(in_clip)
    print(f"[reframe]   {len(tracks)} tracks, {total_frames} frames @ {fps:.2f}fps")

    sample_times = [t for t, _ in samples]
    audio_rms = _extract_audio_rms(in_clip, sample_times)
    if audio_rms:
        vals = list(audio_rms.values())
        print(f"[reframe]   audio RMS: min={min(vals):.3f} max={max(vals):.3f} "
              f"mean={sum(vals) / len(vals):.3f}")
    else:
        print("[reframe]   audio RMS extraction failed → gating disabled")

    active = _pick_active_speaker(samples, tracks, audio_rms)
    track_seqs = [sorted(tr, key=lambda r: r[0]) for tr in tracks]
    segments = _build_segments(active, track_seqs)

    crop_w = int(round(OUT_W / ZOOM))
    crop_h = int(round(OUT_H / ZOOM))
    crop_w = min(crop_w, src_w)
    crop_h = min(crop_h, src_h)
    if crop_w % 2:
        crop_w += 1
    if crop_h % 2:
        crop_h += 1

    per_frame_x, per_frame_y = _per_frame_xy(
        segments, total_frames, fps, src_w, src_h, crop_w, crop_h
    )

    n_cuts = max(0, len(segments) - 1)
    track_json.write_text(json.dumps({
        "src_w": src_w, "src_h": src_h, "fps": fps,
        "crop_w": crop_w, "crop_h": crop_h,
        "n_tracks": len(tracks),
        "n_samples": len(samples),
        "n_segments": len(segments),
        "cuts": n_cuts,
        "segments": [
            {"start": round(s[0], 2), "end": round(s[1], 2),
             "cx": round(s[2], 3), "cy": round(s[3], 3)}
            for s in segments
        ],
        "audio_rms_available": bool(audio_rms),
        "config": {
            "ZOOM": ZOOM, "FACE_Y_FRAC": FACE_Y_FRAC,
            "SPEAKER_WINDOW_SEC": SPEAKER_WINDOW_SEC,
            "SPEAKER_STICKY_RATIO": SPEAKER_STICKY_RATIO,
            "MIN_DWELL_SEC": MIN_DWELL_SEC,
            "MIN_CUT_SEC": MIN_CUT_SEC,
            "TRACK_MERGE_DIST": TRACK_MERGE_DIST,
            "SILENCE_RMS": SILENCE_RMS,
            "tracker": "yunet+mediapipe_jawopen_jumpcut",
        },
    }, indent=2), encoding="utf-8")
    print(f"[reframe]   {len(segments)} segments → {n_cuts} hard cuts over {len(samples)} samples")

    if debug:
        _write_debug_overlay(in_clip, track_json.with_suffix(".debug.mp4"),
                             samples, tracks, active, audio_rms,
                             per_frame_x, per_frame_y, crop_w, crop_h,
                             src_w, src_h, fps, total_frames)

    cap = cv2.VideoCapture(str(in_clip))
    # Pipe source-resolution crops to ffmpeg; it denoises at native res then
    # Lanczos-upscales to 1080x1920 (sharper than OpenCV's upscale).
    ff = subprocess.Popen([
        "ffmpeg", "-y",
        "-f", "rawvideo", "-pix_fmt", "bgr24",
        "-s", f"{crop_w}x{crop_h}",
        "-r", f"{fps}",
        "-i", "-",
        "-i", str(in_clip),
        "-map", "0:v:0", "-map", "1:a:0",
        "-vf", f"hqdn3d=4:3:6:4.5,scale={OUT_W}:{OUT_H}:flags=lanczos+accurate_rnd",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        "-shortest",
        "-movflags", "+faststart",
        str(out_clip),
    ], stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    frame_idx = 0
    try:
        while True:
            ret, frame = cap.read()
            if not ret or frame_idx >= total_frames:
                break
            x = per_frame_x[frame_idx] if frame_idx < len(per_frame_x) else per_frame_x[-1]
            y = per_frame_y[frame_idx] if frame_idx < len(per_frame_y) else per_frame_y[-1]
            crop = frame[y:y + crop_h, x:x + crop_w]
            if crop.shape[0] != crop_h or crop.shape[1] != crop_w:
                pad_h = max(0, crop_h - crop.shape[0])
                pad_w = max(0, crop_w - crop.shape[1])
                if pad_h or pad_w:
                    crop = cv2.copyMakeBorder(crop, 0, pad_h, 0, pad_w, cv2.BORDER_REPLICATE)
            ff.stdin.write(crop.tobytes())
            frame_idx += 1
    finally:
        cap.release()
        if ff.stdin:
            ff.stdin.close()
        ff.wait()
    print(f"[reframe]   wrote {out_clip.name}")


def _write_debug_overlay(in_clip: Path, out_path: Path, samples, tracks, active,
                         audio_rms, per_frame_x, per_frame_y, crop_w, crop_h,
                         src_w, src_h, fps, total_frames) -> None:
    """Half-res annotated source: red = all faces (+ jawOpen value), green =
    active speaker, yellow = crop window. HUD = time, RMS, active track idx."""
    print(f"[reframe]   writing debug overlay → {out_path.name}")
    sample_faces = {t: faces for t, faces in samples}
    active_by_t = {t: idx for t, _cx, _cy, idx in active}
    sample_times_sorted = sorted(sample_faces.keys())

    def nearest_sample_t(t):
        if not sample_times_sorted:
            return None
        i = min(range(len(sample_times_sorted)),
                key=lambda k: abs(sample_times_sorted[k] - t))
        return sample_times_sorted[i]

    dbg_w = src_w // 2 + (src_w // 2) % 2
    dbg_h = src_h // 2 + (src_h // 2) % 2

    cap = cv2.VideoCapture(str(in_clip))
    ff = subprocess.Popen([
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "rawvideo", "-pix_fmt", "bgr24",
        "-s", f"{dbg_w}x{dbg_h}", "-r", f"{fps}", "-i", "-",
        "-i", str(in_clip), "-map", "0:v:0", "-map", "1:a:0",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "96k", "-shortest",
        str(out_path),
    ], stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    frame_idx = 0
    try:
        while True:
            ret, frame = cap.read()
            if not ret or frame_idx >= total_frames:
                break
            t = frame_idx / fps
            tn = nearest_sample_t(t)
            active_idx = active_by_t.get(tn, -1) if tn is not None else -1
            faces = sample_faces.get(tn, []) if tn is not None else []
            for (cx, cy, box, mouth, *_rest) in faces:
                bw = int(math.sqrt(box * src_w * src_h * (16 / 9)))
                bh = int(bw * 0.9)
                x0 = int(cx * src_w - bw / 2)
                y0 = int(cy * src_h - bh / 2)
                cv2.rectangle(frame, (x0, y0), (x0 + bw, y0 + bh), (0, 0, 200), 2)
                cv2.putText(frame, f"{mouth:.2f}", (x0, max(20, y0 - 8)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 200), 2, cv2.LINE_AA)
            if active_idx >= 0 and active_idx < len(tracks):
                seq = sorted(tracks[active_idx], key=lambda r: r[0])
                rec = min(seq, key=lambda r: abs(r[0] - t))
                if abs(rec[0] - t) < 0.5:
                    fx = int(rec[1] * src_w)
                    fy = int(rec[2] * src_h)
                    cv2.circle(frame, (fx, fy), 40, (0, 220, 0), 4)
            x = per_frame_x[frame_idx] if frame_idx < len(per_frame_x) else per_frame_x[-1]
            y = per_frame_y[frame_idx] if frame_idx < len(per_frame_y) else per_frame_y[-1]
            cv2.rectangle(frame, (x, y), (x + crop_w, y + crop_h), (0, 220, 220), 3)
            rms = audio_rms.get(tn, 0.0) if tn is not None else 0.0
            hud = f"t={t:5.2f}s  rms={rms:.3f}  active={active_idx}"
            cv2.putText(frame, hud, (20, 50), cv2.FONT_HERSHEY_SIMPLEX,
                        1.1, (0, 0, 0), 5, cv2.LINE_AA)
            cv2.putText(frame, hud, (20, 50), cv2.FONT_HERSHEY_SIMPLEX,
                        1.1, (255, 255, 255), 2, cv2.LINE_AA)
            bar_w = min(400, max(0, int(rms * 4000)))
            cv2.rectangle(frame, (20, 70), (20 + 400, 90), (50, 50, 50), -1)
            cv2.rectangle(frame, (20, 70), (20 + bar_w, 90), (0, 200, 0), -1)
            small = cv2.resize(frame, (dbg_w, dbg_h), interpolation=cv2.INTER_AREA)
            ff.stdin.write(small.tobytes())
            frame_idx += 1
    finally:
        cap.release()
        if ff.stdin:
            ff.stdin.close()
        ff.wait()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default="my_podcast")
    ap.add_argument("--force", action="store_true",
                    help="Overwrite existing reframed clips")
    ap.add_argument("--debug", action="store_true",
                    help="Also write an annotated debug overlay video per clip")
    ap.add_argument("--only", default=None,
                    help="Reframe only this clip stem (e.g. clip_01)")
    args = ap.parse_args()

    proj = Project(args.project)
    clips = sorted(proj.clips_raw.glob("clip_*.mp4"))
    if args.only:
        clips = [c for c in clips if c.stem == args.only]
    if not clips:
        print(f"[reframe] no clips in {proj.clips_raw}")
        return

    for clip in clips:
        out = proj.clips_reframed / clip.name
        track = proj.clips_reframed / (clip.stem + ".track.json")
        if out.exists() and not args.force:
            print(f"[reframe] skip existing {out.name} (use --force to overwrite)")
            continue
        reframe(clip, out, track, debug=args.debug)

    print(f"[reframe] done → {proj.clips_reframed}")


if __name__ == "__main__":
    main()
