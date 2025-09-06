"""Video capture and face detection using OpenCV and MediaPipe.

This module defines a ``VideoWorker`` class that runs in its own
thread to continuously capture frames from the webcam, detect faces
using MediaPipe, and emit the processed frames along with face
coordinates. The detection algorithm selects the highest confidence
face per frame and computes its bounding box and center.
"""

from __future__ import annotations

from typing import Optional, Tuple

import cv2
import numpy as np
import mediapipe as mp
from PySide6.QtCore import QThread, Signal
from PySide6.QtGui import QImage


class VideoWorker(QThread):
    """Threaded webcam capture with face detection.

    Emits ``frame_ready`` with a QImage and metadata on every frame
    and ``face_center_available`` when a face is detected with
    normalized center coordinates and relative bounding box.
    """

    # Emitted each time a new frame is ready. The dict may include
    # additional metadata (e.g., bounding box coordinates).
    frame_ready = Signal(QImage, dict)
    # Emitted when a face is detected with normalized center and box
    face_center_available = Signal(tuple, tuple)

    def __init__(self, camera_index: int = 0,
                 width: int = 640,
                 height: int = 480,
                 fps: int = 30):
        super().__init__()
        self.camera_index = camera_index
        self.width = width
        self.height = height
        self.fps = fps
        self.running = True
        # Initialize video capture
        self.cap = cv2.VideoCapture(self.camera_index)
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        self.cap.set(cv2.CAP_PROP_FPS, self.fps)
        # Initialize MediaPipe face detection
        self.mp_face = mp.solutions.face_detection.FaceDetection(
            model_selection=0, min_detection_confidence=0.5
        )

    def stop(self) -> None:
        """Signal the thread to stop and release resources."""
        self.running = False
        self.wait(500)
        if self.cap.isOpened():
            self.cap.release()
        # Close MediaPipe resources
        try:
            self.mp_face.close()
        except Exception:
            pass

    def run(self) -> None:
        """Main loop capturing and processing frames."""
        while self.running:
            ok, frame = self.cap.read()
            if not ok:
                self.msleep(10)
                continue
            # BGR to RGB conversion for MediaPipe
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            results = self.mp_face.process(rgb)

            h, w, _ = frame.shape
            bbox_rel: Optional[Tuple[float, float, float, float]] = None
            if results and results.detections:
                # Select the detection with the highest confidence
                det = max(results.detections, key=lambda d: d.score[0])
                rb = det.location_data.relative_bounding_box
                x, y, bw, bh = rb.xmin, rb.ymin, rb.width, rb.height
                # Clamp values to [0, 1]
                x = max(0.0, min(1.0, x))
                y = max(0.0, min(1.0, y))
                bw = max(0.0, min(1.0 - x, bw))
                bh = max(0.0, min(1.0 - y, bh))
                bbox_rel = (x, y, bw, bh)
                # Draw bounding box on the frame for visualization
                px, py = int(x * w), int(y * h)
                pw, ph = int(bw * w), int(bh * h)
                cv2.rectangle(frame, (px, py), (px + pw, py + ph), (0, 255, 120), 2)
                # Compute center in normalized coordinates
                cx = x + bw / 2.0
                cy = y + bh / 2.0
                # Draw center point
                cv2.circle(frame, (int(cx * w), int(cy * h)), 4, (0, 255, 120), -1)
                # Emit face center and box
                self.face_center_available.emit((cx, cy), bbox_rel)
            # Convert BGR to QImage
            qimg = self._to_qimage(frame)
            self.frame_ready.emit(qimg, {"bbox_rel": bbox_rel})
            # Sleep to control frame rate
            self.msleep(int(1000 / self.fps))

    @staticmethod
    def _to_qimage(frame_bgr: np.ndarray) -> QImage:
        """Convert a BGR frame (numpy array) to a QImage."""
        # Convert BGR to RGB
        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        h, w, ch = rgb.shape
        bytes_per_line = ch * w
        # Copy is required because QImage does not own the memory of the numpy array
        return QImage(rgb.data, w, h, bytes_per_line, QImage.Format.Format_RGB888).copy()