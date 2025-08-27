"""
settings_dialog.py
===================

Provides a configurable settings dialog used by the main GUI.  Users can
change the update frequency, tweening parameters, inversion flags, trim
offsets and microsecond mapping for each servo.  The dialog can save and
load settings to JSON files for easy reuse.  When the user clicks
``Apply`` the dialog emits a payload dictionary suitable for sending to the
device.

The dialog accepts an existing :class:`DeviceConfig` instance so that it
initialises its controls to the current configuration.
"""

from __future__ import annotations

import json
from typing import Dict

from PySide6.QtCore import Signal, Slot
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QSpinBox,
    QVBoxLayout,
    QPushButton,
    QCheckBox,
    QMessageBox,
)

from serial_worker import DeviceConfig, SERVO_IDS


class SettingsDialog(QDialog):
    """Dialog to adjust device configuration and emit JSON payloads."""

    apply_settings = Signal(dict)

    def __init__(self, cfg: DeviceConfig, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.cfg = cfg
        layout = QVBoxLayout(self)

        # Frequency and tweening controls
        freq_group = QGroupBox("Frequency and Tween")
        freq_grid = QGridLayout(freq_group)
        freq_grid.addWidget(QLabel("Frequency (Hz)"), 0, 0)
        self.freq_box = QComboBox(); self.freq_box.addItems(["50", "60"])
        self.freq_box.setCurrentText(str(cfg.freq_hz))
        freq_grid.addWidget(self.freq_box, 0, 1)
        freq_grid.addWidget(QLabel("Tween step (deg)"), 1, 0)
        self.step_deg = QSpinBox(); self.step_deg.setRange(0, 15); self.step_deg.setValue(cfg.step_deg)
        freq_grid.addWidget(self.step_deg, 1, 1)
        freq_grid.addWidget(QLabel("Tween interval (ms)"), 1, 2)
        self.step_ms = QSpinBox(); self.step_ms.setRange(0, 200); self.step_ms.setValue(cfg.step_ms)
        freq_grid.addWidget(self.step_ms, 1, 3)

        layout.addWidget(freq_group)

        # Invert and trim controls
        inv_group = QGroupBox("Invert and Trim")
        inv_grid = QGridLayout(inv_group)
        self.inv_boxes: Dict[str, QCheckBox] = {}
        self.trim_boxes: Dict[str, QSpinBox] = {}
        row = 0
        for sid in SERVO_IDS:
            inv_grid.addWidget(QLabel(f"Invert {sid}"), row, 0)
            cb = QCheckBox()
            # Inversion is stored as 0/1 per servo
            cb.setChecked(bool(cfg.invert[SERVO_IDS.index(sid)]))
            self.inv_boxes[sid] = cb
            inv_grid.addWidget(cb, row, 1)
            inv_grid.addWidget(QLabel(f"Trim {sid} (deg)"), row, 2)
            sp = QSpinBox(); sp.setRange(-90, 90); sp.setValue(int(cfg.trim_deg[SERVO_IDS.index(sid)]))
            self.trim_boxes[sid] = sp
            inv_grid.addWidget(sp, row, 3)
            row += 1
        layout.addWidget(inv_group)

        # Mapping controls
        map_group = QGroupBox("Mapping (µs)")
        map_grid = QGridLayout(map_group)
        self.map_min: Dict[str, QSpinBox] = {}
        self.map_max: Dict[str, QSpinBox] = {}
        row = 0
        for sid in SERVO_IDS:
            map_grid.addWidget(QLabel(f"{sid} min"), row, 0)
            sp_min = QSpinBox(); sp_min.setRange(200, 3000); sp_min.setValue(int(cfg.min_us[SERVO_IDS.index(sid)]))
            self.map_min[sid] = sp_min
            map_grid.addWidget(sp_min, row, 1)
            map_grid.addWidget(QLabel(f"{sid} max"), row, 2)
            sp_max = QSpinBox(); sp_max.setRange(700, 3300); sp_max.setValue(int(cfg.max_us[SERVO_IDS.index(sid)]))
            self.map_max[sid] = sp_max
            map_grid.addWidget(sp_max, row, 3)
            row += 1
        # Also provide a convenience for setting all min/max at once
        map_grid.addWidget(QLabel("ALL min"), row, 0)
        self.all_min = QSpinBox(); self.all_min.setRange(200, 3000); self.all_min.setValue(min(cfg.min_us))
        map_grid.addWidget(self.all_min, row, 1)
        map_grid.addWidget(QLabel("ALL max"), row, 2)
        self.all_max = QSpinBox(); self.all_max.setRange(700, 3300); self.all_max.setValue(max(cfg.max_us))
        map_grid.addWidget(self.all_max, row, 3)
        layout.addWidget(map_group)

        # Checkbox to include trim values in the payload
        self.send_trim_cb = QCheckBox("Include trim in JSON payload")
        self.send_trim_cb.setChecked(True)
        layout.addWidget(self.send_trim_cb)

        # Buttons row
        btn_row = QHBoxLayout()
        self.btn_read = QPushButton("Read From Device")
        self.btn_apply = QPushButton("Apply To Device")
        self.btn_save = QPushButton("Save JSON…")
        self.btn_load = QPushButton("Load JSON…")
        btn_row.addWidget(self.btn_read); btn_row.addStretch(1)
        btn_row.addWidget(self.btn_apply); btn_row.addStretch(1)
        btn_row.addWidget(self.btn_save); btn_row.addWidget(self.btn_load)
        layout.addLayout(btn_row)

        # Wire signals
        self.btn_apply.clicked.connect(self._apply)
        self.btn_read.clicked.connect(lambda: self.apply_settings.emit({"GET": True}))
        self.btn_save.clicked.connect(self._save_json)
        self.btn_load.clicked.connect(self._load_json)

    def _apply(self) -> None:
        """Assemble a JSON payload from the form controls and emit it."""
        payload: Dict[str, object] = {
            "freq": int(self.freq_box.currentText()),
            "tween": {"step_deg": int(self.step_deg.value()), "interval_ms": int(self.step_ms.value())},
            "invert": {sid: int(self.inv_boxes[sid].isChecked()) for sid in SERVO_IDS},
            "map": {},
            "save": False,
        }
        # Build per‑servo mapping.  If all four have the same min/max we can
        # shorten the JSON using ALL, otherwise specify each one individually.
        same = True
        mins = []
        maxs = []
        for sid in SERVO_IDS:
            mi = int(self.map_min[sid].value())
            ma = int(self.map_max[sid].value())
            mins.append(mi)
            maxs.append(ma)
        if len(set(mins)) == 1 and len(set(maxs)) == 1:
            payload["map"]["ALL"] = [mins[0], maxs[0]]
        else:
            for sid in SERVO_IDS:
                payload["map"][sid] = [int(self.map_min[sid].value()), int(self.map_max[sid].value())]
        # Include trim when requested
        if self.send_trim_cb.isChecked():
            payload["trim"] = {sid: int(self.trim_boxes[sid].value()) for sid in SERVO_IDS}
        self.apply_settings.emit(payload)

    def _save_json(self) -> None:
        """Save the current settings to a JSON file."""
        path, _ = QFileDialog.getSaveFileName(self, "Save Settings", "settings.json", "JSON (*.json)")
        if not path:
            return
        data = {
            "freq": int(self.freq_box.currentText()),
            "tween": {"step_deg": int(self.step_deg.value()), "interval_ms": int(self.step_ms.value())},
            "invert": {sid: int(self.inv_boxes[sid].isChecked()) for sid in SERVO_IDS},
            "map": {},
            "trim": {sid: int(self.trim_boxes[sid].value()) for sid in SERVO_IDS},
        }
        # Save mapping similarly to apply
        mins = [int(self.map_min[sid].value()) for sid in SERVO_IDS]
        maxs = [int(self.map_max[sid].value()) for sid in SERVO_IDS]
        if len(set(mins)) == 1 and len(set(maxs)) == 1:
            data["map"]["ALL"] = [mins[0], maxs[0]]
        else:
            for sid in SERVO_IDS:
                data["map"][sid] = [int(self.map_min[sid].value()), int(self.map_max[sid].value())]
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            QMessageBox.warning(self, "Save JSON", f"Failed to save: {e}")

    def _load_json(self) -> None:
        """Load settings from a JSON file and update the form."""
        path, _ = QFileDialog.getOpenFileName(self, "Load Settings", "", "JSON (*.json)")
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            QMessageBox.warning(self, "Load JSON", f"Failed to open: {e}")
            return
        try:
            if "invert" in data:
                for sid, val in data["invert"].items():
                    if sid in self.inv_boxes:
                        self.inv_boxes[sid].setChecked(bool(val))
            if "map" in data:
                m = data["map"]
                # If ALL is present apply to all, otherwise per id
                if "ALL" in m and isinstance(m["ALL"], list) and len(m["ALL"]) >= 2:
                    mi, ma = int(m["ALL"][0]), int(m["ALL"][1])
                    for sid in SERVO_IDS:
                        self.map_min[sid].setValue(mi)
                        self.map_max[sid].setValue(ma)
                else:
                    for sid in SERVO_IDS:
                        if sid in m and isinstance(m[sid], list) and len(m[sid]) >= 2:
                            mi, ma = int(m[sid][0]), int(m[sid][1])
                            self.map_min[sid].setValue(mi)
                            self.map_max[sid].setValue(ma)
            if "freq" in data:
                self.freq_box.setCurrentText(str(int(data["freq"])))
            if "tween" in data:
                tw = data["tween"]
                if "step_deg" in tw:
                    self.step_deg.setValue(int(tw["step_deg"]))
                if "interval_ms" in tw:
                    self.step_ms.setValue(int(tw["interval_ms"]))
            if "trim" in data:
                t = data["trim"]
                for sid in SERVO_IDS:
                    if sid in t:
                        self.trim_boxes[sid].setValue(int(t[sid]))
        except Exception as e:
            QMessageBox.warning(self, "Load JSON", f"Invalid format: {e}")