"""Pick a cover frame for each final reel.

Scans a ±2 s window around the reel midpoint and picks the frame with the
highest score = (smile + sharpness + face-found). Falls back to the midpoint
frame if no face is detected anywhere in the window.

Writes JPEGs to projects/<name>/7_covers/cover_NN.jpg.
"""
import argparse
import sys
from pathlib import Path

import cv2

from src.project import Project

ROOT = Path(__file__).resolve().parent.parent
YUNET_PATH = ROOT / "models" / "face_detection_yunet_2023mar.onnx"

SCAN_HALF_SEC = 2.0
SAMPLE_EVERY_N_FRAMES = 5
FACE_CONF = 0.7  # cover scoring tolerates lower-conf faces than reframe does
JPEG_QUALITY = 92


def _yunet_face(frame, detector):
    h, w = frame.shape[:2]
    detector.setInputSize((w, h))
    _, dets = detector.detect(frame)
    if dets is None or len(dets) == 0:
        return None
    return max(dets, key=lambda d: float(d[2]) * float(d[3]))


def _smile_count(face_gray, cascade):
    if face_gray.size == 0:
        return 0
    smiles = cascade.detectMultiScale(
        face_gray, scaleFactor=1.7, minNeighbors=22, minSize=(25, 25)
    )
    return len(smiles)


def _sharpness(img) -> float:
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def pick_cover(reel_path: Path, out_path: Path, detector, cascade) -> None:
    cap = cv2.VideoCapture(str(reel_path))
    fps = cap.get(cv2.CAP_PROP_FPS) or 24.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 0
    if total <= 0:
        cap.release()
        raise RuntimeError(f"empty video: {reel_path}")

    mid = total // 2
    half_n = int(SCAN_HALF_SEC * fps)
    start = max(0, mid - half_n)
    end = min(total - 1, mid + half_n)

    best_frame, best_score = None, -1.0
    cap.set(cv2.CAP_PROP_POS_FRAMES, start)
    idx = start
    while idx <= end:
        ok, frame = cap.read()
        if not ok:
            break
        if (idx - start) % SAMPLE_EVERY_N_FRAMES == 0:
            det = _yunet_face(frame, detector)
            if det is not None:
                x, y, w, h = (int(v) for v in det[:4])
                x, y = max(0, x), max(0, y)
                face = frame[y:y + h, x:x + w]
                if face.size > 0:
                    gray = cv2.cvtColor(face, cv2.COLOR_BGR2GRAY)
                    score = (
                        1.0                          # base reward for face found
                        + 2.0 * _smile_count(gray, cascade)
                        + 0.0005 * _sharpness(face)
                    )
                    if score > best_score:
                        best_score = score
                        best_frame = frame.copy()
        idx += 1
    cap.release()

    if best_frame is None:
        cap = cv2.VideoCapture(str(reel_path))
        cap.set(cv2.CAP_PROP_POS_FRAMES, mid)
        ok, best_frame = cap.read()
        cap.release()
        if not ok:
            raise RuntimeError(f"could not read midpoint frame from {reel_path}")

    cv2.imwrite(str(out_path), best_frame, [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    args = ap.parse_args()
    proj = Project(args.project)

    if not YUNET_PATH.exists():
        sys.exit(f"missing YuNet model: {YUNET_PATH}")
    detector = cv2.FaceDetectorYN.create(
        str(YUNET_PATH), "", (320, 320), FACE_CONF, 0.3, 50
    )
    cascade_xml = cv2.data.haarcascades + "haarcascade_smile.xml"
    cascade = cv2.CascadeClassifier(cascade_xml)
    if cascade.empty():
        sys.exit(f"missing smile cascade: {cascade_xml}")

    reels = sorted(proj.reels.glob("reel_*.mp4"))
    if not reels:
        print(f"[cover] no reels in {proj.reels}, did burn run?")
        return
    for reel in reels:
        out = proj.covers / reel.name.replace("reel_", "cover_").replace(".mp4", ".jpg")
        if out.exists():
            print(f"[cover] skip existing {out.name}")
            continue
        print(f"[cover] {reel.name} → {out.name}")
        pick_cover(reel, out, detector, cascade)
    print(f"[cover] done → {proj.covers}")


if __name__ == "__main__":
    main()
