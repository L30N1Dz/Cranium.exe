"""Serial port manager for communicating servo angles to a microcontroller.

This module wraps the PySerial library to provide a simple API for
opening and closing a serial connection and sending angle commands.

Commands are sent in the format `SET X <deg>` followed by `SET Y <deg>`. To
prevent saturating the MCU with redundant data, successive calls
within a minimal interval (default 25 Hz) and negligible changes in
angles are ignored.
"""

from __future__ import annotations

import time
import serial


class SerialManager:
    """Manage a serial connection for sending angle updates.

    This class encapsulates connection management and rate limiting
    for sending angle commands to a microcontroller. It can be
    configured to ignore small changes and to enforce a minimum
    interval between transmissions.
    """

    def __init__(self):
        self.ser: serial.Serial | None = None
        self.is_open: bool = False
        self._last_x: int | None = None
        self._last_y: int | None = None
        self._last_send: float = 0.0
        # Minimum interval between transmissions (seconds)
        self.min_interval: float = 0.04  # 25 Hz

    def open(self, port: str, baud: int = 115200) -> None:
        """Open a serial port.

        Any existing connection will be closed before opening a new one.

        :param port: Name of the serial port (e.g., 'COM3' or '/dev/ttyUSB0').
        :param baud: Baud rate for the connection. Defaults to 115200.
        """
        self.close()
        self.ser = serial.Serial(port=port, baudrate=baud, timeout=0)
        self.is_open = True

    def close(self) -> None:
        """Close the serial port if it is open."""
        if self.ser and self.ser.is_open:
            self.ser.close()
        self.is_open = False
        self.ser = None

    def _write_line(self, line: str) -> None:
        """Write a single line to the serial port.

        A newline will be appended automatically. If writing fails,
        the connection will be closed.
        """
        if not self.is_open:
            return
        try:
            self.ser.write((line + "\n").encode("ascii"))
        except Exception:
            # In case of any I/O error, close the port to avoid undefined state
            self.close()

    def send_set_angles(self, x_deg: int, y_deg: int, threshold: int = 1) -> None:
        """Send angle commands for X and Y axes.

        The command format is `SET X <deg>` followed by `SET Y <deg>`. The
        method applies rate limiting and skips sending if the change
        from the last sent angles is below the specified threshold.

        :param x_deg: Target X-axis angle in degrees (0–180).
        :param y_deg: Target Y-axis angle in degrees (0–180).
        :param threshold: Minimum change in degrees to trigger a new
            transmission. Defaults to 1.
        """
        now = time.time()
        # Enforce minimal interval between sends
        if now - self._last_send < self.min_interval:
            return
        # Skip sending if change is below threshold
        if self._last_x is not None and abs(x_deg - self._last_x) < threshold and \
           self._last_y is not None and abs(y_deg - self._last_y) < threshold:
            return
        self._write_line(f"SET X {int(x_deg)}")
        self._write_line(f"SET Y {int(y_deg)}")
        self._last_send = now
        self._last_x = x_deg
        self._last_y = y_deg