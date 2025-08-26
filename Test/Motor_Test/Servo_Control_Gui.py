"""
ServoControlGUI_v6.py — PySide6 GUI for animatronic eyes over UART
------------------------------------------------------------------
Why v6?
- FIX: Prior versions ran a blocking while-loop inside a Qt slot on the worker thread.
       That starved the worker's event loop, so queued signals like send_line() never ran.
       Result: no bytes got written (and you saw no hex logs).
- v6 moves serial I/O to a dedicated **Python thread** using a thread-safe **queue.Queue** for TX.
  Qt signals remain responsive.
- Also adds a safe **debug** signal from the worker so hex logs print from the GUI thread.

Other goodies kept:
- Adjustable TX rate (10–200 ms) with coalesced slider commands
- Idle-only GET polling
- EOL selector (LF or CRLF); default LF ("\n")
- Clean shutdown; no lingering QThreads

Install:  pip install PySide6 pyserial
Run:      python ServoControlGUI_v6.py
"""
from __future__ import annotations

import json
import re
import sys
import time
import threading
import queue
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from PySide6.QtCore import QObject, QThread, Signal, Slot, Qt, QTimer
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QComboBox, QGroupBox, QSlider, QSpinBox, QTextEdit, QGridLayout,
    QCheckBox, QFileDialog, QMessageBox, QDialog
)

# -------- Serial backend (pyserial) --------
import serial
import serial.tools.list_ports

BAUD = 115200

SERVO_IDS = ["LX", "LY", "RX", "RY"]
IDX = {"LX":0, "LY":1, "RX":2, "RY":3}

@dataclass
class DeviceConfig:
    min_us: List[int] = field(default_factory=lambda: [500,500,500,500])
    max_us: List[int] = field(default_factory=lambda: [2500,2500,2500,2500])
    trim_deg: List[int] = field(default_factory=lambda: [0,0,0,0])
    invert: List[int] = field(default_factory=lambda: [0,0,1,1])
    freq_hz: int = 50
    step_deg: int = 2
    step_ms: int = 10
    target: List[int] = field(default_factory=lambda: [90,90,90,90])
    current: List[int] = field(default_factory=lambda: [90,90,90,90])

    def angle_to_us(self, idx: int, angle: int) -> int:
        angle = max(0, min(180, int(angle)))
        if self.invert[idx]:
            angle = 180 - angle
        angle = max(0, min(180, angle + int(self.trim_deg[idx])))
        mi, ma = int(self.min_us[idx]), int(self.max_us[idx])
        us = mi + (ma - mi) * angle / 180.0
        return int(round(us))

class SerialWorker(QObject):
    connected = Signal(str)
    disconnected = Signal()
    line_received = Signal(str)
    error = Signal(str)
    debug = Signal(str)  # safe console logs from worker/IO thread

    def __init__(self):
        super().__init__()
        self._ser: Optional[serial.Serial] = None
        self._running = False
        self._txq: "queue.Queue[str]" = queue.Queue()
        self._io_thread: Optional[threading.Thread] = None
        self._log_hex = False

    @Slot(bool)
    def set_hex_logging(self, enabled: bool):
        self._log_hex = bool(enabled)

    @Slot(str)
    def send_line(self, line: str):
        # receives full wire line (with EOL) from GUI thread
        try:
            self._txq.put_nowait(line)
        except Exception as e:
            self.error.emit(f"Queue error: {e}")

    @Slot(str)
    def start(self, port: str):
        try:
            self._ser = serial.Serial(port, BAUD, timeout=0.05)
        except Exception as e:
            self._ser = None
            self.error.emit(f"Open failed: {e}")
            return
        self._running = True
        self.connected.emit(port)

        # spin up python I/O thread
        self._io_thread = threading.Thread(target=self._io_loop, name="SerialIO", daemon=True)
        self._io_thread.start()

    def _io_loop(self):
        buf = bytearray()
        ser = self._ser
        try:
            while self._running and ser and ser.is_open:
                # TX (non-blocking; send all queued quickly)
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

                # RX
                try:
                    chunk = ser.read(256)
                    if chunk:
                        for b in chunk:
                            if b == 10:  # '\n'
                                line = buf.decode(errors="ignore")
                                buf.clear()
                                if line and line[-1] == '\r':
                                    line = line[:-1]
                                self.line_received.emit(line)
                            else:
                                buf.append(b)
                except Exception as e:
                    self.error.emit(f"Read error: {e}")
                    break
        finally:
            # Cleanup
            try:
                if ser and ser.is_open:
                    ser.close()
            except Exception:
                pass
            self._ser = None
            self._running = False
            self.disconnected.emit()

    @Slot()
    def stop(self):
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

