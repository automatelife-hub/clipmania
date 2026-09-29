"""Intelligent auto-cropping and region detection using computer vision."""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

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


@dataclass
class CropRegion:
    """A rectangular crop region defined by width, height, and top-left offset."""

    w: int
    h: int
    x: int
    y: int

    @property
    def crop_str(self) -> str:
        """Return FFmpeg crop string formatted as 'w:h:x:y'."""
        s = f"{self.w}:{self.h}:{self.x}:{self.y}"
        validate_crop(s)
        return s

    @classmethod
    def from_str(cls, val: str) -> CropRegion:
        """Parse from 'w:h:x:y' string."""
        validate_crop(val)
        parts = [int(p) for p in val.strip().split(":")]
        return cls(w=parts[0], h=parts[1], x=parts[2], y=parts[3])


def has_opencv() -> bool:
    """Return True if OpenCV is installed."""
    return _OPENCV_AVAILABLE


def detect_facecam(
    video_path: Path,
    sample_duration: float = 15.0,
    num_samples: int = 6,
) -> CropRegion | None:
    """Sample video frames to detect the stationary streamer facecam window.

    Returns a ``CropRegion`` if a persistent webcam face is found in one of the
    corner quadrants, or ``None`` if no facecam is detected.
    """
    if not has_opencv():
        console.print("[dim yellow]ℹ OpenCV not available for auto-crop. Using standard defaults.[/]")
        return None

    info = probe(video_path)
    width, height = info.width, info.height
    duration = info.duration or 30.0

    if width <= 0 or height <= 0:
        return None

    cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
    face_cascade = cv2.CascadeClassifier(cascade_path)
    if face_cascade.empty():
        return None

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        return None

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
    clusters: list[list[tuple[int, int, int, int]]] = []
    threshold_dist = min(width, height) * 0.15

    for face in detected_faces:
        fx, fy, fw, fh = face
        fcx, fcy = fx + fw / 2, fy + fh / 2
        assigned = False
        for cluster in clusters:
            ccx = sum(f[0] + f[2] / 2 for f in cluster) / len(cluster)
            ccy = sum(f[1] + f[3] / 2 for f in cluster) / len(cluster)
            if math.hypot(fcx - ccx, fcy - ccy) <= threshold_dist:
                cluster.append(face)
                assigned = True
                break
        if not assigned:
            clusters.append([face])

    clusters.sort(key=len, reverse=True)
    best_cluster = clusters[0]

    if len(sample_times) > 2 and len(best_cluster) < 2:
        return None

    avg_x = sum(f[0] for f in best_cluster) // len(best_cluster)
    avg_y = sum(f[1] for f in best_cluster) // len(best_cluster)
    avg_w = sum(f[2] for f in best_cluster) // len(best_cluster)
    avg_h = sum(f[3] for f in best_cluster) // len(best_cluster)

    # Frame the webcam with appropriate headroom and torso margins
    cam_w = int(avg_w * 2.2)
    cam_h = int(cam_w * 9 / 16)
    if cam_h < int(avg_h * 1.8):
        cam_h = int(avg_h * 1.8)
        cam_w = int(cam_h * 16 / 9)

    cam_x = int(avg_x + avg_w / 2 - cam_w / 2)
    cam_y = int(avg_y + avg_h / 2 - cam_h * 0.45)

    cam_x = max(0, min(cam_x, width - cam_w))
    cam_y = max(0, min(cam_y, height - cam_h))
    cam_w = min(cam_w, width - cam_x)
    cam_h = min(cam_h, height - cam_y)

    cam_w = (cam_w // 2) * 2
    cam_h = (cam_h // 2) * 2
    cam_x = (cam_x // 2) * 2
    cam_y = (cam_y // 2) * 2

    return CropRegion(w=cam_w, h=cam_h, x=cam_x, y=cam_y)


def detect_gameplay(
    video_path: Path,
) -> CropRegion:
    """Return the full-frame gameplay region, probed from the source video.

    Returns a ``CropRegion`` covering the full source resolution (even-aligned).
    If the source cannot be probed, returns a safe 1920×1080 default.
    """
    info = probe(video_path)
    width = (info.width // 2) * 2
    height = (info.height // 2) * 2

    if width <= 0 or height <= 0:
        return CropRegion(w=1920, h=1080, x=0, y=0)

    return CropRegion(w=width, h=height, x=0, y=0)


def auto_detect_crops(
    video_path: Path,
) -> tuple[CropRegion, CropRegion]:
    """Auto-detect both facecam and gameplay regions with safe fallbacks.

    When face detection is unavailable or fails, the fallback camera crop is
    derived from the source resolution so it never extends beyond the frame.
    """
    cam = detect_facecam(video_path)
    if cam is None:
        # Build a resolution-safe fallback from source dimensions
        info = probe(video_path)
        src_w = max(info.width, 1)
        src_h = max(info.height, 1)

        # Camera fallback: top-right quadrant, capped to source bounds
        cam_w = min(640, src_w // 2)
        cam_h = min(480, src_h // 2)
        cam_x = max(0, src_w - cam_w)
        cam_y = 0

        # Even-align for FFmpeg
        cam_w = (cam_w // 2) * 2
        cam_h = (cam_h // 2) * 2
        cam_x = (cam_x // 2) * 2

        cam = CropRegion(w=cam_w, h=cam_h, x=cam_x, y=cam_y)

    game = detect_gameplay(video_path)
    return cam, game
