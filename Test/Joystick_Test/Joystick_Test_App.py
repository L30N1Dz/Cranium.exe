"""
Cranium Joystick Test — QtSerialPort Edition
--------------------------------------------
Cross‑platform joystick serial monitor and visualizer.

• Framework: PySide6 (LGPL, royalty‑free)
• Serial: QtSerialPort (PySide6.QtSerialPort)
• Packaging: works with PyInstaller / Nuitka

Expected Arduino line format (per Joystick_Test.ino):
  j1x,j1y,j1btn,j2x,j2y,j2btn

Where buttons are 0/1. BAUD defaults to 9600.
"""
from __future__ import annotations

import sys
import ctypes
import json
import time
from collections import deque
from dataclasses import dataclass

from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtSerialPort import QSerialPort, QSerialPortInfo

APP_TITLE = "Cranium Joystick Test"
DEFAULT_BAUD = 9600
LINE_ENDING = "\n"


@dataclass
class JoystickPacket:
    j1x: int
    j1y: int
    j1b: bool
    j2x: int
    j2y: int
    j2b: bool

    @classmethod
    def parse(cls, line: str) -> "JoystickPacket | None":
        raw = line.strip()
        if not raw:
            return None
        parts = [p.strip() for p in raw.split(",")]
        if len(parts) >= 6:
            try:
                j1x, j1y, j1b, j2x, j2y, j2b = parts[:6]
                return cls(
                    int(float(j1x)),
                    int(float(j1y)),
                    j1b in {"1", "true", "True"},
                    int(float(j2x)),
                    int(float(j2y)),
                    j2b in {"1", "true", "True"},
                )
            except ValueError:
                return None
        try:
            tokens = raw.replace(",", " ").split()
            vals: dict[str, str] = {}
            for tok in tokens:
                if ":" in tok:
                    k, v = tok.split(":", 1)
                    vals[k.lower()] = v
                elif "=" in tok:
                    k, v = tok.split("=", 1)
                    vals[k.lower()] = v
            return cls(
                int(vals.get("j1x", "0")),
                int(vals.get("j1y", "0")),
                vals.get("j1b", "0") in {"1", "true", "True"},
                int(vals.get("j2x", "0")),
                int(vals.get("j2y", "0")),
                vals.get("j2b", "0") in {"1", "true", "True"},
            )
        except Exception:
            return None


class SerialWorker(QtCore.QObject):
    line_received = QtCore.Signal(str)
    packet_received = QtCore.Signal(int, int, bool, int, int, bool)
    pot1_received = QtCore.Signal(int)
    pot2_received = QtCore.Signal(int)
    extra_btn_received = QtCore.Signal(bool)
    cfg_received = QtCore.Signal(dict)
    connected = QtCore.Signal(str)
    disconnected = QtCore.Signal(str)
    error = QtCore.Signal(str)

    def __init__(self):
        super().__init__()
        self._port: QSerialPort | None = None
        self._buffer = QtCore.QByteArray()

    @QtCore.Slot(str, int)
    def open(self, port_name: str, baud: int):
        try:
            if self._port:
                try:
                    if self._port.isOpen():
                        self._port.close()
                except Exception:
                    pass
                try:
                    self._port.readyRead.disconnect(self._on_ready_read)
                except Exception:
                    pass
                self._port.deleteLater()
                self._port = None

            self._port = QSerialPort()
            self._port.setPortName(port_name)
            self._port.setBaudRate(baud)
            self._port.setDataBits(QSerialPort.DataBits.Data8)
            self._port.setParity(QSerialPort.Parity.NoParity)
            self._port.setStopBits(QSerialPort.StopBits.OneStop)
            self._port.setFlowControl(QSerialPort.FlowControl.NoFlowControl)
            self._port.setReadBufferSize(0)
            self._port.readyRead.connect(self._on_ready_read)
            self._port.errorOccurred.connect(self._on_error_occurred)

            if not self._port.open(QSerialPort.OpenModeFlag.ReadWrite):
                self.error.emit(f"Failed to open {port_name}: {self._port.errorString()}")
                return

            self._buffer.clear()
            self.connected.emit(port_name)
            self.send("HELLO")
        except Exception as e:
            self.error.emit(f"Open error: {e}")

    @QtCore.Slot()
    def close(self):
        if not self._port:
            return
        name = self._port.portName()
        try:
            self._port.clear()
            self._port.close()
        except Exception:
            pass
        try:
            self._port.readyRead.disconnect(self._on_ready_read)
        except Exception:
            pass
        self._port.deleteLater()
        self._port = None
        self._buffer.clear()
        self.disconnected.emit(name)

    @QtCore.Slot(str)
    def send(self, text: str):
        if not self._port or not self._port.isOpen():
            self.error.emit("Port not open.")
            return
        try:
            data = (text + LINE_ENDING).encode("utf-8")
            self._port.write(data)
        except Exception as e:
            self.error.emit(f"Write failed: {e}")

    @QtCore.Slot()
    def _on_ready_read(self):
        if not self._port:
            return
        self._buffer += self._port.readAll()
        while True:
            idx_n = self._buffer.indexOf(b"\n")
            idx_r = self._buffer.indexOf(b"\r")
            candidates = [i for i in (idx_n, idx_r) if i >= 0]
            if not candidates:
                break
            idx = min(candidates)
            line = bytes(self._buffer[:idx]).decode("utf-8", errors="ignore").strip()
            self._buffer = self._buffer[idx + 1 :]
            if not line:
                continue
            self.line_received.emit(line)

            if line.startswith("CFG:"):
                try:
                    cfg = json.loads(line[4:].strip())
                    if isinstance(cfg, dict):
                        self.cfg_received.emit(cfg)
                except Exception:
                    pass
                continue

            pkt = JoystickPacket.parse(line)
            if pkt:
                self.packet_received.emit(pkt.j1x, pkt.j1y, pkt.j1b, pkt.j2x, pkt.j2y, pkt.j2b)
                parts = [p.strip() for p in line.split(",")]
                if len(parts) >= 7:
                    eb = parts[6]
                    self.extra_btn_received.emit(eb in {"1", "true", "True"})
                continue

            low = line.lower()

            def _num_after(prefix: str):
                try:
                    return int(float(low.split(prefix, 1)[1]))
                except Exception:
                    return None

            if low.startswith("pot2:"):
                v = _num_after(":")
                if v is not None:
                    self.pot2_received.emit(v)
                    continue
            if low.startswith("pot2="):
                v = _num_after("=")
                if v is not None:
                    self.pot2_received.emit(v)
                    continue
            if low.startswith("pot:"):
                v = _num_after(":")
                if v is not None:
                    self.pot1_received.emit(v)
                    continue
            if low.startswith("pot="):
                v = _num_after("=")
                if v is not None:
                    self.pot1_received.emit(v)
                    continue
            if low.startswith("xbtn:") or low.startswith("extra:") or low.startswith("eb:"):
                try:
                    val = int(float(low.split(":", 1)[1]))
                    self.extra_btn_received.emit(bool(val))
                    continue
                except Exception:
                    pass
            if low.startswith("xbtn=") or low.startswith("extra=") or low.startswith("eb="):
                try:
                    val = int(float(low.split("=", 1)[1]))
                    self.extra_btn_received.emit(bool(val))
                    continue
                except Exception:
                    pass

    @QtCore.Slot(QSerialPort.SerialPortError)
    def _on_error_occurred(self, err: QSerialPort.SerialPortError):
        if err == QSerialPort.SerialPortError.NoError:
            return
        msg = self._port.errorString() if self._port else str(err)
        self.error.emit(f"Serial error: {msg}")


