"""
serial_worker.py
==================

This module contains the definitions for the runtime configuration used by the
animatronic eyes and the serial communication worker.  The code here is
factored out of the original GUI script for readability and ease of reuse.

The ``DeviceConfig`` class holds the runtime parameters such as per‑servo
mapping limits, inversion flags, trim offsets and tweening settings.  It
provides a convenience method ``angle_to_us`` which converts an angle in
degrees into the appropriate microsecond pulse width for the PCA9685 driver.

The ``SerialWorker`` class manages a single serial port connection in its own
thread.  It exposes Qt signals to report connection status, incoming lines
from the device, and error messages.  Outbound lines are queued from the
GUI thread and drained from the worker thread to avoid blocking the user
interface.
"""

from __future__ import annotations

import queue
import threading
from dataclasses import dataclass, field
from typing import List, Optional

from PySide6.QtCore import QObject, Signal, Slot

import serial
import serial.tools.list_ports

BAUD = 115200

# Servo identifiers.  Order matches the hardware indices used on the device.
SERVO_IDS = ["LX", "LY", "RX", "RY"]
IDX = {"LX": 0, "LY": 1, "RX": 2, "RY": 3}


@dataclass
class DeviceConfig:
    """Container for servo configuration and runtime state.

    The firmware exposes min/max pulse widths per servo, a trim (offset) in
    degrees, an invert flag, update frequency and tweening parameters.  Those
    values live here so that the GUI can make reasonable predictions about
    what microseconds will be sent for a given angle without querying the
    device constantly.  ``target`` and ``current`` store the target and
    current angles returned by the firmware via ``GET`` responses.
    """

    min_us: List[int] = field(default_factory=lambda: [500, 500, 500, 500])
    max_us: List[int] = field(default_factory=lambda: [2500, 2500, 2500, 2500])
    trim_deg: List[int] = field(default_factory=lambda: [0, 0, 0, 0])
    invert: List[int] = field(default_factory=lambda: [0, 0, 1, 1])
    freq_hz: int = 50
    step_deg: int = 2
    step_ms: int = 10
    target: List[int] = field(default_factory=lambda: [90, 90, 90, 90])
    current: List[int] = field(default_factory=lambda: [90, 90, 90, 90])

    def angle_to_us(self, idx: int, angle: int) -> int:
        """Convert a logical servo angle (0–180°) into a microsecond pulse.

        The firmware applies inversion and trim before mapping the resulting
        logical angle into the configured microsecond range.  The same
        calculation is replicated here so that the GUI can display the
        approximate microseconds that will be used.  Values are clamped to
        0–180°.
        """
        angle = max(0, min(180, int(angle)))
        # Mirror the motion if the servo is inverted.
        if self.invert[idx]:
            angle = 180 - angle
        # Apply the trim offset and clamp again.
        angle = max(0, min(180, angle + int(self.trim_deg[idx])))
        mi, ma = int(self.min_us[idx]), int(self.max_us[idx])
        us = mi + (ma - mi) * angle / 180.0
        return int(round(us))


class SerialWorker(QObject):
    """Background worker that handles serial I/O without blocking the GUI.

    A ``SerialWorker`` instance lives in its own Qt thread but spins up a
    dedicated Python thread for actual blocking serial I/O operations.  The
    Qt thread remains free to dispatch signals from the serial thread back
    into the GUI.  Outgoing lines are pushed into a thread‑safe queue by
    ``send_line`` and drained by the serial thread.
    """

    connected = Signal(str)
    disconnected = Signal()
    line_received = Signal(str)
    error = Signal(str)
    debug = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self._ser: Optional[serial.Serial] = None
        self._running = False
        self._txq: "queue.Queue[str]" = queue.Queue()
        self._io_thread: Optional[threading.Thread] = None
        self._log_hex = False

    @Slot(bool)
    def set_hex_logging(self, enabled: bool) -> None:
        """Toggle hex logging for transmitted bytes."""
        self._log_hex = bool(enabled)

    @Slot(str)
    def send_line(self, line: str) -> None:
        """Enqueue a line (including EOL) for transmission to the device."""
        try:
            self._txq.put_nowait(line)
        except Exception as e:
            self.error.emit(f"Queue error: {e}")

    @Slot(str)
    def start(self, port: str) -> None:
        """Open the given serial port and start the I/O thread.

        If the port cannot be opened, an error signal is emitted.  On a
        successful open the ``connected`` signal carries the port name
        back to the GUI.
        """
        try:
            self._ser = serial.Serial(port, BAUD, timeout=0.05)
        except Exception as e:
            self._ser = None
            self.error.emit(f"Open failed: {e}")
            return
        self._running = True
        self.connected.emit(port)
        self._io_thread = threading.Thread(target=self._io_loop, name="SerialIO", daemon=True)
        self._io_thread.start()

    def _io_loop(self) -> None:
        """Run the blocking serial read/write loop until stopped."""
        buf = bytearray()
        ser = self._ser
        try:
            while self._running and ser and ser.is_open:
                # Drain the transmit queue as quickly as possible.
                try:
                    while True:
                        payload = self._txq.get_nowait()
                        data = payload.encode("utf-8", errors="ignore")
                        ser.write(data)
                        ser.flush()
                        if self._log_hex:
                            self.debug.emit("TX BYTES: " + ' '.join(f"{b:02X}" for b in data))
                except queue.Empty:
                    pass
                except Exception as e:
                    self.error.emit(f"Write error: {e}")
                    break
                # Read any available bytes and assemble complete lines.
                try:
                    chunk = ser.read(256)
                    if chunk:
                        for b in chunk:
                            if b == 10:  # LF terminates a line
                                line = buf.decode(errors="ignore")
                                buf.clear()
                                # Strip trailing CR if present
                                if line and line[-1] == '\r':
                                    line = line[:-1]
                                self.line_received.emit(line)
                            else:
                                buf.append(b)
                except Exception as e:
                    self.error.emit(f"Read error: {e}")
                    break
        finally:
            # Attempt to close the port gracefully.
            try:
                if ser and ser.is_open:
                    ser.close()
            except Exception:
                pass
            self._ser = None
            self._running = False
            self.disconnected.emit()

    @Slot()
    def stop(self) -> None:
        """Signal the I/O loop to exit and wait for the thread to finish."""
        self._running = False
        try:
            if self._ser and self._ser.is_open:
                self._ser.close()  # unblock read()
        except Exception:
            pass
        t = self._io_thread
        if t and t.is_alive():
            t.join(timeout=1.5)
        self._io_thread = None


def available_ports() -> List[str]:
    """Return a list of available serial ports on the system."""
    return [p.device for p in serial.tools.list_ports.comports()]