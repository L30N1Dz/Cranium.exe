"""
Servo_Control_Gui_enhanced.py
=============================

An improved version of the original animatronic eyes controller GUI.  This
variant introduces a dark theme, per‑servo mapping controls, convenient
buttons to set servo limits and centres, visualisation of eye
orientations and optional joystick control.  The core serial handling and
configuration structures have been factored out into separate modules for
readability.

Run this script with ``python Servo_Control_Gui_enhanced.py``.  It
requires PySide6, pyserial and optionally PySide6.QtGamepad or pygame for
joystick support.
"""

from __future__ import annotations

import json
import re
import sys
import time
from typing import Dict, List, Optional

from PySide6.QtCore import Qt, QTimer, QObject, QThread, Signal, Slot
from PySide6.QtGui import QPalette, QColor
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QComboBox,
    QGroupBox,
    QSlider,
    QSpinBox,
    QTextEdit,
    QGridLayout,
    QCheckBox,
    QMessageBox,
)

from serial_worker import SerialWorker, DeviceConfig, SERVO_IDS, IDX, available_ports
from settings_dialog import SettingsDialog
from visualization_widget import EyeVisualizer
from joystick_handler import JoystickHandler
from theme import apply_dark_theme

class MainWindow(QMainWindow):
    start_serial = Signal(str)
    send_serial = Signal(str)
    stop_serial = Signal()
    set_hexlog = Signal(bool)

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Animatronic Eyes Controller (Enhanced)")
        self.cfg = DeviceConfig()
        # Serial worker lives in its own Qt thread; I/O in Python thread
        self.worker = SerialWorker()
        self.thread = QThread(self)
        self.worker.moveToThread(self.thread)
        self.thread.start()
        self.start_serial.connect(self.worker.start)
        self.send_serial.connect(self.worker.send_line)
        self.stop_serial.connect(self.worker.stop)
        self.set_hexlog.connect(self.worker.set_hex_logging)

        # Joystick handler
        self.joystick = JoystickHandler()
        self.joystick.update.connect(self._on_joystick_update)
        self.joystick.error.connect(lambda msg: self.console.append(f"<span style='color:#c66;'>JOYSTICK ERROR:</span> {msg}"))

        # Build UI
        self._build_ui()
        # Wire worker signals
        self.worker.connected.connect(self.on_connected)
        self.worker.disconnected.connect(self.on_disconnected)
        self.worker.line_received.connect(self.on_line)
        self.worker.error.connect(self.on_error)
        self.worker.debug.connect(self.console.append)

        # Timers for polling and rate limiting
        self._last_tx = time.monotonic()
        self.poll = QTimer(self); self.poll.setInterval(1000); self.poll.timeout.connect(self._poll_tick)
        self._pending: Dict[str, str] = {}
        self.tx_timer = QTimer(self); self.tx_timer.setInterval(self.rate_spin.value())
        self.tx_timer.timeout.connect(self._flush_pending); self.tx_timer.start()
        self.rate_spin.valueChanged.connect(self.tx_timer.setInterval)

        # Populate initial port list and update µs labels
        self.refresh_ports()
        self._update_us_labels()

    # ---- UI construction ----
    def _build_ui(self) -> None:
        central = QWidget(); outer = QVBoxLayout(central)

        # Top row: serial and general controls
        top = QWidget(); top_l = QHBoxLayout(top)
        self.port_box = QComboBox(); self.refresh_btn = QPushButton("Refresh")
        self.connect_btn = QPushButton("Connect")
        self.center_btn = QPushButton("Center")
        self.settings_btn = QPushButton("Settings…")
        self.save_btn = QPushButton("SAVE (EEPROM)")
        self.load_btn = QPushButton("LOAD")
        self.reset_btn = QPushButton("RESETCFG")
        self.status_lbl = QLabel("Disconnected")
        top_l.addWidget(QLabel("Port:")); top_l.addWidget(self.port_box, 1)
        top_l.addWidget(self.refresh_btn); top_l.addWidget(self.connect_btn)
        top_l.addStretch(1); top_l.addWidget(self.center_btn); top_l.addWidget(self.settings_btn)
        top_l.addWidget(self.save_btn); top_l.addWidget(self.load_btn); top_l.addWidget(self.reset_btn)
        top_l.addWidget(self.status_lbl)
        # TX rate and EOL and hex log
        # Rate at which queued commands are sent (ms)
        self.rate_spin = QSpinBox(); self.rate_spin.setRange(10, 200); self.rate_spin.setValue(30)
        top_l.addWidget(QLabel("Cmd rate (ms):")); top_l.addWidget(self.rate_spin)
        self.eol_box = QComboBox(); self.eol_box.addItems(["CRLF (\\r\\n)", "LF (\\n)"])
        self.eol_box.setCurrentIndex(1)
        top_l.addWidget(QLabel("EOL:")); top_l.addWidget(self.eol_box)
        self.hex_cb = QCheckBox("Log TX bytes (hex)"); self.hex_cb.setChecked(False)
        self.hex_cb.toggled.connect(self.set_hexlog)
        top_l.addWidget(self.hex_cb)
        # Status polling toggle (when unchecked GET is not sent periodically)
        self.poll_cb = QCheckBox("Status Polling")
        self.poll_cb.setChecked(True)
        top_l.addWidget(self.poll_cb)
        outer.addWidget(top)

        # X Axis group (controls LX and RX)
        x_group = QGroupBox("X Axis (SET X)"); x_l = QGridLayout(x_group)
        self.x_slider = QSlider(Qt.Horizontal); self.x_slider.setRange(0, 180); self.x_slider.setValue(90)
        self.x_spin = QSpinBox(); self.x_spin.setRange(0, 180); self.x_spin.setValue(90)
        self.x_lx_us = QLabel("LX: -- µs"); self.x_rx_us = QLabel("RX: -- µs"); self.x_tgt = QLabel("X target: 90")
        # Limit buttons
        self.x_set_min_btn = QPushButton("Set Min")
        self.x_set_max_btn = QPushButton("Set Max")
        self.x_reset_limit_btn = QPushButton("Reset Limits")
        # Layout rows
        x_l.addWidget(self.x_slider, 0, 0, 1, 6)
        x_l.addWidget(QLabel("X:"), 1, 0); x_l.addWidget(self.x_spin, 1, 1)
        x_l.addWidget(self.x_lx_us, 1, 2); x_l.addWidget(self.x_rx_us, 1, 3)
        x_l.addWidget(self.x_tgt, 2, 0, 1, 2)
        # Limit controls row
        x_l.addWidget(self.x_set_min_btn, 2, 2)
        x_l.addWidget(self.x_set_max_btn, 2, 3)
        x_l.addWidget(self.x_reset_limit_btn, 2, 4)
        outer.addWidget(x_group)

        # Y Axis group (controls LY and RY)
        y_group = QGroupBox("Y Axis (SET Y)"); y_l = QGridLayout(y_group)
        self.y_slider = QSlider(Qt.Horizontal); self.y_slider.setRange(0, 180); self.y_slider.setValue(90)
        self.y_spin = QSpinBox(); self.y_spin.setRange(0, 180); self.y_spin.setValue(90)
        self.y_ly_us = QLabel("LY: -- µs"); self.y_ry_us = QLabel("RY: -- µs"); self.y_tgt = QLabel("Y target: 90")
        self.y_set_min_btn = QPushButton("Set Min"); self.y_set_max_btn = QPushButton("Set Max"); self.y_reset_limit_btn = QPushButton("Reset Limits")
        y_l.addWidget(self.y_slider, 0, 0, 1, 6)
        y_l.addWidget(QLabel("Y:"), 1, 0); y_l.addWidget(self.y_spin, 1, 1)
        y_l.addWidget(self.y_ly_us, 1, 2); y_l.addWidget(self.y_ry_us, 1, 3)
        y_l.addWidget(self.y_tgt, 2, 0, 1, 2)
        y_l.addWidget(self.y_set_min_btn, 2, 2)
        y_l.addWidget(self.y_set_max_btn, 2, 3)
        y_l.addWidget(self.y_reset_limit_btn, 2, 4)
        outer.addWidget(y_group)

        # Move speed control – vertical slider controlling tween speed
        speed_group = QGroupBox("Move Speed")
        speed_layout = QVBoxLayout(speed_group)
        self.speed_slider = QSlider(Qt.Vertical)
        self.speed_slider.setRange(0, 100)
        # Initialise slider based on current tween settings (rough mapping)
        # Map existing cfg.step_deg and cfg.step_ms into 0–100 range: high speed when step_deg high and step_ms low
        # Avoid division by zero by using defaults if zero
        try:
            # Normalise to approximate speed value
            speed_val = int((self.cfg.step_deg / 10.0) * 50 + ((200 - self.cfg.step_ms) / 200.0) * 50)
        except Exception:
            speed_val = 50
        self.speed_slider.setValue(min(100, max(0, speed_val)))
        self.speed_slider.setTickInterval(10); self.speed_slider.setTickPosition(QSlider.TicksBothSides)
        self.speed_slider.valueChanged.connect(self._speed_changed)
        self.speed_label = QLabel("Speed")
        self.speed_value_label = QLabel(str(self.speed_slider.value()))
        speed_layout.addWidget(self.speed_label, alignment=Qt.AlignHCenter)
        speed_layout.addWidget(self.speed_slider, alignment=Qt.AlignHCenter)
        speed_layout.addWidget(self.speed_value_label, alignment=Qt.AlignHCenter)
        outer.addWidget(speed_group)

        # Per‑servo controls with set centre buttons
        s_group = QGroupBox("Per‑Servo Controls (SET <ID>)"); s_l = QGridLayout(s_group)
        self.s_sliders: Dict[str, QSlider] = {}
        self.s_spins: Dict[str, QSpinBox] = {}
        self.s_us: Dict[str, QLabel] = {}
        self.s_center_btn: Dict[str, QPushButton] = {}
        row = 0
        for sid in SERVO_IDS:
            s_l.addWidget(QLabel(sid), row, 0)
            sl = QSlider(Qt.Horizontal); sl.setRange(0, 180); sl.setValue(90); self.s_sliders[sid] = sl
            sp = QSpinBox(); sp.setRange(0, 180); sp.setValue(90); self.s_spins[sid] = sp
            lab = QLabel("-- µs"); self.s_us[sid] = lab
            center_btn = QPushButton("Set Center"); self.s_center_btn[sid] = center_btn
            s_l.addWidget(sl, row, 1); s_l.addWidget(sp, row, 2); s_l.addWidget(lab, row, 3); s_l.addWidget(center_btn, row, 4)
            row += 1
        outer.addWidget(s_group)

        # Visualisation group
        vis_group = QGroupBox("Visualisation"); vis_l = QHBoxLayout(vis_group)
        self.left_eye_vis = EyeVisualizer(); self.right_eye_vis = EyeVisualizer()
        vis_l.addWidget(QLabel("Left Eye")); vis_l.addWidget(self.left_eye_vis)
        vis_l.addWidget(QLabel("Right Eye")); vis_l.addWidget(self.right_eye_vis)
        outer.addWidget(vis_group)

        # Joystick group
        joy_group = QGroupBox("Joystick Control"); joy_l = QHBoxLayout(joy_group)
        self.joy_enable_cb = QCheckBox("Enable Joystick")
        self.joy_sync_cb = QCheckBox("Sync Eyes"); self.joy_sync_cb.setChecked(True)
        self.joy_device_box = QComboBox(); self.joy_refresh_btn = QPushButton("Refresh")
        # Populate joystick devices
        self._refresh_joystick_devices()
        joy_l.addWidget(self.joy_enable_cb)
        joy_l.addWidget(self.joy_sync_cb)
        joy_l.addWidget(QLabel("Device:")); joy_l.addWidget(self.joy_device_box)
        joy_l.addWidget(self.joy_refresh_btn)
        outer.addWidget(joy_group)

        # Console log
        outer.addWidget(QLabel("Device Log:"))
        self.console = QTextEdit(); self.console.setReadOnly(True)
        outer.addWidget(self.console, 1)
        self.setCentralWidget(central)

        # Connect UI events
        self.refresh_btn.clicked.connect(self.refresh_ports)
        self.connect_btn.clicked.connect(self.toggle_connect)
        self.center_btn.clicked.connect(self._do_center)
        self.settings_btn.clicked.connect(self.open_settings)
        self.save_btn.clicked.connect(self._save_to_device)
        self.load_btn.clicked.connect(lambda: self._send_line("LOAD"))
        self.reset_btn.clicked.connect(lambda: self._send_line("RESETCFG"))
        self.eol_box.currentIndexChanged.connect(lambda: None)  # placeholder for EOL

        # Polling toggle
        self.poll_cb.toggled.connect(self._toggle_polling)

        self.x_slider.valueChanged.connect(self._x_slider_changed)
        self.x_spin.valueChanged.connect(self._x_spin_changed)
        self.x_set_min_btn.clicked.connect(lambda: self._set_axis_limit(axis='X', which='min'))
        self.x_set_max_btn.clicked.connect(lambda: self._set_axis_limit(axis='X', which='max'))
        self.x_reset_limit_btn.clicked.connect(lambda: self._reset_axis_limit(axis='X'))
        self.y_slider.valueChanged.connect(self._y_slider_changed)
        self.y_spin.valueChanged.connect(self._y_spin_changed)
        self.y_set_min_btn.clicked.connect(lambda: self._set_axis_limit(axis='Y', which='min'))
        self.y_set_max_btn.clicked.connect(lambda: self._set_axis_limit(axis='Y', which='max'))
        self.y_reset_limit_btn.clicked.connect(lambda: self._reset_axis_limit(axis='Y'))
        for sid in SERVO_IDS:
            self.s_sliders[sid].valueChanged.connect(lambda val, s=sid: self._servo_slider_changed(s, val))
            self.s_spins[sid].valueChanged.connect(lambda val, s=sid: self._servo_spin_changed(s, val))
            self.s_center_btn[sid].clicked.connect(lambda chk=False, s=sid: self._set_servo_center(s))
        # Joystick events
        self.joy_enable_cb.toggled.connect(self._toggle_joystick)
        self.joy_sync_cb.toggled.connect(lambda enabled: self.joystick.set_sync(enabled))
        self.joy_refresh_btn.clicked.connect(self._refresh_joystick_devices)
        self.joy_device_box.currentIndexChanged.connect(self._on_joystick_device_changed)

    # ---- Serial port helpers ----
    def _apply_eol(self, line: str) -> str:
        if self.eol_box.currentIndex() == 0:  # CRLF
            return line.rstrip("\r\n") + "\r\n"
        else:
            return line.rstrip("\r\n") + "\n"

    def refresh_ports(self) -> None:
        cur = self.port_box.currentText()
        ports = available_ports()
        self.port_box.blockSignals(True); self.port_box.clear(); self.port_box.addItems(ports)
        if cur in ports:
            self.port_box.setCurrentText(cur)
        self.port_box.blockSignals(False)

    def toggle_connect(self) -> None:
        if self.connect_btn.text() == "Connect":
            port = self.port_box.currentText().strip()
            if not port:
                QMessageBox.warning(self, "Connect", "Select a serial port.")
                return
            self.start_serial.emit(port)
            self.connect_btn.setEnabled(False)
        else:
            self.stop_serial.emit()
            self.connect_btn.setEnabled(False)

    @Slot(str)
    def on_connected(self, port: str) -> None:
        self.status_lbl.setText(f"Connected: {port}")
        self.connect_btn.setText("Disconnect"); self.connect_btn.setEnabled(True)
        if self.poll_cb.isChecked():
            self.poll.start()
        # Always ask for a snapshot on connect
        self._send_line("GET")

    @Slot()
    def on_disconnected(self) -> None:
        self.status_lbl.setText("Disconnected")
        self.connect_btn.setText("Connect"); self.connect_btn.setEnabled(True)
        self.poll.stop()

    @Slot(str)
    def on_error(self, msg: str) -> None:
        self.console.append(f"<span style='color:#c66;'>ERROR:</span> {msg}")
        self.status_lbl.setText("Error")
        self.poll.stop()
        self.connect_btn.setText("Connect"); self.connect_btn.setEnabled(True)
        self.stop_serial.emit()

    # ---- Command sending & rate limiting ----
    def _queue_cmd(self, key: str, line: str) -> None:
        self._pending[key] = line

    def _flush_pending(self) -> None:
        if not self._pending:
            return
        items = list(self._pending.values()); self._pending.clear()
        for line in items:
            self._send_line(line)

    def _send_line(self, line: str) -> None:
        wire = self._apply_eol(line)
        self.send_serial.emit(wire)
        self._last_tx = time.monotonic()
        if line.strip().upper() != "GET":
            self.console.append(f"TX: {line}")

    def _poll_tick(self) -> None:
        if not self.poll_cb.isChecked():
            return
        if (time.monotonic() - getattr(self, "_last_tx", 0)) >= 1.0:
            self._send_line("GET")

    def _toggle_polling(self, enabled: bool) -> None:
        """Enable or disable the periodic status polling."""
        if enabled:
            # Immediately send a GET when enabling
            self._send_line("GET")
        else:
            # When disabling polling do nothing; state will persist
            pass

    # ---- Speed control ----
    def _speed_changed(self, val: int) -> None:
        """Adjust the servo movement smoothing based on slider value."""
        # Update numeric label
        self.speed_value_label.setText(str(val))
        # Map slider 0–100 to tween parameters.  Higher value means faster movement.
        # Compute step_deg between 1 and 10
        step_deg = 1 + int((val / 100.0) * 9)
        # Compute interval_ms between 200 (slow) and 0 (fast)
        interval_ms = int((100 - val) / 100.0 * 200)
        # Update local configuration
        self.cfg.step_deg = step_deg
        self.cfg.step_ms = interval_ms
        # Send tween update to the device
        payload = {"tween": {"step_deg": step_deg, "interval_ms": interval_ms}}
        try:
            line = json.dumps(payload)
            self._send_line(line)
        except Exception as e:
            self.console.append(f"<span style='color:#c66;'>Tween Error:</span> {e}")

    # ---- X/Y paired sliders/spins ----
    def _x_slider_changed(self, val: int) -> None:
        if self.x_spin.value() != val:
            self.x_spin.blockSignals(True); self.x_spin.setValue(val); self.x_spin.blockSignals(False)
        self.x_tgt.setText(f"X target: {val}")
        self._update_us_labels()
        self._queue_cmd("X", f"SET X {val}")
    def _x_spin_changed(self, val: int) -> None:
        if self.x_slider.value() != val:
            self.x_slider.blockSignals(True); self.x_slider.setValue(val); self.x_slider.blockSignals(False)
        self._x_slider_changed(val)
    def _y_slider_changed(self, val: int) -> None:
        if self.y_spin.value() != val:
            self.y_spin.blockSignals(True); self.y_spin.setValue(val); self.y_spin.blockSignals(False)
        self.y_tgt.setText(f"Y target: {val}")
        self._update_us_labels()
        self._queue_cmd("Y", f"SET Y {val}")
    def _y_spin_changed(self, val: int) -> None:
        if self.y_slider.value() != val:
            self.y_slider.blockSignals(True); self.y_slider.setValue(val); self.y_slider.blockSignals(False)
        self._y_slider_changed(val)

    # ---- Per‑servo sliders/spins ----
    def _servo_slider_changed(self, sid: str, val: int) -> None:
        sp = self.s_spins[sid]
        if sp.value() != val:
            sp.blockSignals(True); sp.setValue(val); sp.blockSignals(False)
        self._update_us_labels()
        self._queue_cmd(sid, f"SET {sid} {val}")
    def _servo_spin_changed(self, sid: str, val: int) -> None:
        sl = self.s_sliders[sid]
        if sl.value() != val:
            sl.blockSignals(True); sl.setValue(val); sl.blockSignals(False)
        self._servo_slider_changed(sid, val)

    def _do_center(self) -> None:
        """Centre all servos (sets targets to 90°)."""
        self._send_line("CENTER")

    # ---- Limit/centre helpers ----
    def _set_servo_center(self, sid: str) -> None:
        """Compute and apply a trim so that the current angle becomes 90°."""
        try:
            current_angle = self.s_spins[sid].value()
            # The difference between desired centre and current angle
            diff = 90 - current_angle
            # Update trim in config and send TRIM command
            self.cfg.trim_deg[IDX[sid]] += diff
            # Clamp trim values to [-90,90]
            self.cfg.trim_deg[IDX[sid]] = max(-90, min(90, self.cfg.trim_deg[IDX[sid]]))
            self._update_us_labels()
            # Send TRIM <ID> <value>
            line = f"TRIM {sid} {self.cfg.trim_deg[IDX[sid]]}"
            self._send_line(line)
            self.console.append(f"<i>Adjusted trim for {sid} by {diff}°</i>")
        except Exception as e:
            self.console.append(f"<span style='color:#c66;'>Trim Error:</span> {e}")

    def _set_axis_limit(self, *, axis: str, which: str) -> None:
        """Set min or max pulse width for the given axis based on current value."""
        try:
            if axis.upper() == 'X':
                # Use left (LX) and right (RX) channels
                idxs = [IDX['LX'], IDX['RX']]
                angle = self.x_spin.value()
            else:
                idxs = [IDX['LY'], IDX['RY']]
                angle = self.y_spin.value()
            # Compute µs for current angle using current config
            us_values = [self.cfg.angle_to_us(i, angle) for i in idxs]
            for i, us_val in zip(idxs, us_values):
                if which == 'min':
                    self.cfg.min_us[i] = us_val
                elif which == 'max':
                    self.cfg.max_us[i] = us_val
            # Build and send MAP command for each servo
            for sid in [SERVO_IDS[i] for i in idxs]:
                mi, ma = self.cfg.min_us[IDX[sid]], self.cfg.max_us[IDX[sid]]
                self._send_line(f"MAP {sid} {mi} {ma}")
            self.console.append(f"<i>Set {axis} {which} limit at angle {angle}°</i>")
            self._update_us_labels()
        except Exception as e:
            self.console.append(f"<span style='color:#c66;'>Limit Error:</span> {e}")

    def _reset_axis_limit(self, *, axis: str) -> None:
        """Reset the min/max µs for the given axis to default values."""
        # Reset to the defaults stored when application started (500/2500)
        # Since we don't persist original defaults separately, we revert to 500/2500.
        try:
            if axis.upper() == 'X':
                idxs = [IDX['LX'], IDX['RX']]
            else:
                idxs = [IDX['LY'], IDX['RY']]
            for i in idxs:
                self.cfg.min_us[i] = 500
                self.cfg.max_us[i] = 2500
                sid = SERVO_IDS[i]
                self._send_line(f"MAP {sid} 500 2500")
            self.console.append(f"<i>Reset {axis} limits to defaults</i>")
            self._update_us_labels()
        except Exception as e:
            self.console.append(f"<span style='color:#c66;'>Reset Error:</span> {e}")

    # ---- Settings dialog ----
    def open_settings(self) -> None:
        dlg = SettingsDialog(self.cfg, self)
        dlg.apply_settings.connect(self._apply_settings)
        dlg.exec()

    @Slot(dict)
    def _apply_settings(self, payload: dict) -> None:
        if payload.get("GET"):
            self._send_line("GET"); return
        try:
            line = json.dumps(payload, separators=(',', ':'))
            self._send_line(line)
        except Exception as e:
            QMessageBox.warning(self, "Settings", f"Failed to send settings: {e}")

    # ---- Save to device ----
    def _save_to_device(self) -> None:
        """Persist the current configuration to EEPROM on the device.

        The firmware supports a simple ``SAVE`` text command which stores
        whatever configuration is currently active.  All configuration
        changes (map/invert/trim/freq/tween) should have been applied
        beforehand via the settings dialog or the dedicated controls.
        """
        try:
            self._send_line("SAVE")
            # Ask for a snapshot after saving to refresh local config
            self._send_line("GET")
        except Exception as e:
            QMessageBox.warning(self, "Save", f"Failed to send SAVE command: {e}")

    # ---- Incoming lines ----
    @Slot(str)
    def on_line(self, line: str) -> None:
        if not line:
            return
        self.console.append(line)
        self._maybe_parse_status(line)

    def _maybe_parse_status(self, line: str) -> None:
        m = re.match(r"Angles tgt/cur:\s+LX=(\d+)/(\d+)\s+LY=(\d+)/(\d+)\s+RX=(\d+)/(\d+)\s+RY=(\d+)/(\d+)", line)
        if m:
            self.cfg.target = [int(m.group(1)), int(m.group(3)), int(m.group(5)), int(m.group(7))]
            self.cfg.current = [int(m.group(2)), int(m.group(4)), int(m.group(6)), int(m.group(8))]
            for i, sid in enumerate(SERVO_IDS):
                t = self.cfg.target[i]
                sl = self.s_sliders[sid]; sp = self.s_spins[sid]
                if sl.value() != t: sl.blockSignals(True); sl.setValue(t); sl.blockSignals(False)
                if sp.value() != t: sp.blockSignals(True); sp.setValue(t); sp.blockSignals(False)
            # Update X/Y paired sliders/spins
            self.x_slider.blockSignals(True); self.x_spin.blockSignals(True)
            self.x_slider.setValue(self.cfg.target[IDX['LX']]); self.x_spin.setValue(self.cfg.target[IDX['LX']])
            self.x_slider.blockSignals(False); self.x_spin.blockSignals(False)
            self.y_slider.blockSignals(True); self.y_spin.blockSignals(True)
            self.y_slider.setValue(self.cfg.target[IDX['LY']]); self.y_spin.setValue(self.cfg.target[IDX['LY']])
            self.y_slider.blockSignals(False); self.y_spin.blockSignals(False)
            self.x_tgt.setText(f"X target: {self.cfg.target[IDX['LX']]}")
            self.y_tgt.setText(f"Y target: {self.cfg.target[IDX['LY']]}")
            self._update_us_labels()
            return
        m = re.match(r"Ranges \(us\):\s+(\d+)-(\d+)\s+(\d+)-(\d+)\s+(\d+)-(\d+)\s+(\d+)-(\d+)", line)
        if m:
            self.cfg.min_us = [int(m.group(1)), int(m.group(3)), int(m.group(5)), int(m.group(7))]
            self.cfg.max_us = [int(m.group(2)), int(m.group(4)), int(m.group(6)), int(m.group(8))]
            self._update_us_labels(); return
        m = re.match(r"Invert:\s+LX=(\d)\s+LY=(\d)\s+RX=(\d)\s+RY=(\d)", line)
        if m:
            self.cfg.invert = [int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))]
            self._update_us_labels(); return
        m = re.match(r"Trim:\s+(-?\d+)\s+(-?\d+)\s+(-?\d+)\s+(-?\d+)", line)
        if m:
            self.cfg.trim_deg = [int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))]
            self._update_us_labels(); return
        m = re.match(r"Freq:\s+(\d+)\s+Hz", line)
        if m:
            self.cfg.freq_hz = int(m.group(1)); return
        m = re.match(r"Tween:\s+stepDeg=(\d+)\s+intervalMs=(\d+)", line)
        if m:
            self.cfg.step_deg = int(m.group(1)); self.cfg.step_ms = int(m.group(2)); return

    # ---- µs labels & visualisation ----
    def _update_us_labels(self) -> None:
        xv = self.x_spin.value(); yv = self.y_spin.value()
        self.x_lx_us.setText(f"LX: {self.cfg.angle_to_us(IDX['LX'], xv)} µs")
        self.x_rx_us.setText(f"RX: {self.cfg.angle_to_us(IDX['RX'], xv)} µs")
        self.y_ly_us.setText(f"LY: {self.cfg.angle_to_us(IDX['LY'], yv)} µs")
        self.y_ry_us.setText(f"RY: {self.cfg.angle_to_us(IDX['RY'], yv)} µs")
        for sid in SERVO_IDS:
            val = self.s_spins[sid].value()
            self.s_us[sid].setText(f"{self.cfg.angle_to_us(IDX[sid], val)} µs")
        # Update visualisers
        lx_angle = self.s_spins['LX'].value(); ly_angle = self.s_spins['LY'].value()
        rx_angle = self.s_spins['RX'].value(); ry_angle = self.s_spins['RY'].value()
        self.left_eye_vis.set_angles(x_angle=lx_angle, y_angle=ly_angle)
        self.right_eye_vis.set_angles(x_angle=rx_angle, y_angle=ry_angle)

    # ---- Joystick support ----
    def _refresh_joystick_devices(self) -> None:
        devices = JoystickHandler.list_devices()
        self.joy_device_box.blockSignals(True); self.joy_device_box.clear(); self.joy_device_box.addItems(devices)
        self.joy_device_box.blockSignals(False)
        # If no devices found disable enable checkbox
        self.joy_enable_cb.setEnabled(bool(devices))
        if not devices:
            self.joy_enable_cb.setChecked(False)

    def _toggle_joystick(self, enabled: bool) -> None:
        if enabled:
            # Start joystick polling
            idx = max(0, self.joy_device_box.currentIndex())
            self.joystick.start(index=idx, sync=self.joy_sync_cb.isChecked())
        else:
            self.joystick.stop()

    def _on_joystick_device_changed(self) -> None:
        if self.joy_enable_cb.isChecked():
            # Restart joystick on new device
            idx = max(0, self.joy_device_box.currentIndex())
            self.joystick.start(index=idx, sync=self.joy_sync_cb.isChecked())

    def _on_joystick_update(self, lx: float, ly: float, rx: float, ry: float) -> None:
        """Update servo sliders/spins when joystick reports new values."""
        # Convert floats to ints (clamp 0–180)
        def clamp_angle(a: float) -> int:
            return max(0, min(180, int(round(a))))
        lx_i = clamp_angle(lx); ly_i = clamp_angle(ly)
        rx_i = clamp_angle(rx); ry_i = clamp_angle(ry)
        # Only apply updates if joystick values have changed since last sample
        last = getattr(self, '_last_joy_angles', None)
        current = (lx_i, ly_i, rx_i, ry_i)
        if last is not None and current == last:
            return
        self._last_joy_angles = current
        # Update left eye controls
        self.s_sliders['LX'].blockSignals(True); self.s_spins['LX'].blockSignals(True)
        self.s_sliders['LX'].setValue(lx_i); self.s_spins['LX'].setValue(lx_i)
        self.s_sliders['LX'].blockSignals(False); self.s_spins['LX'].blockSignals(False)
        self.s_sliders['LY'].blockSignals(True); self.s_spins['LY'].blockSignals(True)
        self.s_sliders['LY'].setValue(ly_i); self.s_spins['LY'].setValue(ly_i)
        self.s_sliders['LY'].blockSignals(False); self.s_spins['LY'].blockSignals(False)
        # Update right eye controls
        self.s_sliders['RX'].blockSignals(True); self.s_spins['RX'].blockSignals(True)
        self.s_sliders['RX'].setValue(rx_i); self.s_spins['RX'].setValue(rx_i)
        self.s_sliders['RX'].blockSignals(False); self.s_spins['RX'].blockSignals(False)
        self.s_sliders['RY'].blockSignals(True); self.s_spins['RY'].blockSignals(True)
        self.s_sliders['RY'].setValue(ry_i); self.s_spins['RY'].setValue(ry_i)
        self.s_sliders['RY'].blockSignals(False); self.s_spins['RY'].blockSignals(False)
        # Also update X/Y paired controls if sync is enabled (any eye will do)
        self.x_slider.blockSignals(True); self.x_spin.blockSignals(True)
        self.x_slider.setValue(lx_i); self.x_spin.setValue(lx_i)
        self.x_slider.blockSignals(False); self.x_spin.blockSignals(False)
        self.y_slider.blockSignals(True); self.y_spin.blockSignals(True)
        self.y_slider.setValue(ly_i); self.y_spin.setValue(ly_i)
        self.y_slider.blockSignals(False); self.y_spin.blockSignals(False)
        self.x_tgt.setText(f"X target: {lx_i}")
        self.y_tgt.setText(f"Y target: {ly_i}")
        # Send commands via queue
        self._queue_cmd('LX', f"SET LX {lx_i}")
        self._queue_cmd('LY', f"SET LY {ly_i}")
        self._queue_cmd('RX', f"SET RX {rx_i}")
        self._queue_cmd('RY', f"SET RY {ry_i}")
        # Request update of microsecond labels and visualisers
        self._update_us_labels()

    # ---- Clean shutdown ----
    def closeEvent(self, e) -> None:  # type: ignore[override]
        try: self.poll.stop(); self.tx_timer.stop()
        except Exception: pass
        try: self.stop_serial.emit()
        except Exception: pass
        # Stop joystick polling
        self.joystick.stop()
        self.thread.quit(); self.thread.wait(1500)
        super().closeEvent(e)


def main() -> int:
    app = QApplication(sys.argv)
    # Apply dark theme
    apply_dark_theme(app)
    w = MainWindow(); w.resize(1100, 900); w.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())