class JoystickView(QtWidgets.QWidget):
    def __init__(self, name: str, parent=None):
        super().__init__(parent)
        self.name = name
        self.setFixedSize(240, 240)
        self._x, self._y = 512, 512
        self._trail_enabled = False
        self._trail = deque(maxlen=40)
        self._trail_base = QtGui.QColor("#9bffd7")

    def set_trail_color(self, color_hex: str):
        c = QtGui.QColor(color_hex)
        if c.isValid():
            self._trail_base = c
            self.update()

    def set_trail_enabled(self, enabled: bool):
        self._trail_enabled = bool(enabled)
        self._trail.clear()
        self.update()

    @QtCore.Slot(int, int)
    def set_position(self, x: int, y: int):
        self._x, self._y = x, y
        if self._trail_enabled:
            self._trail.append((x, y))
        self.update()

    def paintEvent(self, event):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        rect = self.rect()
        p.fillRect(rect, QtGui.QColor("#0a0a0f"))
        p.setPen(QtGui.QPen(QtGui.QColor("#222"), 1))
        step = 20
        for x in range(0, rect.width(), step):
            p.drawLine(x, 0, x, rect.height())
        for y in range(0, rect.height(), step):
            p.drawLine(0, y, rect.width(), y)
        p.setPen(QtGui.QPen(QtGui.QColor("#4cffb5"), 2))
        p.drawRect(rect.adjusted(0, 0, -1, -1))
        p.setPen(QtGui.QPen(QtGui.QColor("#2a8f6b"), 1))
        p.drawLine(rect.center().x(), rect.top(), rect.center().x(), rect.bottom())
        p.drawLine(rect.left(), rect.center().y(), rect.right(), rect.center().y())
        if self._trail_enabled and len(self._trail) >= 2:
            pts = []
            for (tx, ty) in self._trail:
                px = int(tx / 1023 * rect.width())
                py = int(ty / 1023 * rect.height())
                pts.append(QtCore.QPoint(px, py))
            n = len(pts)
            for i in range(1, n):
                alpha = int(180 * (i / n))
                base = self._trail_base
                color = QtGui.QColor(base)
                color.setAlpha(alpha)
                pen = QtGui.QPen(color, 2)
                p.setPen(pen)
                p.drawLine(pts[i - 1], pts[i])
        x = int(self._x / 1023 * rect.width())
        y = int(self._y / 1023 * rect.height())
        p.setPen(QtGui.QPen(QtGui.QColor("#9bffd7"), 2))
        size = 12
        p.drawLine(x - size, y, x + size, y)
        p.drawLine(x, y - size, x, y + size)
        p.setPen(QtGui.QColor("#8cffc8"))
        p.setFont(QtGui.QFont("Consolas", 10, QtGui.QFont.Bold))
        p.drawText(8, 18, self.name)


class ButtonIndicator(QtWidgets.QFrame):
    def __init__(self, label: str, parent=None):
        super().__init__(parent)
        self.setFixedHeight(36)
        self._pressed = False
        self._label = QtWidgets.QLabel(label)
        self._label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._label)
        self._apply_style()

    def set_text(self, text: str):
        self._label.setText(text)

    @QtCore.Slot(bool)
    def set_pressed(self, pressed: bool):
        self._pressed = bool(pressed)
        self._apply_style()

    def _apply_style(self):
        if self._pressed:
            bg = "#4cffb522"
            border = "#4cffb5"
            text = "#d6ffe9"
        else:
            bg = "#140f16"
            border = "#2a2d2f"
            text = "#7fa690"
        self.setStyleSheet(
            "QFrame { background: %s; border: 2px solid %s; border-radius: 8px; }\n"
            "QLabel { color: %s; font-family: Consolas; font-size: 12px; letter-spacing: 1px; }"
            % (bg, border, text)
        )


