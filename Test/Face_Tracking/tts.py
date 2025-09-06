from __future__ import annotations

"""
Fully local TTS manager with two backends:
  • SYSTEM      → OS voices via pyttsx3 (safe fallback)
  • KOKORO82M   → hexgrad Kokoro‑82M (PyTorch) using local .pt voices

Key change vs earlier builds
----------------------------
This version **does not** instantiate `KModel` or manually load `kokoro‑v1_0.pth`.
It lets `KPipeline` construct the correct model internally and then feeds your
local voice tensor (`*.pt`) directly to `voice=`. That removes the
state‑dict mismatch you’re seeing (all those missing/extra keys).

Env you should set in main.py (before creating TTSManager)
---------------------------------------------------------
- `KOKORO82M_VOICES_DIR` → folder containing voice `*.pt` files (e.g., am_adam.pt)
- Optional for offline: `HF_HUB_OFFLINE=1` and `HF_HOME` to a local cache dir.

Packages
--------
`pip install --upgrade kokoro torch transformers sentencepiece tokenizers huggingface_hub safetensors unidecode sounddevice pyttsx3`

"""

import os
import sys
import queue
import threading
from enum import Enum
from typing import Optional, Tuple, List

# third‑party
try:
    import numpy as np
except Exception:  # pragma: no cover
    np = None  # type: ignore

try:
    import sounddevice as sd
except Exception:  # pragma: no cover
    sd = None  # type: ignore

try:
    import pyttsx3
except Exception:  # pragma: no cover
    pyttsx3 = None  # type: ignore

# kokoro / torch (guarded import so we can show clean errors)
_KOKORO_OK = True
_KOKORO_ERR: Optional[BaseException] = None
try:
    import torch
    from kokoro import KPipeline
except Exception as e:  # pragma: no cover
    _KOKORO_OK = False
    _KOKORO_ERR = e


class TTSBackend(str, Enum):
    SYSTEM = "system"
    KOKORO82M = "kokoro82m"


