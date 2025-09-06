from __future__ import annotations

"""Text-to-speech manager supporting system and Kokoro backends.

This module defines ``TTSManager``, a robust, threaded text-to-speech engine
that can speak queued utterances using either the host system voices via
``pyttsx3`` or the Kokoro ONNX model.  The manager creates a fresh
``pyttsx3`` engine for every utterance to avoid the one-and-done stall
seen on Windows when reusing SAPI engines.  When Kokoro is selected and
properly configured via environment variables ``KOKORO_MODEL_PATH`` and
``KOKORO_VOICES_PATH``, the manager instantiates Kokoro per utterance,
passing explicit paths to ensure the chosen voice is applied.  If any
step fails, it falls back to the system backend gracefully.

Example usage::

    tts = TTSManager()
    tts.set_backend("kokoro")
    tts.set_voice_by_hint("am_adam")
    tts.speak("Hello world")

    # Clean up when finished
    tts.stop()
"""

import os
import sys
import queue
import threading
from enum import Enum
from typing import Optional, List, Tuple

try:
    import numpy as np  # type: ignore
except Exception:  # pragma: no cover
    np = None  # type: ignore

try:
    import sounddevice as sd  # type: ignore
except Exception:  # pragma: no cover
    sd = None  # type: ignore

try:
    import pyttsx3  # type: ignore
except Exception:  # pragma: no cover
    pyttsx3 = None

# Attempt to import Kokoro from the two common package names.  The
# ``kokoro-onnx`` package exposes the class under ``kokoro_onnx``, while
# ``kokoro`` packages export it directly.  We try both and set a
# sentinel flag accordingly.
_KOKORO_IMPORTED = False
_Kokoro = None
try:
    from kokoro_onnx import Kokoro as _Kokoro  # type: ignore
    _KOKORO_IMPORTED = True
except Exception:
    try:
        from kokoro import Kokoro as _Kokoro  # type: ignore
        _KOKORO_IMPORTED = True
    except Exception:
        _KOKORO_IMPORTED = False
        _Kokoro = None


class TTSBackend(str, Enum):
    """Enumeration of the supported TTS backends."""

    SYSTEM = "system"
    KOKORO = "kokoro"


