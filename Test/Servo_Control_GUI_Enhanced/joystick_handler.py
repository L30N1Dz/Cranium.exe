"""
joystick_handler.py
====================

Provides a small abstraction for reading joystick axes on both Windows and
Linux.  The code attempts to import Qt's gamepad support via
``PySide6.QtGamepad`` first.  If that is unavailable it falls back to
``pygame``.  When neither backend is present the handler will silently
disable itself.

The class exposes a Qt signal ``update`` carrying four floating point
values corresponding to the desired X and Y angles for the left and right
eyes.  Angles are normalised into the 0–180° range before emission.  A
separate thread performs polling at roughly 30 Hz so that the GUI
remains responsive.

Usage::

    handler = JoystickHandler()
    handler.update.connect(on_update)
    devices = handler.list_devices()
    handler.start(index=0, sync=True)
    ...
    handler.stop()

If ``sync`` is true the left eye values are applied to both eyes.  When
the joystick has fewer than four axes only the left eye values are used.
"""

from __future__ import annotations

import sys
import time
import threading
from typing import List, Optional

from PySide6.QtCore import QObject, Signal


class JoystickHandler(QObject):
    # Emitted whenever new joystick data is available.  Arguments are
    # (lx_angle, ly_angle, rx_angle, ry_angle) in degrees (0–180).
    update = Signal(float, float, float, float)
    started = Signal()
    stopped = Signal()
    error = Signal(str)

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._device_index = 0
        self._sync = True
        # Determine which backend to use lazily
        self._backend: Optional[str] = None
        self._device: Optional[object] = None

    @staticmethod
    def _detect_backend() -> Optional[str]:
        """Return the name of an available joystick backend or ``None``."""
        # Prefer Qt Gamepad if available
        try:
            from PySide6.QtGamepad import QGamepad, QGamepadManager  # type: ignore
            _ = QGamepadManager.instance()
            return "qt"
        except Exception:
            pass
        # Fall back to pygame
        try:
            import pygame  # type: ignore
            return "pygame"
        except Exception:
            pass
        return None

    @staticmethod
    def list_devices() -> List[str]:
        """Return a list of human readable names for available joysticks."""
        backend = JoystickHandler._detect_backend()
        if backend == "qt":
            try:
                from PySide6.QtGamepad import QGamepadManager  # type: ignore
                mgr = QGamepadManager.instance()
                ids = mgr.connectedGamepads()
                names = []
                for jid in ids:
                    # QGamepadManager provides vendor/product strings
                    names.append(f"Gamepad {jid}")
                return names
            except Exception:
                return []
        elif backend == "pygame":
            try:
                import pygame  # type: ignore
                pygame.joystick.init()
                names: List[str] = []
                for i in range(pygame.joystick.get_count()):
                    js = pygame.joystick.Joystick(i)
                    js.init()
                    names.append(js.get_name())
                return names
            except Exception:
                return []
        else:
            return []

    def start(self, index: int = 0, *, sync: bool = True) -> None:
        """Start polling the joystick at the given index.

        If no backend is available the handler emits an error and returns.
        Multiple calls to ``start`` will restart polling with the new
        parameters.  To stop polling call :meth:`stop`.
        """
        self._device_index = index
        self._sync = bool(sync)
        # Stop any existing polling
        self.stop()
        # Determine backend
        backend = self._detect_backend()
        if backend is None:
            self.error.emit("No joystick backend available (missing QtGamepad or pygame)")
            return
        self._backend = backend
        # Prepare device specific state lazily in polling thread
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, name="JoystickPoll", daemon=True)
        self._thread.start()
        self.started.emit()

    def stop(self) -> None:
        """Stop polling the joystick and wait for the thread to finish."""
        if not self._running:
            return
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.5)
        self._thread = None
        self.stopped.emit()

    def set_sync(self, sync: bool) -> None:
        """Enable or disable synchronised eye movement."""
        self._sync = bool(sync)

    def _run_loop(self) -> None:
        """Main polling loop for the joystick."""
        backend = self._backend
        if backend is None:
            return
        if backend == "qt":
            self._poll_qt_gamepad()
        elif backend == "pygame":
            self._poll_pygame()

    # --- Qt Gamepad backend ---
    def _poll_qt_gamepad(self) -> None:
        try:
            from PySide6.QtGamepad import QGamepad, QGamepadManager  # type: ignore
        except Exception:
            self.error.emit("QtGamepad backend unavailable")
            return
        mgr = QGamepadManager.instance()
        ids = mgr.connectedGamepads()
        if not ids:
            self.error.emit("No Qt gamepad connected")
            return
        # Clamp index
        idx = max(0, min(self._device_index, len(ids) - 1))
        pad_id = ids[idx]
        gamepad = QGamepad(pad_id, self)
        # Poll axes until stopped
        while self._running:
            # QGamepad normalises axis values to −1..+1
            lx = gamepad.axisLeftX()
            ly = gamepad.axisLeftY()
            rx = gamepad.axisRightX()
            ry = gamepad.axisRightY()
            if self._sync:
                rx, ry = lx, ly
            # Map to degrees
            lx_deg = self._normalised_to_deg(lx)
            ly_deg = self._normalised_to_deg(-ly)  # Invert Y
            rx_deg = self._normalised_to_deg(rx)
            ry_deg = self._normalised_to_deg(-ry)
            self.update.emit(lx_deg, ly_deg, rx_deg, ry_deg)
            # Sleep briefly to avoid saturating CPU
            time.sleep(0.03)

    # --- pygame backend ---
    def _poll_pygame(self) -> None:
        try:
            import pygame  # type: ignore
        except Exception:
            self.error.emit("pygame backend unavailable")
            return
        pygame.init()
        pygame.joystick.init()
        count = pygame.joystick.get_count()
        if count == 0:
            self.error.emit("No pygame joystick connected")
            return
        idx = max(0, min(self._device_index, count - 1))
        js = pygame.joystick.Joystick(idx)
        try:
            js.init()
        except Exception as e:
            self.error.emit(f"Joystick init failed: {e}")
            return
        while self._running:
            # Pygame requires pumping the event queue to update values
            try:
                pygame.event.pump()
            except Exception:
                pass
            # Read axes with fallbacks
            def axis_safe(i: int) -> float:
                try:
                    return js.get_axis(i)
                except Exception:
                    return 0.0
            lx = axis_safe(0)
            ly = axis_safe(1)
            rx = axis_safe(2)
            ry = axis_safe(3)
            if self._sync:
                rx, ry = lx, ly
            lx_deg = self._normalised_to_deg(lx)
            ly_deg = self._normalised_to_deg(-ly)  # Invert Y for consistency
            rx_deg = self._normalised_to_deg(rx)
            ry_deg = self._normalised_to_deg(-ry)
            self.update.emit(lx_deg, ly_deg, rx_deg, ry_deg)
            time.sleep(0.03)

    @staticmethod
    def _normalised_to_deg(value: float) -> float:
        """Map a normalised axis value (−1..+1) to a 0–180 degree angle."""
        # Clamp to [-1,1]
        v = max(-1.0, min(1.0, float(value)))
        return (v + 1.0) / 2.0 * 180.0