class PotMeterWidget(QtWidgets.QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(28)
        self.setMinimumHeight(240)
        self._min = 0
        self._max = 1023
        self._value = 0

    def set_range(self, mn: int, mx: int):
        self._min, self._max = mn, mx
        self.update()

    @QtCore.Slot(int)
    def set_value(self, v: int):
        self._value = v
        self.update()

    def paintEvent(self, event):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        r = self.rect()
        p.fillRect(r, QtGui.QColor("#0a0a0f"))
        p.setPen(QtGui.QPen(QtGui.QColor("#4cffb5"), 1))
        p.drawRect(r.adjusted(0, 0, -1, -1))
        rng = max(1, self._max - self._min)
        t = max(0.0, min(1.0, (self._value - self._min) / rng))
        h = int(t * (r.height() - 4))
        bar = QtCore.QRect(r.left() + 4, r.bottom() - 2 - h, r.width() - 8, h)
        p.fillRect(bar, QtGui.QColor("#9bffd7"))


class SettingsDialog(QtWidgets.QDialog):
    config_ready = QtCore.Signal(dict)
    read_requested = QtCore.Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setModal(True)
        self.setMinimumWidth(460)

        title1 = QtWidgets.QLabel("Joystick 1")
        title1.setStyleSheet("color:#8cffc8; font-weight:bold; margin-top:4px;")
        form1 = QtWidgets.QFormLayout()
        form1.setLabelAlignment(QtCore.Qt.AlignmentFlag.AlignRight)
        self.j1x_edit = QtWidgets.QLineEdit()
        self.j1y_edit = QtWidgets.QLineEdit()
        self.j1sw_edit = QtWidgets.QLineEdit()
        self.j1x_edit.setPlaceholderText("e.g., A0 or 34")
        self.j1y_edit.setPlaceholderText("e.g., A1 or 35")
        self.j1sw_edit.setPlaceholderText("e.g., 2")
        form1.addRow("Joy1_X_PIN:", self.j1x_edit)
        form1.addRow("Joy1_Y_PIN:", self.j1y_edit)
        form1.addRow("Joy1_SW_PIN:", self.j1sw_edit)

        self.btn1_label_edit = QtWidgets.QLineEdit()
        self.btn1_label_edit.setPlaceholderText("Button 1")
        form1.addRow("Button1 Label:", self.btn1_label_edit)

        line_div = QtWidgets.QFrame()
        line_div.setFrameShape(QtWidgets.QFrame.Shape.HLine)
        line_div.setStyleSheet("color:#273034;")

        title2 = QtWidgets.QLabel("Joystick 2")
        title2.setStyleSheet("color:#8cffc8; font-weight:bold; margin-top:6px;")
        form2 = QtWidgets.QFormLayout()
        form2.setLabelAlignment(QtCore.Qt.AlignmentFlag.AlignRight)
        self.j2x_edit = QtWidgets.QLineEdit()
        self.j2y_edit = QtWidgets.QLineEdit()
        self.j2sw_edit = QtWidgets.QLineEdit()
        self.j2x_edit.setPlaceholderText("e.g., A2 or 36")
        self.j2y_edit.setPlaceholderText("e.g., A3 or 37")
        self.j2sw_edit.setPlaceholderText("e.g., 3")
        form2.addRow("Joy2_X_PIN:", self.j2x_edit)
        form2.addRow("Joy2_Y_PIN:", self.j2y_edit)
        form2.addRow("Joy2_SW_PIN:", self.j2sw_edit)

        self.btn2_label_edit = QtWidgets.QLineEdit()
        self.btn2_label_edit.setPlaceholderText("Button 2")
        form2.addRow("Button2 Label:", self.btn2_label_edit)

        title_extra = QtWidgets.QLabel("Extra Button")
        title_extra.setStyleSheet("color:#8cffc8; font-weight:bold; margin-top:6px;")
        self.extra_enable = QtWidgets.QCheckBox("Enable Extra Button")
        self.extra_pin_edit = QtWidgets.QLineEdit()
        self.extra_pin_edit.setPlaceholderText("e.g., 4")
        form_extra = QtWidgets.QFormLayout()
        form_extra.setLabelAlignment(QtCore.Qt.AlignmentFlag.AlignRight)
        form_extra.addRow(self.extra_enable)
        form_extra.addRow("Extra_SW_PIN:", self.extra_pin_edit)

        self.extra_label_edit = QtWidgets.QLineEdit()
        self.extra_label_edit.setPlaceholderText("Extra Button")
        form_extra.addRow("Extra Label:", self.extra_label_edit)

        pot1_title = QtWidgets.QLabel("Potentiometer 1")
        pot1_title.setStyleSheet("color:#8cffc8; font-weight:bold; margin-top:6px;")
        self.pot1_enable = QtWidgets.QCheckBox("Enable Pot 1")
        self.pot1_pin_edit = QtWidgets.QLineEdit()
        self.pot1_pin_edit.setPlaceholderText("e.g., A4")
        self.pot1_min_edit = QtWidgets.QLineEdit()
        self.pot1_min_edit.setPlaceholderText("0")
        self.pot1_max_edit = QtWidgets.QLineEdit()
        self.pot1_max_edit.setPlaceholderText("1023")
        form_pot1 = QtWidgets.QFormLayout()
        form_pot1.setLabelAlignment(QtCore.Qt.AlignmentFlag.AlignRight)
        form_pot1.addRow(self.pot1_enable)
        form_pot1.addRow("POT1_PIN:", self.pot1_pin_edit)
        form_pot1.addRow("Pot1_Min:", self.pot1_min_edit)
        form_pot1.addRow("Pot1_Max:", self.pot1_max_edit)

        pot2_title = QtWidgets.QLabel("Potentiometer 2")
        pot2_title.setStyleSheet("color:#8cffc8; font-weight:bold; margin-top:6px;")
        self.pot2_enable = QtWidgets.QCheckBox("Enable Pot 2")
        self.pot2_pin_edit = QtWidgets.QLineEdit()
        self.pot2_pin_edit.setPlaceholderText("e.g., A5")
        self.pot2_min_edit = QtWidgets.QLineEdit()
        self.pot2_min_edit.setPlaceholderText("0")
        self.pot2_max_edit = QtWidgets.QLineEdit()
        self.pot2_max_edit.setPlaceholderText("1023")
        form_pot2 = QtWidgets.QFormLayout()
        form_pot2.setLabelAlignment(QtCore.Qt.AlignmentFlag.AlignRight)
        form_pot2.addRow(self.pot2_enable)
        form_pot2.addRow("POT2_PIN:", self.pot2_pin_edit)
        form_pot2.addRow("Pot2_Min:", self.pot2_min_edit)
        form_pot2.addRow("Pot2_Max:", self.pot2_max_edit)

        def set_enabled_groups():
            self.extra_pin_edit.setEnabled(self.extra_enable.isChecked())
            self.pot1_pin_edit.setEnabled(self.pot1_enable.isChecked())
            self.pot1_min_edit.setEnabled(self.pot1_enable.isChecked())
            self.pot1_max_edit.setEnabled(self.pot1_enable.isChecked())
            self.pot2_pin_edit.setEnabled(self.pot2_enable.isChecked())
            self.pot2_min_edit.setEnabled(self.pot2_enable.isChecked())
            self.pot2_max_edit.setEnabled(self.pot2_enable.isChecked())

        self.extra_enable.toggled.connect(set_enabled_groups)
        self.pot1_enable.toggled.connect(set_enabled_groups)
        self.pot2_enable.toggled.connect(set_enabled_groups)

        report_title = QtWidgets.QLabel("Reporting Mode")
        report_title.setStyleSheet("color:#8cffc8; font-weight:bold; margin-top:6px;")
        self.mode_combo = QtWidgets.QComboBox()
        self.mode_combo.addItems(["stream", "onchange"])
        form_mode = QtWidgets.QFormLayout()
        form_mode.setLabelAlignment(QtCore.Qt.AlignmentFlag.AlignRight)
        form_mode.addRow("Mode:", self.mode_combo)

        invert_title = QtWidgets.QLabel("Axis Invert")
        invert_title.setStyleSheet("color:#8cffc8; font-weight:bold; margin-top:6px;")

        self.j1x_inv = QtWidgets.QCheckBox("Invert J1 X")
        self.j1y_inv = QtWidgets.QCheckBox("Invert J1 Y")
        self.j2x_inv = QtWidgets.QCheckBox("Invert J2 X")
        self.j2y_inv = QtWidgets.QCheckBox("Invert J2 Y")

        invert_grid = QtWidgets.QGridLayout()
        invert_grid.addWidget(self.j1x_inv, 0, 0)
        invert_grid.addWidget(self.j1y_inv, 0, 1)
        invert_grid.addWidget(self.j2x_inv, 1, 0)
        invert_grid.addWidget(self.j2y_inv, 1, 1)

        trail_title = QtWidgets.QLabel("Visual Effects")
        trail_title.setStyleSheet("color:#8cffc8; font-weight:bold; margin-top:6px;")
        self.trail_enable = QtWidgets.QCheckBox("Enable joystick cursor trail")

        btn_box = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok | QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )
        btn_box.accepted.connect(self.accept)
        btn_box.rejected.connect(self.reject)
        self.send_btn = QtWidgets.QPushButton("Send to MCU")
        self.send_btn.clicked.connect(self._emit_send)
        self.read_btn = QtWidgets.QPushButton("Read from MCU")
        self.read_btn.clicked.connect(self._emit_read)

        outer = QtWidgets.QVBoxLayout(self)
        self.setSizeGripEnabled(True)
        self.setMinimumSize(560, 520)  # tweak to taste

        scroller = QtWidgets.QScrollArea()
        scroller.setWidgetResizable(True)
        content = QtWidgets.QWidget()
        content_layout = QtWidgets.QVBoxLayout(content)
        scroller.setWidget(content)

        # Remove white viewport + match dark theme locally
        scroller.setFrameShape(QtWidgets.QFrame.NoFrame)
        scroller.setStyleSheet(
            "QScrollArea { background: transparent; }"
            "QScrollArea > QWidget { background: #0b0f12; }"
            "QScrollArea > QWidget > QWidget { background: #0b0f12; }"
        )
        content.setStyleSheet("background: #0b0f12;")

        content_layout.addWidget(title1)
        content_layout.addLayout(form1)
        content_layout.addWidget(line_div)
        content_layout.addWidget(title2)
        content_layout.addLayout(form2)
        content_layout.addWidget(invert_title)
        content_layout.addLayout(invert_grid)
        content_layout.addWidget(title_extra)
        content_layout.addLayout(form_extra)
        content_layout.addWidget(pot1_title)
        content_layout.addLayout(form_pot1)
        content_layout.addWidget(pot2_title)
        content_layout.addLayout(form_pot2)
        onchg_title = QtWidgets.QLabel("On-change Thresholds")
        onchg_title.setStyleSheet("color:#8cffc8; font-weight:bold; margin-top:6px;")
        self.axis_thresh_edit = QtWidgets.QLineEdit()
        self.axis_thresh_edit.setPlaceholderText("8")
        self.pot_thresh_edit = QtWidgets.QLineEdit()
        self.pot_thresh_edit.setPlaceholderText("8")
        form_onchg = QtWidgets.QFormLayout()
        form_onchg.setLabelAlignment(QtCore.Qt.AlignmentFlag.AlignRight)
        form_onchg.addRow("Axis Δ threshold:", self.axis_thresh_edit)
        form_onchg.addRow("Pot Δ threshold:", self.pot_thresh_edit)
        content_layout.addWidget(onchg_title)
        content_layout.addLayout(form_onchg)
        content_layout.addWidget(report_title)
        content_layout.addLayout(form_mode)
        content_layout.addWidget(trail_title)
        content_layout.addWidget(self.trail_enable)

        self.trail_color_edit = QtWidgets.QLineEdit()
        self.trail_color_edit.setPlaceholderText("#9bffd7")

        # add a pick button + swatch preview
        self.trail_pick_btn = QtWidgets.QToolButton()
        self.trail_pick_btn.setText("Pick")

        self.trail_swatch = QtWidgets.QLabel()
        self.trail_swatch.setFixedSize(22, 22)
        self.trail_swatch.setStyleSheet("background:#9bffd7; border:1px solid #273034; border-radius:4px;")

        row_widget = QtWidgets.QWidget()
        row_h = QtWidgets.QHBoxLayout(row_widget)
        row_h.setContentsMargins(0, 0, 0, 0)
        row_h.setSpacing(6)
        row_h.addWidget(self.trail_color_edit, 1)
        row_h.addWidget(self.trail_pick_btn, 0)
        row_h.addWidget(self.trail_swatch, 0)

        form_trail = QtWidgets.QFormLayout()
        form_trail.setLabelAlignment(QtCore.Qt.AlignmentFlag.AlignRight)
        form_trail.addRow("Trail Color:", row_widget)

        self.trail_pick_btn.clicked.connect(self._pick_trail_color)
        self.trail_color_edit.textChanged.connect(self._update_trail_swatch)

        content_layout.addLayout(form_trail)

        bottom = QtWidgets.QHBoxLayout()
        bottom.addWidget(self.send_btn)
        bottom.addWidget(self.read_btn)
        bottom.addStretch(1)
        bottom.addWidget(btn_box)
        outer.addWidget(scroller, 1)
        outer.addLayout(bottom)

        self.resize(700, 600)

    def set_values(self, settings: dict):
        self.j1x_edit.setText(settings.get("Joy1_X_PIN", ""))
        self.j1y_edit.setText(settings.get("Joy1_Y_PIN", ""))
        self.j1sw_edit.setText(settings.get("Joy1_SW_PIN", ""))
        self.j2x_edit.setText(settings.get("Joy2_X_PIN", ""))
        self.j2y_edit.setText(settings.get("Joy2_Y_PIN", ""))
        self.j2sw_edit.setText(settings.get("Joy2_SW_PIN", ""))
        self.extra_enable.setChecked(bool(settings.get("ExtraBtn_Enabled", False)))
        self.extra_pin_edit.setText(settings.get("Extra_SW_PIN", ""))
        self.pot1_enable.setChecked(bool(settings.get("Pot1_Enabled", False)))
        self.pot1_pin_edit.setText(settings.get("POT1_PIN", ""))
        self.pot1_min_edit.setText(str(settings.get("Pot1_Min", 0)))
        self.pot1_max_edit.setText(str(settings.get("Pot1_Max", 1023)))
        self.pot2_enable.setChecked(bool(settings.get("Pot2_Enabled", False)))
        self.pot2_pin_edit.setText(settings.get("POT2_PIN", ""))
        self.pot2_min_edit.setText(str(settings.get("Pot2_Min", 0)))
        self.pot2_max_edit.setText(str(settings.get("Pot2_Max", 1023)))
        self.mode_combo.setCurrentText(settings.get("Report_Mode", "stream"))
        self.trail_enable.setChecked(bool(settings.get("Trail_Enabled", False)))
        self.extra_pin_edit.setEnabled(self.extra_enable.isChecked())
        self.pot1_pin_edit.setEnabled(self.pot1_enable.isChecked())
        self.pot1_min_edit.setEnabled(self.pot1_enable.isChecked())
        self.pot1_max_edit.setEnabled(self.pot1_enable.isChecked())
        self.pot2_pin_edit.setEnabled(self.pot2_enable.isChecked())
        self.pot2_min_edit.setEnabled(self.pot2_enable.isChecked())
        self.pot2_max_edit.setEnabled(self.pot2_enable.isChecked())
        self.axis_thresh_edit.setText(str(settings.get("OnChange_Threshold_Axis", 8)))
        self.pot_thresh_edit.setText(str(settings.get("OnChange_Threshold_Pot", 8)))
        self.trail_color_edit.setText(settings.get("Trail_Color", "#9bffd7"))
        self._update_trail_swatch()
        self.btn1_label_edit.setText(settings.get("Btn1_Label", "Button 1"))
        self.btn2_label_edit.setText(settings.get("Btn2_Label", "Button 2"))
        self.extra_label_edit.setText(settings.get("Extra_Label", "Extra Button"))
        self.j1x_inv.setChecked(bool(settings.get("Invert_J1X", False)))
        self.j1y_inv.setChecked(bool(settings.get("Invert_J1Y", False)))
        self.j2x_inv.setChecked(bool(settings.get("Invert_J2X", False)))
        self.j2y_inv.setChecked(bool(settings.get("Invert_J2Y", False)))


    def values(self) -> dict:
        return {
            "Joy1_X_PIN": self.j1x_edit.text().strip(),
            "Joy1_Y_PIN": self.j1y_edit.text().strip(),
            "Joy1_SW_PIN": self.j1sw_edit.text().strip(),
            "Joy2_X_PIN": self.j2x_edit.text().strip(),
            "Joy2_Y_PIN": self.j2y_edit.text().strip(),
            "Joy2_SW_PIN": self.j2sw_edit.text().strip(),
            "ExtraBtn_Enabled": self.extra_enable.isChecked(),
            "Extra_SW_PIN": self.extra_pin_edit.text().strip(),
            "Pot1_Enabled": self.pot1_enable.isChecked(),
            "POT1_PIN": self.pot1_pin_edit.text().strip(),
            "Pot1_Min": int(self.pot1_min_edit.text() or 0),
            "Pot1_Max": int(self.pot1_max_edit.text() or 1023),
            "Pot2_Enabled": self.pot2_enable.isChecked(),
            "POT2_PIN": self.pot2_pin_edit.text().strip(),
            "Pot2_Min": int(self.pot2_min_edit.text() or 0),
            "Pot2_Max": int(self.pot2_max_edit.text() or 1023),
            "Report_Mode": self.mode_combo.currentText(),
            "Trail_Enabled": self.trail_enable.isChecked(),
            "OnChange_Threshold_Axis": int(self.axis_thresh_edit.text() or 8),
            "OnChange_Threshold_Pot": int(self.pot_thresh_edit.text() or 8),
            "Trail_Color": self.trail_color_edit.text().strip() or "#9bffd7",
            "Btn1_Label": self.btn1_label_edit.text().strip() or "Button 1",
            "Btn2_Label": self.btn2_label_edit.text().strip() or "Button 2",
            "Extra_Label": self.extra_label_edit.text().strip() or "Extra Button",
            "Invert_J1X": self.j1x_inv.isChecked(),
            "Invert_J1Y": self.j1y_inv.isChecked(),
            "Invert_J2X": self.j2x_inv.isChecked(),
            "Invert_J2Y": self.j2y_inv.isChecked(),
        }

    def _pick_trail_color(self):
        # start from current text or fallback
        start = self.trail_color_edit.text().strip() or "#9bffd7"
        start_q = QtGui.QColor(start) if QtGui.QColor(start).isValid() else QtGui.QColor("#9bffd7")
        col = QtWidgets.QColorDialog.getColor(start_q, self, "Choose Trail Color")
        if col.isValid():
            # normalize to #RRGGBB (no alpha)
            self.trail_color_edit.setText(col.name(QtGui.QColor.HexRgb))

    def _update_trail_swatch(self, *_):
        c = QtGui.QColor(self.trail_color_edit.text().strip())
        if not c.isValid():
            c = QtGui.QColor("#9bffd7")
        self.trail_swatch.setStyleSheet(
            f"background:{c.name(QtGui.QColor.HexRgb)}; border:1px solid #273034; border-radius:4px;"
        )


    def _emit_send(self):
        self.config_ready.emit(self.values())

    def _emit_read(self):
        self.read_requested.emit()

    def showEvent(self, e: QtGui.QShowEvent) -> None:
        enable_dark_title_bar(self)
        return super().showEvent(e)


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_TITLE)
        self.setMinimumWidth(620)
        self.resize(800, 780)
        self.setStyleSheet(self._stylesheet())
        self._settings = QtCore.QSettings("IGBOTO", "CraniumJoystickTest")
        self._cfg = {
            "Joy1_X_PIN": self._settings.value("Joy1_X_PIN", "A0"),
            "Joy1_Y_PIN": self._settings.value("Joy1_Y_PIN", "A1"),
            "Joy1_SW_PIN": self._settings.value("Joy1_SW_PIN", "2"),
            "Joy2_X_PIN": self._settings.value("Joy2_X_PIN", "A2"),
            "Joy2_Y_PIN": self._settings.value("Joy2_Y_PIN", "A3"),
            "Joy2_SW_PIN": self._settings.value("Joy2_SW_PIN", "3"),
            "ExtraBtn_Enabled": self._settings.value("ExtraBtn_Enabled", False, type=bool),
            "Extra_SW_PIN": self._settings.value("Extra_SW_PIN", "4"),
            "Pot1_Enabled": self._settings.value("Pot1_Enabled", False, type=bool),
            "POT1_PIN": self._settings.value("POT1_PIN", "A4"),
            "Pot1_Min": int(self._settings.value("Pot1_Min", 0)),
            "Pot1_Max": int(self._settings.value("Pot1_Max", 1023)),
            "Pot2_Enabled": self._settings.value("Pot2_Enabled", False, type=bool),
            "POT2_PIN": self._settings.value("POT2_PIN", "A5"),
            "Pot2_Min": int(self._settings.value("Pot2_Min", 0)),
            "Pot2_Max": int(self._settings.value("Pot2_Max", 1023)),
            "Report_Mode": self._settings.value("Report_Mode", "stream"),
            "Trail_Enabled": self._settings.value("Trail_Enabled", False, type=bool),
            "Extra_Label": self._settings.value("Extra_Label", "Extra Button"),
            "OnChange_Threshold_Axis": int(self._settings.value("OnChange_Threshold_Axis", 8)),
            "OnChange_Threshold_Pot": int(self._settings.value("OnChange_Threshold_Pot", 8)),
            "Trail_Color": self._settings.value("Trail_Color", "#9bffd7"),
            "Btn1_Label": self._settings.value("Btn1_Label", "Button 1"),
            "Btn2_Label": self._settings.value("Btn2_Label", "Button 2"),
            "Invert_J1X": self._settings.value("Invert_J1X", False, type=bool),
            "Invert_J1Y": self._settings.value("Invert_J1Y", False, type=bool),
            "Invert_J2X": self._settings.value("Invert_J2X", False, type=bool),
            "Invert_J2Y": self._settings.value("Invert_J2Y", False, type=bool),

        }

        central = QtWidgets.QWidget()
        self.setCentralWidget(central)

        self.port_box = QtWidgets.QComboBox()
        self.port_box.setMinimumWidth(220)
        self.baud_box = QtWidgets.QComboBox()
        for b in [9600, 19200, 38400, 57600, 115200]:
            self.baud_box.addItem(str(b))
        self.baud_box.setCurrentText(str(DEFAULT_BAUD))
        self.refresh_btn = QtWidgets.QPushButton("Refresh Ports")
        self.refresh_btn.clicked.connect(self.refresh_ports)

        row1 = QtWidgets.QHBoxLayout()
        row1.setSpacing(8)
        row1.addWidget(QtWidgets.QLabel("Port:"))
        row1.addWidget(self.port_box, 1)
        row1.addSpacing(6)
        row1.addWidget(QtWidgets.QLabel("Baud:"))
        row1.addWidget(self.baud_box)
        row1.addSpacing(6)
        row1.addWidget(self.refresh_btn)

        self.connect_btn = QtWidgets.QPushButton("Connect")
        self.disconnect_btn = QtWidgets.QPushButton("Disconnect")
        self.disconnect_btn.setEnabled(False)
        self.start_btn = QtWidgets.QPushButton("Start")
        self.stop_btn = QtWidgets.QPushButton("Stop")
        self.settings_btn = QtWidgets.QPushButton("Settings")
        self.rate_label = QtWidgets.QLabel("Rate: 0 Hz")
        self.rate_label.setStyleSheet("color:#9bffd7; font-weight:bold;")
        self.status_dot = QtWidgets.QLabel("● DISCONNECTED")
        self._set_status(False)

        row2 = QtWidgets.QHBoxLayout()
        row2.setSpacing(8)
        row2.addWidget(self.connect_btn)
        row2.addWidget(self.disconnect_btn)
        row2.addSpacing(10)
        row2.addWidget(self.start_btn)
        row2.addWidget(self.stop_btn)
        row2.addWidget(self.settings_btn)
        row2.addStretch(1)
        row2.addWidget(self.rate_label)
        row2.addSpacing(12)
        row2.addWidget(self.status_dot)

        self.port_box.currentTextChanged.connect(self._auto_connect_on_select)
        self.connect_btn.clicked.connect(self.connect_serial)
        self.disconnect_btn.clicked.connect(self.disconnect_serial)
        self.start_btn.clicked.connect(lambda: self._send_cmd("START"))
        self.stop_btn.clicked.connect(lambda: self._send_cmd("STOP"))
        self.settings_btn.clicked.connect(self.open_settings)

        self.j1 = JoystickView("Joystick 1")
        self.j2 = JoystickView("Joystick 2")
        self.j1.set_trail_enabled(bool(self._cfg.get("Trail_Enabled", False)))
        self.j2.set_trail_enabled(bool(self._cfg.get("Trail_Enabled", False)))
        self.b1 = ButtonIndicator("Button 1")
        self.b2 = ButtonIndicator("Button 2")

        self.pot1_meter = PotMeterWidget()
        self.pot1_label = QtWidgets.QLabel("POT1")
        self.pot1_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignHCenter)
        self.pot1_label.setStyleSheet("color:#9bffd7;")
        self.pot2_meter = PotMeterWidget()
        self.pot2_label = QtWidgets.QLabel("POT2")
        self.pot2_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignHCenter)
        self.pot2_label.setStyleSheet("color:#9bffd7;")
        self._apply_pot_visibility()

        jbox = QtWidgets.QHBoxLayout()
        jbox.setSpacing(14)
        jbox.addStretch(1)
        pot_col_left = QtWidgets.QVBoxLayout()
        pot_col_left.addWidget(self.pot1_meter, alignment=QtCore.Qt.AlignmentFlag.AlignHCenter)
        pot_col_left.addWidget(self.pot1_label)
        jbox.addLayout(pot_col_left)
        jbox.addSpacing(12)
        left = QtWidgets.QVBoxLayout()
        left.addWidget(self.j1, alignment=QtCore.Qt.AlignmentFlag.AlignHCenter)
        left.addWidget(self.b1)
        middle = QtWidgets.QVBoxLayout()
        middle.addWidget(self.j2, alignment=QtCore.Qt.AlignmentFlag.AlignHCenter)
        middle.addWidget(self.b2)
        jbox.addLayout(left)
        jbox.addSpacing(12)
        jbox.addLayout(middle)
        jbox.addSpacing(12)
        pot_col_right = QtWidgets.QVBoxLayout()
        pot_col_right.addWidget(self.pot2_meter, alignment=QtCore.Qt.AlignmentFlag.AlignHCenter)
        pot_col_right.addWidget(self.pot2_label)
        jbox.addLayout(pot_col_right)
        jbox.addStretch(1)

        self.extra_btn = ButtonIndicator("Extra Button")
        # Trail color
        self.j1.set_trail_color(self._cfg.get("Trail_Color", "#9bffd7"))
        self.j2.set_trail_color(self._cfg.get("Trail_Color", "#9bffd7"))

        # Button labels
        self.b1.set_text(self._cfg.get("Btn1_Label", "Button 1"))
        self.b2.set_text(self._cfg.get("Btn2_Label", "Button 2"))
        self.extra_btn.set_text(self._cfg.get("Extra_Label", "Extra Button"))

        # Equal sizing for all three indicators
        same_w = self.j1.width()  # or use max(self.j1.width(), self.j2.width())
        self.b1.setFixedWidth(same_w)
        self.b2.setFixedWidth(same_w)
        self.extra_btn.setFixedWidth(same_w)
        self.extra_btn_row = QtWidgets.QHBoxLayout()
        self.extra_btn_row.addStretch(1)
        self.extra_btn_row.addWidget(self.extra_btn)
        self.extra_btn_row.addStretch(1)
        self._apply_extra_visibility()

        self.console = QtWidgets.QPlainTextEdit()
        self.console.setReadOnly(True)
        self.console.setPlaceholderText("Serial monitor…")
        self.clear_btn = QtWidgets.QPushButton("Clear Log")
        self.clear_btn.clicked.connect(self.console.clear)

        console_box = QtWidgets.QVBoxLayout()
        console_box.addWidget(self.console, 1)
        console_box.addWidget(self.clear_btn, 0, alignment=QtCore.Qt.AlignmentFlag.AlignRight)

        root = QtWidgets.QVBoxLayout(central)
        root.addLayout(row1)
        root.addLayout(row2)
        root.addSpacing(8)
        root.addLayout(jbox)
        root.addLayout(self.extra_btn_row)
        root.addSpacing(8)
        root.addLayout(console_box, 1)

        self._thread = QtCore.QThread(self)
        self._worker = SerialWorker()
        self._worker.moveToThread(self._thread)
        self._thread.start()

        self._worker.connected.connect(self._on_connected, QtCore.Qt.ConnectionType.QueuedConnection)
        self._worker.disconnected.connect(self._on_disconnected, QtCore.Qt.ConnectionType.QueuedConnection)
        self._worker.line_received.connect(self._on_line, QtCore.Qt.ConnectionType.QueuedConnection)
        self._worker.packet_received.connect(self._on_packet, QtCore.Qt.ConnectionType.QueuedConnection)
        self._worker.pot1_received.connect(self._on_pot1, QtCore.Qt.ConnectionType.QueuedConnection)
        self._worker.pot2_received.connect(self._on_pot2, QtCore.Qt.ConnectionType.QueuedConnection)
        self._worker.extra_btn_received.connect(self._on_extra_btn, QtCore.Qt.ConnectionType.QueuedConnection)
        self._worker.cfg_received.connect(self._on_cfg_from_mcu, QtCore.Qt.ConnectionType.QueuedConnection)
        self._worker.error.connect(self._on_error, QtCore.Qt.ConnectionType.QueuedConnection)

        self.refresh_ports()

        self._port_timer = QtCore.QTimer(self)
        self._port_timer.setInterval(2500)
        self._port_timer.timeout.connect(self._auto_refresh_ports)
        self._port_timer.start()

        self._log_buffer: list[str] = []
        self._log_timer = QtCore.QTimer(self)
        self._log_timer.setInterval(30)
        self._log_timer.timeout.connect(self._flush_log)
        self._log_timer.start()

        self._packet_times = deque(maxlen=2000)
        self._rate_timer = QtCore.QTimer(self)
        self._rate_timer.setInterval(500)
        self._rate_timer.timeout.connect(self._update_rate)
        self._rate_timer.start()

        self._pending_cfg_dialog: SettingsDialog | None = None
        self._cfg_read_timer: QtCore.QTimer | None = None

    def _stylesheet(self) -> str:
        return (
            """
            QMainWindow { background: #0b0f12; }
            QWidget { color: #c9ffe8; font-family: Consolas, monospace; }
            QLabel { color: #9bffd7; }
            QLineEdit { background: #0e1318; color: #c9ffe8; border: 1px solid #273034; border-radius: 8px; padding: 6px; }
            QComboBox {
                background: #0e1318; border: 1px solid #273034; border-radius: 8px; padding: 6px;
                color: #c9ffe8; selection-background-color: #1a222a; selection-color: #c9ffe8;
            }
            QComboBox QAbstractItemView {
                background: #0e1318; color: #c9ffe8; border: 1px solid #273034;
                selection-background-color: #1a222a; selection-color: #c9ffe8;
            }
            QPlainTextEdit { background: #0e1318; color: #baffea; border: 1px solid #273034; border-radius: 8px; }
            QPushButton {
                background: #121821; color: #c9ffe8; border: 1px solid #2b734f; border-radius: 10px; padding: 6px 12px;
            }
            QPushButton:hover { border-color: #4cffb5; }
            QPushButton:pressed { background: #182029; }
            QDialog { background: #0b0f12; }
            QFrame[objectName="divider"] { color: #273034; }
            /* Make scroll areas match the dark theme */
            QScrollArea {
                background: #0b0f12;   /* behind the viewport */
                border: none;
            }
            QScrollArea > QWidget {
                background: #0b0f12;   /* the viewport's child */
            }
            QScrollArea > QWidget > QWidget {
                background: #0b0f12;   /* the auto-generated container inside */
            }
            QScrollBar:vertical, QScrollBar:horizontal { background: #0e1318; }
            QScrollBar::handle { background: #273034; border-radius: 4px; }
            QScrollBar::handle:hover { background: #2b734f; }
            """
        )

    def open_settings(self):
        dlg = SettingsDialog(self)
        dlg.setStyleSheet(self._stylesheet())
        dlg.set_values(self._cfg)
        dlg.config_ready.connect(self._send_config)
        dlg.read_requested.connect(lambda: self._request_cfg_read(dlg))
        if dlg.exec() == QtWidgets.QDialog.DialogCode.Accepted:
            newcfg = dlg.values()
            for k, v in newcfg.items():
                self._settings.setValue(k, v)
            self._cfg.update(newcfg)
            self._apply_pot_visibility()
            self._apply_extra_visibility()
            self.j1.set_trail_enabled(bool(self._cfg.get("Trail_Enabled", False)))
            self.j2.set_trail_enabled(bool(self._cfg.get("Trail_Enabled", False)))
            # Apply updated button labels
            self.b1.set_text(self._cfg.get("Btn1_Label", "Button 1"))
            self.b2.set_text(self._cfg.get("Btn2_Label", "Button 2"))
            self.extra_btn.set_text(self._cfg.get("Extra_Label", "Extra Button"))

            # Apply updated trail color
            self.j1.set_trail_color(self._cfg.get("Trail_Color", "#9bffd7"))
            self.j2.set_trail_color(self._cfg.get("Trail_Color", "#9bffd7"))

    def _send_config(self, cfg: dict):
        payload = {
            "mode": cfg.get("Report_Mode", "stream"),
            "j1": {"x": cfg.get("Joy1_X_PIN"), "y": cfg.get("Joy1_Y_PIN"), "sw": cfg.get("Joy1_SW_PIN")},
            "j2": {"x": cfg.get("Joy2_X_PIN"), "y": cfg.get("Joy2_Y_PIN"), "sw": cfg.get("Joy2_SW_PIN")},
            "extra": {
                "enabled": bool(cfg.get("ExtraBtn_Enabled", False)),
                "sw": cfg.get("Extra_SW_PIN"),
            },
            "pot1": {
                "enabled": bool(cfg.get("Pot1_Enabled", False)),
                "pin": cfg.get("POT1_PIN"),
                "min": int(cfg.get("Pot1_Min", 0)),
                "max": int(cfg.get("Pot1_Max", 1023)),
            },
            "pot2": {
                "enabled": bool(cfg.get("Pot2_Enabled", False)),
                "pin": cfg.get("POT2_PIN"),
                "min": int(cfg.get("Pot2_Min", 0)),
                "max": int(cfg.get("Pot2_Max", 1023)),
            },
            "thresholds": {
                "axis": int(cfg.get("OnChange_Threshold_Axis", 8)),
                "pot": int(cfg.get("OnChange_Threshold_Pot", 8)),
            },
            "invert": {
                "j1x": bool(cfg.get("Invert_J1X", False)),
                "j1y": bool(cfg.get("Invert_J1Y", False)),
                "j2x": bool(cfg.get("Invert_J2X", False)),
                "j2y": bool(cfg.get("Invert_J2Y", False)),
            },
            "save": True  # set True when sending from Settings to persist on MCU
        }
        s = "CFG:" + json.dumps(payload, separators=(",", ":"))
        self._send_cmd(s)

    def _request_cfg_read(self, dlg: SettingsDialog):
        if self._pending_cfg_dialog is not None:
            return
        self._pending_cfg_dialog = dlg
        self._send_cmd("CFG?")
        t = QtCore.QTimer(self)
        t.setSingleShot(True)
        t.setInterval(2000)
        t.timeout.connect(self._cfg_read_timeout)
        t.start()
        self._cfg_read_timer = t

    def _cfg_read_timeout(self):
        if self._pending_cfg_dialog is None:
            return
        self._pending_cfg_dialog.set_values(self._cfg)
        self._pending_cfg_dialog = None
        if self._cfg_read_timer:
            self._cfg_read_timer.deleteLater()
            self._cfg_read_timer = None

    def _on_cfg_from_mcu(self, cfg: dict):
        mapped = self._map_cfg_json(cfg)
        if self._pending_cfg_dialog is not None:
            self._pending_cfg_dialog.set_values(mapped)
            self._pending_cfg_dialog = None
            if self._cfg_read_timer:
                self._cfg_read_timer.stop()
                self._cfg_read_timer.deleteLater()
                self._cfg_read_timer = None
        else:
            self._cfg.update(mapped)
            self._apply_pot_visibility()
            self._apply_extra_visibility()

    def _map_cfg_json(self, cfg: dict) -> dict:
        out = dict(self._cfg)
        try:
            if "mode" in cfg:
                out["Report_Mode"] = str(cfg.get("mode"))
            if "j1" in cfg:
                j1 = cfg.get("j1", {})
                out["Joy1_X_PIN"] = str(j1.get("x", out["Joy1_X_PIN"]))
                out["Joy1_Y_PIN"] = str(j1.get("y", out["Joy1_Y_PIN"]))
                out["Joy1_SW_PIN"] = str(j1.get("sw", out["Joy1_SW_PIN"]))
            if "j2" in cfg:
                j2 = cfg.get("j2", {})
                out["Joy2_X_PIN"] = str(j2.get("x", out["Joy2_X_PIN"]))
                out["Joy2_Y_PIN"] = str(j2.get("y", out["Joy2_Y_PIN"]))
                out["Joy2_SW_PIN"] = str(j2.get("sw", out["Joy2_SW_PIN"]))
            if "extra" in cfg:
                ex = cfg.get("extra", {})
                out["ExtraBtn_Enabled"] = bool(ex.get("enabled", out["ExtraBtn_Enabled"]))
                out["Extra_SW_PIN"] = str(ex.get("sw", out["Extra_SW_PIN"]))
            if "pot1" in cfg:
                p1 = cfg.get("pot1", {})
                out["Pot1_Enabled"] = bool(p1.get("enabled", out["Pot1_Enabled"]))
                out["POT1_PIN"] = str(p1.get("pin", out["POT1_PIN"]))
                out["Pot1_Min"] = int(p1.get("min", out["Pot1_Min"]))
                out["Pot1_Max"] = int(p1.get("max", out["Pot1_Max"]))
            if "pot2" in cfg:
                p2 = cfg.get("pot2", {})
                out["Pot2_Enabled"] = bool(p2.get("enabled", out["Pot2_Enabled"]))
                out["POT2_PIN"] = str(p2.get("pin", out["POT2_PIN"]))
                out["Pot2_Min"] = int(p2.get("min", out["Pot2_Min"]))
                out["Pot2_Max"] = int(p2.get("max", out["Pot2_Max"]))
            if "thresholds" in cfg:
                th = cfg.get("thresholds", {})
                out["OnChange_Threshold_Axis"] = int(th.get("axis", out["OnChange_Threshold_Axis"]))
                out["OnChange_Threshold_Pot"] = int(th.get("pot", out["OnChange_Threshold_Pot"]))
            if "invert" in cfg:
                inv = cfg.get("invert", {})
                out["Invert_J1X"] = bool(inv.get("j1x", out.get("Invert_J1X", False)))
                out["Invert_J1Y"] = bool(inv.get("j1y", out.get("Invert_J1Y", False)))
                out["Invert_J2X"] = bool(inv.get("j2x", out.get("Invert_J2X", False)))
                out["Invert_J2Y"] = bool(inv.get("j2y", out.get("Invert_J2Y", False)))
        except Exception:
            pass
        return out

    def _apply_pot_visibility(self):
        e1 = bool(self._cfg.get("Pot1_Enabled", False))
        e2 = bool(self._cfg.get("Pot2_Enabled", False))
        self.pot1_meter.setVisible(e1)
        self.pot1_label.setVisible(e1)
        self.pot2_meter.setVisible(e2)
        self.pot2_label.setVisible(e2)
        self.pot1_meter.set_range(int(self._cfg.get("Pot1_Min", 0)), int(self._cfg.get("Pot1_Max", 1023)))
        self.pot2_meter.set_range(int(self._cfg.get("Pot2_Min", 0)), int(self._cfg.get("Pot2_Max", 1023)))

    def _apply_extra_visibility(self):
        e = bool(self._cfg.get("ExtraBtn_Enabled", False))
        for i in range(self.extra_btn_row.count()):
            w = self.extra_btn_row.itemAt(i).widget()
            if w:
                w.setVisible(e)

    @QtCore.Slot()
    def refresh_ports(self):
        old_name = self._port_name_from_combo()
        ports = QSerialPortInfo.availablePorts()
        items = []
        for p in ports:
            desc = p.description() or ""
            name = p.portName()
            disp = f"{name} — {desc}" if desc and desc != name else name
            items.append((name, disp))
        self.port_box.blockSignals(True)
        self.port_box.clear()
        for name, disp in items:
            self.port_box.addItem(disp, userData=name)
        if old_name:
            for i in range(self.port_box.count()):
                if self.port_box.itemData(i) == old_name:
                    self.port_box.setCurrentIndex(i)
                    break
        self.port_box.blockSignals(False)

    @QtCore.Slot()
    def _auto_refresh_ports(self):
        old = [self.port_box.itemData(i) for i in range(self.port_box.count())]
        new = [p.portName() for p in QSerialPortInfo.availablePorts()]
        if old != new:
            self.refresh_ports()

    @QtCore.Slot(str)
    def _auto_connect_on_select(self, _display: str):
        if self._port_name_from_combo() and not self._is_connected():
            self.connect_serial()

    def _port_name_from_combo(self) -> str:
        idx = self.port_box.currentIndex()
        if idx < 0:
            return ""
        name = self.port_box.itemData(idx)
        return name or ""

    def _is_connected(self) -> bool:
        return self.disconnect_btn.isEnabled()

    @QtCore.Slot()
    def connect_serial(self):
        name = self._port_name_from_combo()
        if not name:
            self._log("No COM port selected.")
            return
        try:
            baud = int(self.baud_box.currentText())
        except Exception:
            baud = DEFAULT_BAUD
        QtCore.QMetaObject.invokeMethod(
            self._worker,
            "open",
            QtCore.Qt.ConnectionType.QueuedConnection,
            QtCore.Q_ARG(str, name),
            QtCore.Q_ARG(int, baud),
        )

    @QtCore.Slot()
    def disconnect_serial(self):
        self._send_cmd("STOP")
        QtCore.QMetaObject.invokeMethod(
            self._worker, "close", QtCore.Qt.ConnectionType.QueuedConnection
        )

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        try:
            self.disconnect_serial()
            self._thread.quit()
            self._thread.wait(1000)
        finally:
            super().closeEvent(event)

    @QtCore.Slot(str)
    def _on_connected(self, port: str):
        self._set_status(True)
        self.connect_btn.setEnabled(False)
        self.disconnect_btn.setEnabled(True)
        self._log(f"Connected to {port}")
        self._send_cmd("START")

    @QtCore.Slot(str)
    def _on_disconnected(self, port: str):
        self._set_status(False)
        self.connect_btn.setEnabled(True)
        self.disconnect_btn.setEnabled(False)
        self._log(f"Disconnected from {port}")

    @QtCore.Slot(str)
    def _on_line(self, text: str):
        if text.startswith("CFG:"):
            try:
                cfg = json.loads(text[4:].strip())
                self._on_cfg_from_mcu(cfg)
            except Exception:
                pass
        self._log_buffer.append(text)

    @QtCore.Slot(int, int, bool, int, int, bool)
    def _on_packet(self, j1x: int, j1y: int, j1b: bool, j2x: int, j2y: int, j2b: bool):
        self.j1.set_position(j1x, j1y)
        self.j2.set_position(j2x, j2y)
        self.b1.set_pressed(j1b)
        self.b2.set_pressed(j2b)
        self._packet_times.append(time.monotonic())

    @QtCore.Slot(bool)
    def _on_extra_btn(self, pressed: bool):
        if not bool(self._cfg.get("ExtraBtn_Enabled", False)):
            return
        self.extra_btn.set_pressed(bool(pressed))

    @QtCore.Slot(int)
    def _on_pot1(self, value: int):
        if not bool(self._cfg.get("Pot1_Enabled", False)):
            return
        self.pot1_meter.set_value(value)

    @QtCore.Slot(int)
    def _on_pot2(self, value: int):
        if not bool(self._cfg.get("Pot2_Enabled", False)):
            return
        self.pot2_meter.set_value(value)

    @QtCore.Slot(str)
    def _on_error(self, msg: str):
        self._log(f"[ERROR] {msg}")

    def _update_rate(self):
        now = time.monotonic()
        while self._packet_times and (now - self._packet_times[0]) > 1.0:
            self._packet_times.popleft()
        rate = len(self._packet_times)
        self.rate_label.setText(f"Rate: {rate} Hz")

    def _send_cmd(self, text: str):
        timestamp = QtCore.QTime.currentTime().toString("HH:mm:ss")
        self.console.appendPlainText(f"[{timestamp}] → {text}")
        self.console.verticalScrollBar().setValue(self.console.verticalScrollBar().maximum())
        QtCore.QMetaObject.invokeMethod(
            self._worker,
            "send",
            QtCore.Qt.ConnectionType.QueuedConnection,
            QtCore.Q_ARG(str, text),
        )

    def _log(self, s: str):
        timestamp = QtCore.QTime.currentTime().toString("HH:mm:ss")
        self.console.appendPlainText(f"[{timestamp}] {s}")
        self.console.verticalScrollBar().setValue(self.console.verticalScrollBar().maximum())

    def _flush_log(self):
        if not self._log_buffer:
            return
        timestamp = QtCore.QTime.currentTime().toString("HH:mm:ss")
        self.console.appendPlainText("\n".join(f"[{timestamp}] {s}" for s in self._log_buffer))
        self._log_buffer.clear()
        self.console.verticalScrollBar().setValue(self.console.verticalScrollBar().maximum())

    def _set_status(self, ok: bool):
        if ok:
            self.status_dot.setText("● CONNECTED")
            self.status_dot.setStyleSheet("color: #4cffb5; font-weight: bold;")
        else:
            self.status_dot.setText("● DISCONNECTED")
            self.status_dot.setStyleSheet("color: #8b3a3a; font-weight: bold;")


def enable_dark_title_bar(win: QtWidgets.QWidget):
    if sys.platform != "win32":
        return
    try:
        hwnd = int(win.winId())
        DWMWA_USE_IMMERSIVE_DARK_MODE = 20
        DWMWA_USE_IMMERSIVE_DARK_MODE_BEFORE_20H1 = 19
        value = ctypes.c_int(1)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            ctypes.c_void_p(hwnd), ctypes.c_uint(DWMWA_USE_IMMERSIVE_DARK_MODE),
            ctypes.byref(value), ctypes.sizeof(value)
        )
        try:
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                ctypes.c_void_p(hwnd), ctypes.c_uint(DWMWA_USE_IMMERSIVE_DARK_MODE_BEFORE_20H1),
                ctypes.byref(value), ctypes.sizeof(value)
            )
        except Exception:
            pass
    except Exception:
        pass


def main():
    app = QtWidgets.QApplication(sys.argv)
    app.setApplicationName(APP_TITLE)
    win = MainWindow()
    win.show()
    enable_dark_title_bar(win)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