class TTSManager:
    """Threaded text-to-speech manager.

    The manager runs a worker thread that processes utterance requests
    enqueued via :meth:`speak`.  It supports two backends: the
    ``system`` backend uses ``pyttsx3`` for synthesis; the ``kokoro``
    backend uses the Kokoro ONNX model.  For reliability on Windows,
    each utterance is synthesised with a fresh ``pyttsx3`` engine
    created on the worker thread; this avoids the problem where only
    the first utterance is spoken when reusing a single engine.  When
    Kokoro is selected, the manager instantiates a new Kokoro object
    for each utterance with explicit model and voice paths read from
    environment variables.  If Kokoro cannot be initialised or fails
    to synthesise, synthesis falls back to the system backend.
    """

    def __init__(self, backend: TTSBackend | str = TTSBackend.SYSTEM,
                 voice_hint: Optional[str] = None) -> None:
        self._backend: TTSBackend = TTSBackend(backend)
        self._voice_hint: Optional[str] = voice_hint

        # Queue for utterances: each item is (text, per-call voice hint)
        self._queue: "queue.Queue[Tuple[str, Optional[str]]]" = queue.Queue()
        # Event to signal worker thread termination
        self._stop_event = threading.Event()
        # Start the worker thread
        self._worker_thread = threading.Thread(
            target=self._worker, name="TTSWorker", daemon=True
        )
        self._worker_thread.start()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def list_voices(self) -> List[str]:
        """Return available voice names for the current backend.

        For the Kokoro backend, we present a fixed list of common voice
        identifiers (``am_adam``, ``am_molly``, etc.) when both the
        model and voices files exist.  Otherwise we defer to the
        system backend.  For the system backend, installed voices are
        enumerated via ``pyttsx3``, with a fallback to a single
        ``"Default"`` entry if enumeration fails.
        """
        if self._backend == TTSBackend.KOKORO:
            model_path = os.environ.get("KOKORO_MODEL_PATH", "")
            voices_path = os.environ.get("KOKORO_VOICES_PATH", "")
            if os.path.isfile(model_path) and os.path.isfile(voices_path):
                return [
                    "am_adam",
                    "am_molly",
                    "am_dave",
                    "am_lisa",
                    "am_mark",
                    "am_sarah",
                ]
        # System voices via pyttsx3
        names: List[str] = []
        if pyttsx3:
            try:
                eng = pyttsx3.init()
                for v in eng.getProperty("voices"):
                    try:
                        names.append(v.name)
                    except Exception:
                        continue
                try:
                    eng.stop()
                except Exception:
                    pass
            except Exception:
                pass
        return names or ["Default"]

    def set_backend(self, backend: TTSBackend | str) -> None:
        """Switch the synthesis backend.

        The backend can be changed at runtime.  Valid values are
        ``"system"`` and ``"kokoro"`` (case-insensitive).  If an
        invalid value is supplied, a :class:`ValueError` is raised.
        """
        self._backend = TTSBackend(backend)

    def set_voice_by_hint(self, hint: Optional[str]) -> None:
        """Set a global voice hint used when synthesising utterances.

        The hint is matched case-insensitively against installed voice
        names for the system backend, and passed as-is to Kokoro for
        the Kokoro backend.  A ``None`` or empty string resets the
        hint.
        """
        self._voice_hint = hint or None

    def speak(self, text: str, voice_hint: Optional[str] = None) -> None:
        """Queue a text utterance for synthesis.

        The text is enqueued for the worker thread.  An optional
        per-call voice hint may be supplied, overriding the global
        hint for this utterance only.
        """
        if text:
            self._queue.put((text, voice_hint))

    def stop(self) -> None:
        """Stop the worker thread and flush remaining utterances."""
        self._stop_event.set()
        try:
            self._queue.put_nowait(("", None))
        except Exception:
            pass
        if self._worker_thread.is_alive():
            self._worker_thread.join(timeout=1.0)

    # ------------------------------------------------------------------
    # Worker thread
    # ------------------------------------------------------------------
    def _worker(self) -> None:
        while not self._stop_event.is_set():
            try:
                text, hint = self._queue.get(timeout=0.1)
            except queue.Empty:
                continue
            if not text:
                continue
            # Determine voice hint: per-call overrides global
            voice_hint = hint or self._voice_hint
            # Attempt synthesis with selected backend
            if self._backend == TTSBackend.KOKORO and self._try_kokoro(text, voice_hint):
                continue
            # Fallback to system TTS
            self._system_say(text, voice_hint)

    # ------------------------------------------------------------------
    # Synthesis backends
    # ------------------------------------------------------------------
    def _system_say(self, text: str, hint: Optional[str]) -> None:
        """Speak using pyttsx3 with a fresh engine per utterance."""
        if not pyttsx3:
            print("[TTS] pyttsx3 not installed; cannot use system TTS", file=sys.stderr)
            return
        # Create a new engine for each utterance; this avoids freezes on Windows
        try:
            engine = pyttsx3.init()
        except Exception as e:
            print(f"[TTS] Failed to init pyttsx3: {e}", file=sys.stderr)
            return
        # Apply voice hint if provided
        try:
            if hint:
                want = hint.lower()
                for v in engine.getProperty("voices"):
                    name = getattr(v, "name", "").lower()
                    if want in name:
                        engine.setProperty("voice", v.id)
                        break
        except Exception:
            pass
        # Speak
        try:
            engine.say(text)
            engine.runAndWait()
        except Exception as e:
            print(f"[TTS] pyttsx3 speak failed: {e}", file=sys.stderr)
        finally:
            try:
                engine.stop()
            except Exception:
                pass

    def _try_kokoro(self, text: str, hint: Optional[str]) -> bool:
        """Attempt to synthesise and play audio with Kokoro.

        Returns ``True`` on success; if any error occurs or Kokoro is
        unavailable, returns ``False``, signalling the caller to use
        system TTS instead.
        """
        if not _KOKORO_IMPORTED or _Kokoro is None:
            return False
        model_path = os.environ.get("KOKORO_MODEL_PATH")
        voices_path = os.environ.get("KOKORO_VOICES_PATH")
        if not model_path or not voices_path:
            return False
        if not os.path.isfile(model_path) or not os.path.isfile(voices_path):
            return False
        if sd is None or np is None:
            return False
        # Instantiate Kokoro for this utterance.  Some Kokoro voice packs
        # contain pickled numpy arrays and require the ``allow_pickle`` flag.
        def _construct_kokoro() -> object:
            """Try to instantiate Kokoro, patching numpy.load to allow_pickle if needed."""
            try:
                # Try positional arguments first (typical for kokoro-onnx)
                return _Kokoro(model_path, voices_path)  # type: ignore[arg-type]
            except TypeError:
                # Fall back to keyword arguments
                return _Kokoro(model_path=model_path, voices_path=voices_path)  # type: ignore[arg-type]
            except Exception as e:
                # If the error mentions pickled data, retry with allow_pickle=True
                msg = str(e).lower()
                if "allow_pickle" in msg or "pickled" in msg or "pickle" in msg:
                    try:
                        import numpy as _np
                        _orig_load = _np.load
                        def _patched_load(file, *args, **kwargs):
                            kwargs.setdefault("allow_pickle", True)
                            return _orig_load(file, *args, **kwargs)
                        _np.load = _patched_load  # temporarily override
                        try:
                            try:
                                return _Kokoro(model_path, voices_path)  # type: ignore[arg-type]
                            except TypeError:
                                return _Kokoro(model_path=model_path, voices_path=voices_path)  # type: ignore[arg-type]
                        finally:
                            _np.load = _orig_load
                    except Exception:
                        pass
                # Re-raise other errors
                raise

        try:
            kokoro = _construct_kokoro()
        except Exception as e:
            print(f"[TTS] Kokoro init failed: {e}; using system.", file=sys.stderr)
            return False
        voice = (hint or self._voice_hint or "am_adam")
        # Synthesise
        try:
            if hasattr(kokoro, "tts"):
                audio = kokoro.tts(text, voice=voice)  # type: ignore[attr-defined]
            else:
                audio = kokoro.infer(text, voice=voice)  # type: ignore[attr-defined]
        except Exception as e:
            print(f"[TTS] Kokoro synthesis failed: {e}", file=sys.stderr)
            return False
        # Convert to numpy array and play
        try:
            if not isinstance(audio, np.ndarray):
                audio = np.asarray(audio, dtype=np.float32)
            if audio.dtype != np.float32:
                audio = audio.astype(np.float32)
            sd.play(audio, samplerate=24000, blocking=True)
        except Exception as e:
            print(f"[TTS] Audio playback failed: {e}", file=sys.stderr)
            return False
        # Success
        return True


class TextToSpeech:
    """Compatibility shim for code expecting a ``TextToSpeech`` class.

    Older code may instantiate this class; internally it delegates
    operations to a singleton :class:`TTSManager`.  New code should
    use :class:`TTSManager` directly.
    """
    _manager: Optional[TTSManager] = None

    def __init__(self) -> None:
        if TextToSpeech._manager is None:
            TextToSpeech._manager = TTSManager()
        self._mgr = TextToSpeech._manager

    def get_voices(self) -> List[Tuple[str, str]]:
        names = self._mgr.list_voices()
        return [(name, name) for name in names]

    def set_voice(self, voice_id: str) -> None:
        self._mgr.set_voice_by_hint(voice_id)

    def speak(self, text: str) -> None:
        self._mgr.speak(text)