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
                 fps: int = 30,
                 detector_type: str = "face_detection"):
        """Initialize the video worker.

        Parameters
        ----------
        camera_index: int
            Index of the camera device.
        width: int
            Desired capture width.
        height: int
            Desired capture height.
        fps: int
            Desired frames per second.
        detector_type: str
            Which face detection algorithm to use: "face_detection" (MediaPipe
            Face Detection) or "face_mesh" (MediaPipe Face Mesh landmarks).
        """
        super().__init__()
        self.camera_index = camera_index
        self.width = width
        self.height = height
        self.fps = fps
        self.detector_type = detector_type
        self.running = True
        # Initialize video capture
        self.cap = cv2.VideoCapture(self.camera_index)
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        self.cap.set(cv2.CAP_PROP_FPS, self.fps)
        # Pre-initialize MediaPipe detectors. We keep both around and choose
        # between them at runtime to avoid incurring initialization costs when
        # switching detectors. The FaceDetection API returns bounding boxes
        # directly, while FaceMesh returns facial landmarks from which we
        # compute a bounding box.
        self.mp_face_detection = mp.solutions.face_detection.FaceDetection(
            model_selection=0, min_detection_confidence=0.5
        )
        self.mp_face_mesh = mp.solutions.face_mesh.FaceMesh(
            static_image_mode=False,
            max_num_faces=2,
            refine_landmarks=False,
            min_detection_confidence=0.5,
            min_tracking_confidence=0.5,
        )

    def set_detector_type(self, detector_type: str) -> None:
        """Update the detector type used for face tracking.

        Parameters
        ----------
        detector_type: str
            Either "face_detection" or "face_mesh".
        """
        if detector_type not in {"face_detection", "face_mesh"}:
            raise ValueError("detector_type must be 'face_detection' or 'face_mesh'")
        self.detector_type = detector_type

    def stop(self) -> None:
        """Signal the thread to stop and release resources."""
        self.running = False
        self.wait(500)
        if self.cap.isOpened():
            self.cap.release()
        # Close MediaPipe resources
        try:
            # Close detectors if they support close()
            if hasattr(self.mp_face_detection, 'close'):
                self.mp_face_detection.close()
            if hasattr(self.mp_face_mesh, 'close'):
                self.mp_face_mesh.close()
        except Exception:
            pass

    def run(self) -> None:
        """Main loop capturing and processing frames."""
        while self.running:
            ok, frame = self.cap.read()
            if not ok:
                self.msleep(10)
                continue
            # Convert BGR to RGB for MediaPipe processing
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            h, w, _ = frame.shape
            bbox_rel: Optional[Tuple[float, float, float, float]] = None
            cx = cy = None
            # Choose detector based on current setting
            if self.detector_type == "face_mesh":
                results = self.mp_face_mesh.process(rgb)
                if results and results.multi_face_landmarks:
                    # Determine bounding box for the largest face by area
                    best_bbox = None
                    best_area = 0
                    for face_landmarks in results.multi_face_landmarks:
                        # Extract x,y coordinates from landmarks
                        xs = [lm.x for lm in face_landmarks.landmark]
                        ys = [lm.y for lm in face_landmarks.landmark]
                        x_min = max(0.0, min(xs))
                        y_min = max(0.0, min(ys))
                        x_max = min(1.0, max(xs))
                        y_max = min(1.0, max(ys))
                        bw = x_max - x_min
                        bh = y_max - y_min
                        area = bw * bh
                        if area > best_area:
                            best_area = area
                            best_bbox = (x_min, y_min, bw, bh)
                    if best_bbox:
                        x, y, bw, bh = best_bbox
                        bbox_rel = (x, y, bw, bh)
                        # Compute center
                        cx = x + bw / 2.0
                        cy = y + bh / 2.0
                        # Draw bounding box
                        px, py = int(x * w), int(y * h)
                        pw, ph = int(bw * w), int(bh * h)
                        cv2.rectangle(frame, (px, py), (px + pw, py + ph), (0, 255, 120), 2)
                        # Draw center point
                        cv2.circle(frame, (int(cx * w), int(cy * h)), 4, (0, 255, 120), -1)
                        # Optionally draw some landmarks (nose tip for orientation)
                        # We'll highlight a few key landmarks: nose tip (index 1), left and right eyes (33, 263)
                        indices = [1, 33, 263]
                        for idx in indices:
                            try:
                                lm = results.multi_face_landmarks[0].landmark[idx]
                                cv2.circle(frame, (int(lm.x * w), int(lm.y * h)), 2, (120, 200, 255), -1)
                            except Exception:
                                pass
                        # Emit face center and bounding box
                        self.face_center_available.emit((cx, cy), bbox_rel)
            else:
                # Default: use MediaPipe Face Detection
                results = self.mp_face_detection.process(rgb)
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
                    cx = x + bw / 2.0
                    cy = y + bh / 2.0
                    # Draw bounding box and center
                    px, py = int(x * w), int(y * h)
                    pw, ph = int(bw * w), int(bh * h)
                    cv2.rectangle(frame, (px, py), (px + pw, py + ph), (0, 255, 120), 2)
                    cv2.circle(frame, (int(cx * w), int(cy * h)), 4, (0, 255, 120), -1)
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