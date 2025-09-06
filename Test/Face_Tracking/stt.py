"""Speech-to-text worker using Vosk with hotword detection.

This module defines ``STTWorker``, a QThread that listens to an audio input
device, recognises speech with a Vosk model, and triggers on a hotword. Once
the hotword is detected, the next complete sentence (ending in ., ?, or !)
will be emitted via the ``detected_sentence`` signal. Errors in model
loading or audio capture are emitted via the ``error`` signal.

Usage
-----

    from .stt import STTWorker
    worker = STTWorker(device_index=0, hotword="computer")
    worker.detected_sentence.connect(handle_sentence)
    worker.error.connect(handle_error)
    worker.start()

Ensure you have downloaded and **unzipped** a Vosk model into
``models/vosk`` (or set ``VOSK_MODEL`` env var to the unzipped folder).
"""

from __future__ import annotations

import json
import os
from typing import Optional

import sounddevice as sd
from vosk import Model, KaldiRecognizer
from PySide6.QtCore import QThread, Signal


class STTWorker(QThread):
    """A speech-to-text thread that listens for a hotword and captures the next sentence.

    Parameters
    ----------
    device_index: int or None
        Index of the microphone to use (see ``sounddevice.query_devices``). If None,
        the default input device is used.
    hotword: str
        Case-insensitive keyword that triggers recording of the next sentence.
    model_dir: str or None
        Path to the Vosk model directory. If None, uses environment variable
        ``VOSK_MODEL`` or ``models/vosk`` relative to cwd.
    sample_rate: int
        Sampling rate to use for audio capture and recognition.
    """

    detected_sentence = Signal(str)
    error = Signal(str)

    def __init__(self, device_index: Optional[int] = None, hotword: str = "cranium",
                 model_dir: Optional[str] = None, sample_rate: int = 16000) -> None:
        super().__init__()
        self.device_index = device_index
        self.hotword = (hotword or "cranium").lower()
        # Determine model directory
        if model_dir:
            self.model_dir = model_dir
        else:
            self.model_dir = os.environ.get("VOSK_MODEL") or os.path.join(os.getcwd(), "models", "vosk")
        self.sample_rate = sample_rate
        # Internal state
        self._running = True
        # Indicates whether we've heard the hotword and are awaiting the
        # next utterance. When True, the next final result will be
        # emitted as a sentence and then reset.
        self._heard_hotword = False
        # Buffer for collecting utterance fragments after the hotword; this is
        # used primarily when we allow multiple final segments to be joined.
        self._buf: list[str] = []

    def stop(self) -> None:
        """Stop the worker gracefully."""
        self._running = False

    def run(self) -> None:
        """Thread entry point: load model, open audio device, and process audio."""
        # Check model directory
        if not os.path.isdir(self.model_dir):
            self.error.emit(f"Vosk model folder not found: {self.model_dir}. Unzip the model folder there or set VOSK_MODEL.")
            return
        # Check that the directory appears unzipped (contains expected subdirs)
        try:
            contents = set(os.listdir(self.model_dir))
        except Exception:
            contents = set()
        expected_any = {"am", "conf", "graph", "ivector"}
        if not contents.intersection(expected_any):
            self.error.emit("Vosk model folder does not appear to be extracted correctly. It should contain subdirectories like 'am', 'conf', 'graph'.")
            return
        # Load model
        try:
            model = Model(self.model_dir)
        except Exception as e:
            self.error.emit(f"Failed to load Vosk model: {e}")
            return
        recognizer = KaldiRecognizer(model, self.sample_rate)
        recognizer.SetWords(True)

        def callback(indata, frames, time, status) -> None:
            """sounddevice callback processes audio chunks.

            The ``indata`` argument is a memoryview backed by a cffi buffer when
            using RawInputStream. Vosk expects a bytes-like object; passing
            ``indata`` directly can raise errors on some platforms (e.g.,
            ``initializer for ctype 'char *' must be a cdata pointer``). We
            explicitly convert it to bytes before feeding it to Vosk. If
            interrupted (e.g., via ``stop()``), the callback will abort the
            stream.
            """
            # Early exit if stopped
            if not self._running:
                raise sd.CallbackAbort
            try:
                # Convert memoryview/cffi buffer to bytes for Vosk
                data_bytes = bytes(indata)
                if recognizer.AcceptWaveform(data_bytes):
                    res = json.loads(recognizer.Result())
                    self._handle_final(res.get("text", ""))
                else:
                    part = json.loads(recognizer.PartialResult()).get("partial", "")
                    self._handle_partial(part)
            except Exception as err:
                self.error.emit(f"STT stream error: {err}")
                raise sd.CallbackAbort

        # Open audio stream
        try:
            with sd.RawInputStream(
                samplerate=self.sample_rate,
                blocksize=8000,
                dtype="int16",
                channels=1,
                callback=callback,
                device=self.device_index,
            ):
                # Keep thread alive until stopped
                while self._running:
                    self.msleep(100)
        except Exception as exc:
            self.error.emit(f"Audio capture error: {exc}")

    def _handle_partial(self, text: str) -> None:
        """Handle partial (intermediate) recognition results."""
        if not text:
            return
        lower = text.lower()
        # Detect the hotword in partial results; once heard, clear the buffer
        # and set the flag to capture the next final utterance.
        if not self._heard_hotword and self.hotword in lower:
            self._heard_hotword = True
            self._buf.clear()

    def _handle_final(self, text: str) -> None:
        """Handle final recognition results."""
        if not text:
            return
        lower = text.lower()
        # If we haven't yet heard the hotword in this utterance, look for it
        if not self._heard_hotword:
            if self.hotword in lower:
                # Remove the hotword from the text and emit any trailing
                # content immediately. If there's no trailing content, set
                # the flag to capture the next final utterance.
                idx = lower.find(self.hotword)
                remainder = text[idx + len(self.hotword):].strip()
                if remainder:
                    self.detected_sentence.emit(remainder)
                else:
                    self._heard_hotword = True
                    self._buf.clear()
            return
        # At this point we've heard the hotword. Remove the hotword if it
        # appears in the final result and emit the remaining text.  This
        # ensures the hotword itself is not included in the transcribed
        # sentence.
        combined = text.strip()
        if combined:
            import re
            pattern = rf"^\s*{re.escape(self.hotword)}\s*[:,\-]?\s*"
            cleaned = re.sub(pattern, "", combined, flags=re.IGNORECASE)
            if cleaned.strip():
                self.detected_sentence.emit(cleaned.strip())
        # Reset for next hotword
        self._buf.clear()
        self._heard_hotword = False
