"""Ollama client for streaming chat with vision-enabled models.

This module provides a wrapper around the Ollama REST API to
facilitate sending images alongside chat messages and receiving
streamed responses. It maintains a conversation history and exposes
signals for incremental output, completion, and errors.
"""

from __future__ import annotations

import base64
import json
from typing import Callable, List, Dict, Optional

import requests
from PySide6.QtCore import QObject, QThread, Signal, Slot
from PySide6.QtGui import QImage


class _ChatWorker(QThread):
    """Background thread for streaming responses from the Ollama API.

    The worker posts the given payload to the Ollama server and
    emits partial outputs as they arrive. Each line returned by
    Ollama is a JSON object containing either a message delta or a
    completion marker. Errors are surfaced via the ``error`` signal.
    """

    streamed = Signal(str)
    done = Signal()
    error = Signal(str)

    def __init__(self, endpoint: str, payload: dict):
        super().__init__()
        self.endpoint = endpoint
        self.payload = payload
        self._stop = False

    def run(self) -> None:
        try:
            with requests.post(self.endpoint, json=self.payload, stream=True, timeout=600) as response:
                response.raise_for_status()
                for line in response.iter_lines(decode_unicode=True):
                    if self._stop:
                        break
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                    except Exception:
                        continue
                    # The Ollama API returns JSON objects with a message delta
                    msg = data.get("message")
                    if msg and isinstance(msg, dict):
                        content = msg.get("content", "")
                        if content:
                            self.streamed.emit(content)
                    if data.get("done"):
                        break
            self.done.emit()
        except Exception as exc:
            self.error.emit(str(exc))

    def stop(self) -> None:
        self._stop = True


class OllamaClient(QObject):
    """High-level API for interacting with a local Ollama server.

    This class manages conversation state, converts QImage frames to
    base64 strings, and coordinates background workers for streaming
    responses. Callbacks are provided for streaming deltas, message
    completion, and error handling.
    """

    def __init__(self, model: str,
                 on_stream_delta: Callable[[str], None],
                 on_response_done: Callable[[], None],
                 on_error: Callable[[str], None],
                 host: str = "http://127.0.0.1:11434"):
        super().__init__()
        self.host = host.rstrip('/')
        self.model = model
        self.on_stream_delta = on_stream_delta
        self.on_response_done = on_response_done
        self.on_error = on_error
        self.history: List[Dict[str, str]] = []  # maintain full chat history
        self.is_busy: bool = False
        self._worker: Optional[_ChatWorker] = None

    def set_model(self, model: str) -> None:
        """Update the model used for subsequent requests."""
        self.model = model

    def reset(self) -> None:
        """Clear conversation history."""
        self.history.clear()

    def close(self) -> None:
        """Terminate any active worker threads."""
        if self._worker and self._worker.isRunning():
            self._worker.stop()
            self._worker.wait(200)
        self._worker = None

    def _qimage_to_base64(self, img: QImage, max_width: int = 640) -> str:
        """Convert a QImage to a base64 encoded JPEG string.

        The image is optionally downscaled to ``max_width`` to reduce
        bandwidth. JPEG encoding is used for good compression while
        preserving reasonable quality.

        :param img: The image to encode.
        :param max_width: Maximum width to resize the image before
            encoding. Aspect ratio is preserved.
        :returns: Base64-encoded JPEG string (without data URI prefix).
        """
        # Downscale if necessary
        if img.width() > max_width:
            scaled = img.scaledToWidth(max_width)
        else:
            scaled = img
        # Use QBuffer to write JPEG into a QByteArray
        from PySide6.QtCore import QBuffer, QByteArray
        qba = QByteArray()
        buf = QBuffer(qba)
        buf.open(QBuffer.OpenModeFlag.ReadWrite)
        scaled.save(buf, "JPEG", quality=85)
        data = bytes(qba)
        return base64.b64encode(data).decode('ascii')

    def _build_payload(self, user_text: Optional[str], img_b64: Optional[str]) -> dict:
        """Construct the request payload for the Ollama API.

        If ``user_text`` is provided, the message containing the text
        will include the image if ``img_b64`` is not None. If
        ``user_text`` is None, a default prompt instructing the model
        to analyze the frame is used.
        """
        messages = self.history.copy()
        # Build the message to append
        if user_text is not None:
            msg: Dict[str, object] = {"role": "user", "content": user_text}
            if img_b64:
                msg["images"] = [img_b64]
            messages.append(msg)
        else:
            # Use a generic prompt if no user text is provided (for auto mode)
            msg: Dict[str, object] = {
                "role": "user",
                "content": "Describe the contents of the attached frame.",
            }
            if img_b64:
                msg["images"] = [img_b64]
            messages.append(msg)
        return {
            "model": self.model,
            "messages": messages,
            "stream": True,
        }

    def _launch_worker(self, payload: dict) -> None:
        """Create and start a new worker thread for streaming the response."""
        self._worker = _ChatWorker(self.host + "/api/chat", payload)
        self._worker.streamed.connect(self._on_stream)
        self._worker.done.connect(self._on_done)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    # Public methods
    def send_user_message_with_frame(self, text: str, frame: QImage) -> None:
        """Send a user message along with the current frame to the model.

        This method respects the busy flag to prevent overlapping
        requests. It appends the user message to the history and
        creates an empty assistant entry which will be filled as
        streaming deltas are received.
        """
        if self.is_busy:
            return
        img64 = self._qimage_to_base64(frame)
        payload = self._build_payload(text, img64)
        # Append user and placeholder assistant messages to history
        self.history.append({"role": "user", "content": text})
        self.history.append({"role": "assistant", "content": ""})
        self.is_busy = True
        self._launch_worker(payload)

    def send_frame_with_prompt(self, frame: QImage, prompt: str) -> None:
        """Send a frame with a generic prompt (no user text).

        This is used for automatic frame analysis when the user does
        not explicitly ask a question. The prompt describes what
        analysis to perform on the image.
        """
        if self.is_busy:
            return
        img64 = self._qimage_to_base64(frame)
        payload = self._build_payload(prompt, img64)
        self.history.append({"role": "user", "content": prompt})
        self.history.append({"role": "assistant", "content": ""})
        self.is_busy = True
        self._launch_worker(payload)

    # Worker signal handlers
    @Slot(str)
    def _on_stream(self, delta: str) -> None:
        # Append delta to last assistant entry
        if self.history and self.history[-1]["role"] == "assistant":
            self.history[-1]["content"] += delta
        self.on_stream_delta(delta)

    @Slot()
    def _on_done(self) -> None:
        self.is_busy = False
        self.on_response_done()

    @Slot(str)
    def _on_error(self, msg: str) -> None:
        self.is_busy = False
        self.on_error(msg)