# -------- Settings dialog --------
class SettingsDialog(QDialog):
    apply_settings = Signal(dict)  # payload will be JSON dictionary to send

    def __init__(self, cfg: DeviceConfig, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.cfg = cfg

        layout = QVBoxLayout(self)
        grid = QGridLayout()

        # Frequency
        grid.addWidget(QLabel("Frequency (Hz)"), 0,0)
        self.freq = QComboBox(); self.freq.addItems(["50","60"])
        self.freq.setCurrentText(str(cfg.freq_hz))
        grid.addWidget(self.freq, 0,1)

        # Tween
        grid.addWidget(QLabel("Tween step (deg)"), 1,0)
        self.step_deg = QSpinBox(); self.step_deg.setRange(0, 15); self.step_deg.setValue(cfg.step_deg)
        grid.addWidget(self.step_deg, 1,1)
        grid.addWidget(QLabel("Tween interval (ms)"), 1,2)
        self.step_ms = QSpinBox(); self.step_ms.setRange(0, 200); self.step_ms.setValue(cfg.step_ms)
        grid.addWidget(self.step_ms, 1,3)

        # Invert & Trim
        row = 2
        self.inv_boxes: Dict[str,QCheckBox] = {}
        self.trim_boxes: Dict[str,QSpinBox] = {}
        for i,sid in enumerate(SERVO_IDS):
            grid.addWidget(QLabel(f"Invert {sid}"), row, 0)
            cb = QCheckBox(); cb.setChecked(bool(cfg.invert[i])); self.inv_boxes[sid]=cb
            grid.addWidget(cb, row,1)
            grid.addWidget(QLabel(f"Trim {sid} (deg)"), row,2)
            sp = QSpinBox(); sp.setRange(-90,90); sp.setValue(int(cfg.trim_deg[i])); self.trim_boxes[sid]=sp
            grid.addWidget(sp, row,3)
            row += 1

        # uS Map (ALL)
        grid.addWidget(QLabel("Map ALL min (µs)"), row,0)
        self.all_min = QSpinBox(); self.all_min.setRange(200, 3000); self.all_min.setValue(min(cfg.min_us))
        grid.addWidget(self.all_min, row,1)
        grid.addWidget(QLabel("Map ALL max (µs)"), row,2)
        self.all_max = QSpinBox(); self.all_max.setRange(700, 3300); self.all_max.setValue(max(cfg.max_us))
        grid.addWidget(self.all_max, row,3)
        row += 1

        # Toggle for sending trim in JSON
        self.send_trim_cb = QCheckBox("Include trim in JSON payload")
        self.send_trim_cb.setChecked(True)
        layout.addLayout(grid)
        layout.addWidget(self.send_trim_cb)

        # Buttons
        btn_row = QHBoxLayout()
        self.btn_read = QPushButton("Read From Device")
        self.btn_apply = QPushButton("Apply To Device")
        self.btn_save = QPushButton("Save JSON…")
        self.btn_load = QPushButton("Load JSON…")
        btn_row.addWidget(self.btn_read); btn_row.addStretch(1)
        btn_row.addWidget(self.btn_apply); btn_row.addStretch(1)
        btn_row.addWidget(self.btn_save); btn_row.addWidget(self.btn_load)
        layout.addLayout(btn_row)

        # Wire buttons
        self.btn_apply.clicked.connect(self._apply)
        self.btn_read.clicked.connect(lambda: self.apply_settings.emit({"GET": True}))
        self.btn_save.clicked.connect(self._save_json)
        self.btn_load.clicked.connect(self._load_json)

    def _apply(self):
        payload = {
            "freq": int(self.freq.currentText()),
            "tween": {"step_deg": int(self.step_deg.value()), "interval_ms": int(self.step_ms.value())},
            "invert": { sid: int(self.inv_boxes[sid].isChecked()) for sid in SERVO_IDS },
            "map": {"ALL": [ int(self.all_min.value()), int(self.all_max.value()) ]},
            "save": False,
        }
        if self.send_trim_cb.isChecked():
            payload["trim"] = { sid: int(self.trim_boxes[sid].value()) for sid in SERVO_IDS }
        self.apply_settings.emit(payload)

    def _save_json(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save Settings", "settings.json", "JSON (*.json)")
        if not path:
            return
        data = {
            "invert": { sid: int(self.inv_boxes[sid].isChecked()) for sid in SERVO_IDS },
            "map": {"ALL": [ int(self.all_min.value()), int(self.all_max.value()) ]},
            "freq": int(self.freq.currentText()),
            "tween": {"step_deg": int(self.step_deg.value()), "interval_ms": int(self.step_ms.value())},
            "trim": { sid: int(self.trim_boxes[sid].value()) for sid in SERVO_IDS },
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def _load_json(self):
        path, _ = QFileDialog.getOpenFileName(self, "Load Settings", "", "JSON (*.json)")
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if "invert" in data:
                for sid,val in data["invert"].items():
                    if sid in self.inv_boxes: self.inv_boxes[sid].setChecked(bool(val))
            if "map" in data and "ALL" in data["map"] and isinstance(data["map"]["ALL"], list):
                mi,ma = data["map"]["ALL"][0], data["map"]["ALL"][1]
                self.all_min.setValue(int(mi)); self.all_max.setValue(int(ma))
            if "freq" in data:
                self.freq.setCurrentText(str(int(data["freq"])))
            if "tween" in data:
                tw = data["tween"]; self.step_deg.setValue(int(tw.get("step_deg", self.step_deg.value())))
                self.step_ms.setValue(int(tw.get("interval_ms", self.step_ms.value())))
            if "trim" in data:
                for sid,val in data["trim"].items():
                    if sid in self.trim_boxes: self.trim_boxes[sid].setValue(int(val))
        except Exception as e:
            QMessageBox.warning(self, "Load JSON", f"Failed to load: {e}")

# -------- Main window --------
class MainWindow(QMainWindow):
    start_serial = Signal(str)
    send_serial = Signal(str)
    stop_serial = Signal()
    set_hexlog = Signal(bool)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Animatronic Eyes Controller")
        self.cfg = DeviceConfig()

        # Serial worker lives in its own QThread (just for signals),
        # I/O happens in a python thread inside the worker.
        self.worker = SerialWorker()
        self.thread = QThread(self)
        self.worker.moveToThread(self.thread)
        self.thread.start()

        self.start_serial.connect(self.worker.start)
        self.send_serial.connect(self.worker.send_line)
        self.stop_serial.connect(self.worker.stop)
        self.set_hexlog.connect(self.worker.set_hex_logging)

        # UI top row
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

        # TX rate limit (ms)
        self.rate_spin = QSpinBox(); self.rate_spin.setRange(10, 200); self.rate_spin.setValue(30)
        top_l.addWidget(QLabel("TX rate (ms):"))
        top_l.addWidget(self.rate_spin)

        # EOL selection (default LF)
        self.eol_box = QComboBox(); self.eol_box.addItems(["CRLF (\\r\\n)", "LF (\\n)"])
        self.eol_box.setCurrentIndex(1)
        top_l.addWidget(QLabel("EOL:"))
        top_l.addWidget(self.eol_box)

        # Hex logging toggle
        self.hex_cb = QCheckBox("Log TX bytes (hex)")
        self.hex_cb.setChecked(False)
        self.hex_cb.toggled.connect(self.set_hexlog)
        top_l.addWidget(self.hex_cb)

        # X/Y and per-servo groups
        x_group = QGroupBox("X Axis (SET X)"); x_l = QGridLayout(x_group)
        self.x_slider = QSlider(Qt.Horizontal); self.x_slider.setRange(0,180); self.x_slider.setValue(90)
        self.x_spin = QSpinBox(); self.x_spin.setRange(0,180); self.x_spin.setValue(90)
        self.x_lx_us = QLabel("LX: -- µs"); self.x_rx_us = QLabel("RX: -- µs"); self.x_tgt = QLabel("X target: 90")
        x_l.addWidget(self.x_slider, 0,0,1,4)
        x_l.addWidget(QLabel("X:"), 1,0); x_l.addWidget(self.x_spin, 1,1)
        x_l.addWidget(self.x_lx_us, 1,2); x_l.addWidget(self.x_rx_us, 1,3)
        x_l.addWidget(self.x_tgt, 2,0,1,4)

        y_group = QGroupBox("Y Axis (SET Y)"); y_l = QGridLayout(y_group)
        self.y_slider = QSlider(Qt.Horizontal); self.y_slider.setRange(0,180); self.y_slider.setValue(90)
        self.y_spin = QSpinBox(); self.y_spin.setRange(0,180); self.y_spin.setValue(90)
        self.y_ly_us = QLabel("LY: -- µs"); self.y_ry_us = QLabel("RY: -- µs"); self.y_tgt = QLabel("Y target: 90")
        y_l.addWidget(self.y_slider, 0,0,1,4)
        y_l.addWidget(QLabel("Y:"), 1,0); y_l.addWidget(self.y_spin, 1,1)
        y_l.addWidget(self.y_ly_us, 1,2); y_l.addWidget(self.y_ry_us, 1,3)
        y_l.addWidget(self.y_tgt, 2,0,1,4)

        s_group = QGroupBox("Per-Servo Controls (SET <ID>)"); s_l = QGridLayout(s_group)
        self.s_sliders: Dict[str,QSlider] = {}; self.s_spins: Dict[str,QSpinBox] = {}; self.s_us: Dict[str,QLabel] = {}
        row = 0
        for sid in SERVO_IDS:
            s_l.addWidget(QLabel(sid), row, 0)
            sl = QSlider(Qt.Horizontal); sl.setRange(0,180); sl.setValue(90); self.s_sliders[sid]=sl
            sp = QSpinBox(); sp.setRange(0,180); sp.setValue(90); self.s_spins[sid]=sp
            lab = QLabel("-- µs"); self.s_us[sid]=lab
            s_l.addWidget(sl, row, 1); s_l.addWidget(sp, row, 2); s_l.addWidget(lab, row, 3)
            row += 1

        self.console = QTextEdit(); self.console.setReadOnly(True)

        central = QWidget(); cv = QVBoxLayout(central)
        cv.addWidget(top); cv.addWidget(x_group); cv.addWidget(y_group); cv.addWidget(s_group)
        cv.addWidget(QLabel("Device Log:")); cv.addWidget(self.console, 1)
        self.setCentralWidget(central)

        # Wire UI
        self.refresh_btn.clicked.connect(self.refresh_ports)
        self.connect_btn.clicked.connect(self.toggle_connect)
        self.center_btn.clicked.connect(lambda: self._send_line("CENTER"))
        self.settings_btn.clicked.connect(self.open_settings)
        self.save_btn.clicked.connect(lambda: self._send_line("SAVE"))
        self.load_btn.clicked.connect(lambda: self._send_line("LOAD"))
        self.reset_btn.clicked.connect(lambda: self._send_line("RESETCFG"))

        self.x_slider.valueChanged.connect(self._x_slider_changed)
        self.x_spin.valueChanged.connect(self._x_spin_changed)
        self.y_slider.valueChanged.connect(self._y_slider_changed)
        self.y_spin.valueChanged.connect(self._y_spin_changed)
        for sid in SERVO_IDS:
            self.s_sliders[sid].valueChanged.connect(lambda val, s=sid: self._servo_slider_changed(s, val))
            self.s_spins[sid].valueChanged.connect(lambda val, s=sid: self._servo_spin_changed(s, val))

        # Worker signals
        self.worker.connected.connect(self.on_connected)
        self.worker.disconnected.connect(self.on_disconnected)
        self.worker.line_received.connect(self.on_line)
        self.worker.error.connect(self.on_error)
        self.worker.debug.connect(self.console.append)

        # polling & rate limit
        self._last_tx = time.monotonic()
        self.poll = QTimer(self); self.poll.setInterval(1000); self.poll.timeout.connect(self._poll_tick)
        self._pending: Dict[str, str] = {}
        self.tx_timer = QTimer(self); self.tx_timer.setInterval(self.rate_spin.value())
        self.tx_timer.timeout.connect(self._flush_pending); self.tx_timer.start()
        self.rate_spin.valueChanged.connect(self.tx_timer.setInterval)

        self.refresh_ports(); self._update_us_labels()

    # ---- Helpers ----
    def _apply_eol(self, line: str) -> str:
        if self.eol_box.currentIndex() == 0:  # CRLF
            return line.rstrip("\r\n") + "\r\n"
        else:  # LF
            return line.rstrip("\r\n") + "\n"

    # ---- Port handling ----
    def refresh_ports(self):
        cur = self.port_box.currentText()
        self.port_box.blockSignals(True); self.port_box.clear()
        ports = [p.device for p in serial.tools.list_ports.comports()]
        self.port_box.addItems(ports)
        if cur in ports: self.port_box.setCurrentText(cur)
        self.port_box.blockSignals(False)

    def toggle_connect(self):
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
    def on_connected(self, port: str):
        self.status_lbl.setText(f"Connected: {port}")
        self.connect_btn.setText("Disconnect"); self.connect_btn.setEnabled(True)
        self.poll.start()
        # Ask for a snapshot using the current EOL
        self._send_line("GET")

    @Slot()
    def on_disconnected(self):
        self.status_lbl.setText("Disconnected")
        self.connect_btn.setText("Connect"); self.connect_btn.setEnabled(True)
        self.poll.stop()

    @Slot(str)
    def on_error(self, msg: str):
        self.console.append(f"<span style='color:#c66;'>ERROR:</span> {msg}")
        self.status_lbl.setText("Error")
        self.poll.stop()
        self.connect_btn.setText("Connect"); self.connect_btn.setEnabled(True)
        self.stop_serial.emit()

    # ---- Sending & rate limiting ----
    def _queue_cmd(self, key: str, line: str):
        self._pending[key] = line

    def _flush_pending(self):
        if not self._pending: return
        items = list(self._pending.values()); self._pending.clear()
        for line in items: self._send_line(line)

    def _send_line(self, line: str):
        wire = self._apply_eol(line)
        self.send_serial.emit(wire)
        self._last_tx = time.monotonic()
        if line.strip().upper() != "GET":
            self.console.append(f"TX: {line}")

    def _poll_tick(self):
        if (time.monotonic() - getattr(self, "_last_tx", 0)) >= 1.0:
            self._send_line("GET")

    # ---- Paired Sliders/Spins ----
    def _x_slider_changed(self, val: int):
        if self.x_spin.value() != val:
            self.x_spin.blockSignals(True); self.x_spin.setValue(val); self.x_spin.blockSignals(False)
        self.x_tgt.setText(f"X target: {val}")
        self._update_us_labels()
        self._queue_cmd("X", f"SET X {val}")

    def _x_spin_changed(self, val: int):
        if self.x_slider.value() != val:
            self.x_slider.blockSignals(True); self.x_slider.setValue(val); self.x_slider.blockSignals(False)
        self._x_slider_changed(val)

    def _y_slider_changed(self, val: int):
        if self.y_spin.value() != val:
            self.y_spin.blockSignals(True); self.y_spin.setValue(val); self.y_spin.blockSignals(False)
        self.y_tgt.setText(f"Y target: {val}")
        self._update_us_labels()
        self._queue_cmd("Y", f"SET Y {val}")

    def _y_spin_changed(self, val: int):
        if self.y_slider.value() != val:
            self.y_slider.blockSignals(True); self.y_slider.setValue(val); self.y_slider.blockSignals(False)
        self._y_slider_changed(val)

    # ---- Per-servo Sliders/Spins ----
    def _servo_slider_changed(self, sid: str, val: int):
        sp = self.s_spins[sid]
        if sp.value() != val:
            sp.blockSignals(True); sp.setValue(val); sp.blockSignals(False)
        self._update_us_labels()
        self._queue_cmd(sid, f"SET {sid} {val}")

    def _servo_spin_changed(self, sid: str, val: int):
        sl = self.s_sliders[sid]
        if sl.value() != val:
            sl.blockSignals(True); sl.setValue(val); sl.blockSignals(False)
        self._servo_slider_changed(sid, val)

    # ---- Incoming lines ----
    @Slot(str)
    def on_line(self, line: str):
        if not line: return
        self.console.append(line)
        self._maybe_parse_status(line)

    def _maybe_parse_status(self, line: str):
        m = re.match(r"Angles tgt/cur:\s+LX=(\d+)/(\d+)\s+LY=(\d+)/(\d+)\s+RX=(\d+)/(\d+)\s+RY=(\d+)/(\d+)", line)
        if m:
            self.cfg.target = [int(m.group(1)), int(m.group(3)), int(m.group(5)), int(m.group(7))]
            self.cfg.current = [int(m.group(2)), int(m.group(4)), int(m.group(6)), int(m.group(8))]
            for i,sid in enumerate(SERVO_IDS):
                t = self.cfg.target[i]
                sl = self.s_sliders[sid]; sp = self.s_spins[sid]
                if sl.value()!=t: sl.blockSignals(True); sl.setValue(t); sl.blockSignals(False)
                if sp.value()!=t: sp.blockSignals(True); sp.setValue(t); sp.blockSignals(False)
            self.x_slider.blockSignals(True); self.x_spin.blockSignals(True)
            self.x_slider.setValue(self.cfg.target[IDX["LX"]]); self.x_spin.setValue(self.cfg.target[IDX["LX"]])
            self.x_slider.blockSignals(False); self.x_spin.blockSignals(False)
            self.y_slider.blockSignals(True); self.y_spin.blockSignals(True)
            self.y_slider.setValue(self.cfg.target[IDX["LY"]]); self.y_spin.setValue(self.cfg.target[IDX["LY"]])
            self.y_slider.blockSignals(False); self.y_spin.blockSignals(False)
            self.x_tgt.setText(f"X target: {self.cfg.target[IDX['LX']]}")
            self.y_tgt.setText(f"Y target: {self.cfg.target[IDX['LY']]}")
            self._update_us_labels(); return
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
        if m: self.cfg.freq_hz = int(m.group(1)); return
        m = re.match(r"Tween:\s+stepDeg=(\d+)\s+intervalMs=(\d+)", line)
        if m: self.cfg.step_deg = int(m.group(1)); self.cfg.step_ms = int(m.group(2)); return

    # ---- Labels update ----
    def _update_us_labels(self):
        xv = self.x_spin.value(); yv = self.y_spin.value()
        self.x_lx_us.setText(f"LX: {self.cfg.angle_to_us(IDX['LX'], xv)} µs")
        self.x_rx_us.setText(f"RX: {self.cfg.angle_to_us(IDX['RX'], xv)} µs")
        self.y_ly_us.setText(f"LY: {self.cfg.angle_to_us(IDX['LY'], yv)} µs")
        self.y_ry_us.setText(f"RY: {self.cfg.angle_to_us(IDX['RY'], yv)} µs")
        for sid in SERVO_IDS:
            val = self.s_spins[sid].value()
            self.s_us[sid].setText(f"{self.cfg.angle_to_us(IDX[sid], val)} µs")

    # ---- Settings ----
    def open_settings(self):
        dlg = SettingsDialog(self.cfg, self)
        dlg.apply_settings.connect(self._apply_settings)
        dlg.exec()

    @Slot(dict)
    def _apply_settings(self, payload: dict):
        if payload.get("GET"):
            self._send_line("GET"); return
        try:
            line = json.dumps(payload)
            self._send_line(line)
        except Exception as e:
            QMessageBox.warning(self, "Settings", f"Failed to send settings: {e}")

    # ---- Shutdown cleanly ----
    def closeEvent(self, e):
        try: self.poll.stop(); self.tx_timer.stop()
        except Exception: pass
        try: self.stop_serial.emit()
        except Exception: pass
        self.thread.quit(); self.thread.wait(1500)
        super().closeEvent(e)

# -------- main --------
if __name__ == "__main__":
    app = QApplication(sys.argv)
    w = MainWindow(); w.resize(1000, 800); w.show()
    sys.exit(app.exec())