class TTSManager:
    def __init__(self,
                 backend: TTSBackend | str = TTSBackend.SYSTEM,
                 voice_hint: Optional[str] = None,
                 lang_code: str = "a",      # 'a' = American English in Kokoro
                 speed: float = 1.0) -> None:
        self._backend: TTSBackend = TTSBackend(backend)
        self._voice_hint = voice_hint
        self._lang_code = lang_code
        self._speed = float(speed)

        self._stop = threading.Event()
        self._q: "queue.Queue[tuple[str, Optional[str]]]" = queue.Queue()
        self._th = threading.Thread(target=self._worker, name="TTSWorker", daemon=True)

        # kokoro bits
        self._pipe: Optional[KPipeline] = None
        self._voices_dir: Optional[str] = None

        self._th.start()

    # ---------------- public API ----------------
    def set_backend(self, backend: TTSBackend | str) -> None:
        self._backend = TTSBackend(backend)
        # reset/late‑init pipeline on next use

    def set_voice_by_hint(self, hint: Optional[str]) -> None:
        self._voice_hint = (hint or None)

    def set_speed(self, speed: float) -> None:
        self._speed = max(0.5, min(2.0, float(speed)))

    def list_voices(self) -> List[str]:
        if self._backend == TTSBackend.KOKORO82M:
            d = os.environ.get("KOKORO82M_VOICES_DIR", "")
            out: List[str] = []
            if d and os.path.isdir(d):
                for fn in sorted(os.listdir(d)):
                    if fn.lower().endswith(".pt"):
                        out.append(os.path.splitext(fn)[0])
            return out or ["am_adam"]
        # system
        names: List[str] = []
        if pyttsx3 is not None:
            try:
                eng = pyttsx3.init()
                for v in eng.getProperty("voices"):
                    nm = getattr(v, "name", None)
                    if nm:
                        names.append(nm)
                eng.stop()
            except Exception:
                pass
        return names or ["Default"]

    def speak(self, text: str, voice_hint: Optional[str] = None) -> None:
        self._q.put((text, voice_hint))

    def stop(self) -> None:
        self._stop.set()
        try:
            self._q.put_nowait(("", None))
        except Exception:
            pass
        if self._th.is_alive():
            self._th.join(timeout=1.0)

    # ---------------- worker ----------------
    def _worker(self) -> None:
        while not self._stop.is_set():
            try:
                text, hint = self._q.get(timeout=0.1)
            except queue.Empty:
                continue
            if not text:
                continue
            try:
                if self._backend == TTSBackend.KOKORO82M and self._try_kokoro82m(text, hint):
                    continue
                self._system_say(text, hint)
            except Exception as e:
                print(f"[TTS] speak error: {e!r}")
                try:
                    self._system_say(text, hint)
                except Exception as e2:
                    print(f"[TTS] system fallback failed: {e2!r}")

    # ---------------- backends ----------------
    def _system_say(self, text: str, hint: Optional[str]) -> None:
        if pyttsx3 is None:
            raise RuntimeError("pyttsx3 not installed")
        eng = pyttsx3.init()
        try:
            want = (hint or self._voice_hint or "").lower()
            if want:
                try:
                    for v in eng.getProperty("voices"):
                        nm = getattr(v, "name", "").lower()
                        if want in nm:
                            eng.setProperty("voice", v.id)
                            break
                except Exception:
                    pass
            eng.say(text)
            eng.runAndWait()
        finally:
            try:
                eng.stop()
            except Exception:
                pass

    def _ensure_kokoro_ready(self) -> None:
        if not _KOKORO_OK:
            raise RuntimeError(f"kokoro import failed: {_KOKORO_ERR!r}")
        if sd is None:
            raise RuntimeError("sounddevice not installed")
        if np is None:
            raise RuntimeError("numpy not installed")
        if self._pipe is not None:
            return

        voices_dir = os.environ.get("KOKORO82M_VOICES_DIR", "")
        if not voices_dir or not os.path.isdir(voices_dir):
            raise FileNotFoundError("Set KOKORO82M_VOICES_DIR to a folder with *.pt voices")
        self._voices_dir = voices_dir

        # Let KPipeline build the correct model internally (no manual KModel)
        print(f"[TTS] Initializing Kokoro‑82M pipeline…")
        self._pipe = KPipeline(lang_code=self._lang_code)
        print(f"[TTS] Kokoro‑82M ready; voices: {self._voices_dir}")

    def _find_voice(self, hint: Optional[str]) -> Tuple[str, str]:
        assert self._voices_dir is not None
        want = (hint or self._voice_hint)
        if want:
            cand = os.path.join(self._voices_dir, f"{want}.pt")
            if os.path.isfile(cand):
                return want, cand
        for fn in sorted(os.listdir(self._voices_dir)):
            if fn.lower().endswith(".pt"):
                return os.path.splitext(fn)[0], os.path.join(self._voices_dir, fn)
        raise FileNotFoundError("No .pt voices found in KOKORO82M_VOICES_DIR")

    def _try_kokoro82m(self, text: str, hint: Optional[str]) -> bool:
        try:
            self._ensure_kokoro_ready()
        except Exception as e:
            print(f"[TTS] Kokoro‑82M init failed: {e}; using system.")
            return False

        assert self._pipe is not None and self._voices_dir is not None
        name, path = self._find_voice(hint)

        # Load local voice tensor
        try:
            try:
                voice_tensor = torch.load(path, weights_only=True)
            except TypeError:
                voice_tensor = torch.load(path)
        except Exception as e:
            print(f"[TTS] Kokoro‑82M voice load failed: {e}; using system.")
            return False

        # Run pipeline and play the concatenated audio
        try:
            chunks: List[np.ndarray] = []
            for _, _, audio in self._pipe(text, voice=voice_tensor, speed=self._speed):
                if isinstance(audio, (list, tuple)):
                    audio = np.asarray(audio, dtype=np.float32)
                chunks.append(audio)
            if not chunks:
                return False
            wav = chunks[0] if len(chunks) == 1 else np.concatenate(chunks, axis=0)
            import sounddevice as sd  # ensure default device resolves at call time
            sd.play(wav, samplerate=24000, blocking=True)
        except Exception as e:
            print(f"[TTS] Kokoro‑82M synthesis failed: {e!r}; using system.")
            return False

        print(f"[TTS] Kokoro‑82M voice applied: {name}")
        return True
