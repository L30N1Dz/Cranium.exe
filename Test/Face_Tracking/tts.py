"""Simple text-to-speech abstraction using pyttsx3.

This module defines a ``TextToSpeech`` class that wraps the ``pyttsx3``
engine to provide voice enumeration and asynchronous speaking of
strings. Voices can be enumerated and selected by their engine ID.
Speaking occurs in a background thread to avoid blocking the GUI.

Note: On some platforms, pyttsx3 may require additional drivers or
dependencies. Users can install additional voices through their
operating system settings. If no voices are found, TTS will be
disabled.
"""

from __future__ import annotations

import threading
from typing import List, Tuple

import pyttsx3


class TextToSpeech:
    """Provide basic text-to-speech functionality via pyttsx3.

    The class encapsulates voice listing and selection as well as
    asynchronous speech synthesis. Each call to ``speak`` spawns a
    thread that initializes its own engine instance to avoid
    concurrency issues within pyttsx3.
    """

    def __init__(self) -> None:
        # We retain a default engine instance primarily to list voices.
        try:
            self._engine = pyttsx3.init()
        except Exception:
            self._engine = None
        self._voice_id: str | None = None

    def get_voices(self) -> List[Tuple[str, str]]:
        """Return a list of available (voice_id, voice_name) tuples.

        Returns an empty list if voice enumeration fails or no voices
        are available.
        """
        voices: List[Tuple[str, str]] = []
        if self._engine is None:
            return voices
        try:
            for v in self._engine.getProperty("voices"):
                voices.append((v.id, v.name))
        except Exception:
            pass
        return voices

    def set_voice(self, voice_id: str) -> None:
        """Set the voice ID to be used for subsequent speech.

        The voice will be applied to future calls to ``speak``. If the
        ID is invalid or an error occurs, the default engine voice
        remains unchanged.
        """
        self._voice_id = voice_id

    def speak(self, text: str) -> None:
        """Speak the given text asynchronously.

        A new thread is created for each invocation to avoid blocking
        the calling thread (e.g., the GUI event loop). A separate
        engine instance is used inside the thread to avoid interfering
        with any other pyttsx3 engines in the process.
        """
        if not text:
            return

        def run_speech(t: str, voice_id: str | None) -> None:
            try:
                engine = pyttsx3.init()
                # Apply the requested voice if available
                if voice_id:
                    try:
                        engine.setProperty("voice", voice_id)
                    except Exception:
                        pass
                engine.say(t)
                engine.runAndWait()
                engine.stop()
            except Exception:
                # Silently ignore speech failures
                return

        # Launch speech in a daemon thread so it doesn't block or
        # prevent the application from exiting.
        threading.Thread(target=run_speech, args=(text, self._voice_id), daemon=True).start()