"""OpenCV-powered computer vision for intelligent crop and facecam detection."""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from rich.console import Console

from .ffmpeg import probe
from ..utils import validate_crop

console = Console()

try:
    import cv2  # type: ignore[import-untyped]
    import numpy as np  # type: ignore[import-untyped]
    _OPENCV_AVAILABLE = True
except ImportError:
    cv2 = None
    np = None
    _OPENCV_AVAILABLE = False


def has_opencv() -> bool:
    """Return True if OpenCV and NumPy are available."""
    return _OPENCV_AVAILABLE


@dataclass
class FacecamDetectionResult:
    """Detected facecam box and complementary gameplay region."""

    cam_crop: str
    game_crop: str
    confidence: float
    position: str  # "top-left", "top-right", "bottom-left", "bottom-right", "center"


def detect_facecam(
    video_path: Path,
    sample_duration: float = 15.0,
    num_samples: int = 6,
) -> FacecamDetectionResult | None:
    """Analyze video frames with OpenCV to automatically detect a streamer's facecam.

    Finds persistent face clusters in typical webcam corner zones. Expands the
    bounding box into a standard 16:9 or 4:3 webcam frame and returns validated
    ``(cam_crop, game_crop)`` strings for ``Layout.VERTICAL_STACKED``.

    Returns ``None`` if OpenCV is not installed or no persistent face is detected.
    """
    if not has_opencv():
        console.print(
            "[dim yellow]ℹ OpenCV not installed. Install with: pip install 'clipmania[smart]'[/]"
        )
        return None

    info = probe(video_path)
    width, height = info.width, info.height
    duration = info.duration or 30.0

    if width <= 0 or height <= 0:
        return None

    # Load Haar cascade face classifier
    cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
    face_cascade = cv2.CascadeClassifier(cascade_path)
    if face_cascade.empty():
        return None

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        return None

    # Sample timestamps evenly over sample_duration or clip length
    effective_duration = min(duration, sample_duration)
    step = effective_duration / max(1, num_samples)
    sample_times = [step * i for i in range(num_samples)]

    detected_faces: list[tuple[int, int, int, int]] = []

    try:
        for t in sample_times:
            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000.0)
            ret, frame = cap.read()
            if not ret or frame is None:
                continue

            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            # Min face size: at least 5% of height; max: 40% of height
            min_size = int(height * 0.05)
            max_size = int(height * 0.40)

            faces = face_cascade.detectMultiScale(
                gray,
                scaleFactor=1.15,
                minNeighbors=4,
                minSize=(min_size, min_size),
                maxSize=(max_size, max_size),
            )

            for (x, y, w, h) in faces:
                detected_faces.append((int(x), int(y), int(w), int(h)))
    finally:
        cap.release()

    if not detected_faces:
        return None

    # Group detected faces by spatial proximity to find persistent facecam
    # A webcam is typically static in a corner quadrant
    clusters: list[list[tuple[int, int, int, int]]] = []
    threshold_dist = min(width, height) * 0.15

    for face in detected_faces:
        fx, fy, fw, fh = face
        fcx, fcy = fx + fw / 2, fy + fh / 2
        assigned = False
        for cluster in clusters:
            # Average center of cluster
            ccx = sum(f[0] + f[2] / 2 for f in cluster) / len(cluster)
            ccy = sum(f[1] + f[3] / 2 for f in cluster) / len(cluster)
            if math.hypot(fcx - ccx, fcy - ccy) <= threshold_dist:
                cluster.append(face)
                assigned = True
                break
        if not assigned:
            clusters.append([face])

    # Choose the most frequent persistent cluster
    clusters.sort(key=len, reverse=True)
    best_cluster = clusters[0]

    # Require detection in at least 2 frames or 30% of samples if multiple samples
    if len(sample_times) > 2 and len(best_cluster) < 2:
        return None

    # Compute median face box in best cluster
    avg_x = sum(f[0] for f in best_cluster) // len(best_cluster)
    avg_y = sum(f[1] for f in best_cluster) // len(best_cluster)
    avg_w = sum(f[2] for f in best_cluster) // len(best_cluster)
    avg_h = sum(f[3] for f in best_cluster) // len(best_cluster)

    # Expand face box to standard webcam bounding box:
    # Give 40% headroom above, 80% torso below, 60% margins on sides
    cam_w = int(avg_w * 2.2)
    # Standard 16:9 webcam aspect ratio
    cam_h = int(cam_w * 9 / 16)
    if cam_h < int(avg_h * 1.8):
        cam_h = int(avg_h * 1.8)
        cam_w = int(cam_h * 16 / 9)

    cam_x = int(avg_x + avg_w / 2 - cam_w / 2)
    cam_y = int(avg_y + avg_h / 2 - cam_h * 0.45)

    # Clamp to video bounds
    cam_x = max(0, min(cam_x, width - cam_w))
    cam_y = max(0, min(cam_y, height - cam_h))
    cam_w = min(cam_w, width - cam_x)
    cam_h = min(cam_h, height - cam_y)

    # Ensure even dimensions for H.264
    cam_w = (cam_w // 2) * 2
    cam_h = (cam_h // 2) * 2
    cam_x = (cam_x // 2) * 2
    cam_y = (cam_y // 2) * 2

    # Determine quadrant position name
    is_left = (cam_x + cam_w / 2) < (width / 2)
    is_top = (cam_y + cam_h / 2) < (height / 2)
    pos = f"{'top' if is_top else 'bottom'}-{'left' if is_left else 'right'}"

    cam_crop = f"{cam_w}:{cam_h}:{cam_x}:{cam_y}"
    game_crop = f"{width}:{height}:0:0"

    validate_crop(cam_crop)
    validate_crop(game_crop)

    confidence = len(best_cluster) / max(1, len(sample_times))
    return FacecamDetectionResult(
        cam_crop=cam_crop,
        game_crop=game_crop,
        confidence=confidence,
        position=pos,
    )


def detect_speaker_x_center(
    video_path: Path,
    sample_duration: float = 10.0,
    num_samples: int = 5,
) -> int | None:
    """Return the median horizontal center of detected faces for vertical-crop centering."""
    if not has_opencv():
        return None

    info = probe(video_path)
    width, height = info.width, info.height
    duration = info.duration or 30.0

    cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
    face_cascade = cv2.CascadeClassifier(cascade_path)
    if face_cascade.empty():
        return None

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        return None

    step = min(duration, sample_duration) / max(1, num_samples)
    x_centers: list[int] = []

    try:
        for i in range(num_samples):
            cap.set(cv2.CAP_PROP_POS_MSEC, i * step * 1000.0)
            ret, frame = cap.read()
            if not ret or frame is None:
                continue

            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            faces = face_cascade.detectMultiScale(
                gray,
                scaleFactor=1.15,
                minNeighbors=4,
                minSize=(int(height * 0.08), int(height * 0.08)),
            )
            for (x, y, w, h) in faces:
                x_centers.append(int(x + w / 2))
    finally:
        cap.release()

    if not x_centers:
        return None

    x_centers.sort()
    median_x = x_centers[len(x_centers) // 2]
    return max(0, min(median_x, width))
