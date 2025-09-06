"""Offline speech-to-text with hotword detection using Vosk and sounddevice.

This module provides a convenience wrapper around the Vosk speech
recognition toolkit to perform offline transcription of microphone
audio. It includes a ``STTWorker`` class that runs in its own
``QThread`` and emits recognized text when a specified hotword is
detected, followed by the next spoken utterance. Microphone devices
can be enumerated via ``list_input_devices``.

Usage:

    from .stt import STTWorker, list_input_devices

    devices = list_input_devices()
    worker = STTWorker(hotword="cranium", device_index=devices[0][0])
    worker.recognized.connect(handle_text)
    worker.start()

The worker will continuously listen and emit the next sentence after
the hotword is spoken. It stops when ``stop()`` is called.

Note: To run this module, a Vosk model directory must be available.
By default, it looks for a model in ``models/vosk`` relative to the
current working directory or uses the ``VOSK_MODEL`` environment
variable. You can download small English models from
https://alphacephei.com/vosk/models.
"""

from __future__ import annotations

import json
import os
import queue
from typing import List, Tuple

import sounddevice as sd
from vosk import Model, KaldiRecognizer
from PySide6.QtCore import QThread, Signal


def list_input_devices() -> List[Tuple[int, str]]:
    """Enumerate available audio input devices.

    Returns a list of tuples ``(index, name)`` for each device with at
    least one input channel. If device enumeration fails, an empty
    list is returned.
    """
    devices: List[Tuple[int, str]] = []
    try:
        for idx, dev in enumerate(sd.query_devices()):
            if dev["max_input_channels"] > 0:
                devices.append((idx, dev["name"]))
    except Exception:
        # If enumeration fails, return empty list
        return devices
    return devices


class STTWorker(QThread):
    """Threaded speech-to-text engine with hotword detection.

    When started, this worker listens to the specified audio input
    device and processes audio using Vosk. When the hotword is
    detected in the transcription, the worker waits for the next
    complete utterance (final result) and emits it via the
    ``recognized`` signal. The hotword itself is removed from the
    emitted text if present at the beginning.
    """

    recognized = Signal(str)

    def __init__(self, hotword: str = "cranium", device_index: int | None = None, model_path: str | None = None,
                 sample_rate: int = 16000) -> None:
        super().__init__()
        self.hotword = (hotword or "").lower()
        self.device_index = device_index
        # Locate the model: environment variable overrides default path
        self.model_path = model_path or os.environ.get("VOSK_MODEL", os.path.join(os.getcwd(), "models", "vosk"))
        self.sample_rate = sample_rate
        self._running = True

    def stop(self) -> None:
        """Stop listening and signal the thread to finish."""
        self._running = False

    def run(self) -> None:
        """Main loop reading audio, performing recognition and detecting hotwords."""
        # Attempt to load the model. If it fails, the thread exits silently.
        try:
            model = Model(self.model_path)
        except Exception:
            return
        recognizer = KaldiRecognizer(model, self.sample_rate)

        audio_queue: queue.Queue[bytes] = queue.Queue()

        def audio_callback(indata, frames, time, status) -> None:
            """Callback that copies raw audio bytes into a queue."""
            if status:
                # We ignore overflow or other status flags
                pass
            # Convert to bytes and push to queue
            audio_queue.put(bytes(indata))

        try:
            stream = sd.RawInputStream(
                samplerate=self.sample_rate,
                blocksize=8000,
                dtype="int16",
                channels=1,
                callback=audio_callback,
                device=self.device_index,
            )
        except Exception:
            # Device could not be opened
            return

        # Indicates whether we detected the hotword and are awaiting the next utterance
        listening_for_next: bool = False

        with stream:
            while self._running:
                try:
                    data = audio_queue.get(timeout=0.1)
                except queue.Empty:
                    continue
                if recognizer.AcceptWaveform(data):
                    # Final result received
                    try:
                        result = json.loads(recognizer.Result())
                    except Exception:
                        result = {}
                    text = (result.get("text") or "").strip().lower()
                    if not text:
                        continue
                    if not listening_for_next:
                        # Check if hotword appears in this final text
                        if self.hotword and self.hotword in text:
                            # If there is remaining text after the hotword in this result, emit it
                            idx = text.find(self.hotword)
                            remainder = text[idx + len(self.hotword):].strip()
                            if remainder:
                                self.recognized.emit(remainder)
                            else:
                                # Wait for next utterance
                                listening_for_next = True
                            continue
                    else:
                        # We were waiting for the next utterance
                        listening_for_next = False
                        # Emit the entire recognized text
                        # Remove hotword if present at start (in case of misalignment)
                        if text.startswith(self.hotword):
                            text = text[len(self.hotword):].strip()
                        if text:
                            self.recognized.emit(text)
                else:
                    # Partial result available
                    try:
                        partial = json.loads(recognizer.PartialResult())
                    except Exception:
                        partial = {}
                    partial_text = (partial.get("partial") or "").strip().lower()
                    if partial_text and not listening_for_next and self.hotword:
                        if self.hotword in partial_text:
                            # Start capturing the next utterance
                            listening_for_next = True