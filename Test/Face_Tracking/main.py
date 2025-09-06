"""Graphical user interface for real-time face tracking and vision LLM chat.

This module defines the main PySide6 application. It integrates webcam
capture, face detection, a multimodal LLM via Ollama, and optional
serial communication to drive servos based on face tracking. Users
can chat with the model, attach the live frame to their prompts, and
adjust settings such as model name, polling intervals, and serial
port configuration from within the interface.
"""

from __future__ import annotations

import sys
import os
from typing import Optional, Tuple

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QLabel, QTextEdit, QLineEdit, QPushButton,
    QVBoxLayout, QHBoxLayout, QGridLayout, QGroupBox, QCheckBox, QSpinBox,
    QComboBox, QMessageBox
)
from PySide6.QtCore import Qt, QTimer, Slot
from PySide6.QtGui import QPixmap, QImage

from webcam import VideoWorker
from llm_client import OllamaClient
from uart import SerialManager
from utils import map_range_clamped


class MainWindow(QMainWindow):
    """Primary window containing the video feed, chat, and controls."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Face Tracking & Vision LLM")
        self.resize(1280, 800)

        # State
        self.last_frame_qimage: Optional[QImage] = None
        self.last_face_center_norm: Tuple[float, float] = (0.5, 0.5)
        self.only_on_user_prompt: bool = True
        self.auto_prompt: str = "Describe what you see in this frame."  # default auto prompt

        # Video display
        self.video_label = QLabel("Waiting for camera...")
        self.video_label.setAlignment(Qt.AlignCenter)
        self.video_label.setMinimumSize(640, 480)
        self.video_label.setStyleSheet(
            "background: #0b0f14; color: #9aa1a9; border: 1px solid #263241;"
        )

        # Coordinates display
        self.coords_label = QLabel("X: -, Y: - (deg)")
        self.coords_label.setStyleSheet("color: #cdd6f4; padding: 4px;")

        # Chat widgets
        self.chat_log = QTextEdit()
        self.chat_log.setReadOnly(True)
        self.chat_input = QLineEdit()
        self.chat_input.setPlaceholderText("Ask the model... (the frame will be attached)")
        self.send_btn = QPushButton("Send")
        self.send_btn.clicked.connect(self.on_send_prompt)
        self.chat_input.returnPressed.connect(self.on_send_prompt)

        chat_layout = QVBoxLayout()
        chat_layout.addWidget(self.chat_log)
        input_row = QHBoxLayout()
        input_row.addWidget(self.chat_input, 1)
        input_row.addWidget(self.send_btn)
        chat_layout.addLayout(input_row)
        chat_group = QGroupBox("Chat with Vision LLM")
        chat_group.setLayout(chat_layout)

        # Controls / settings
        # Model selection
        self.model_edit = QLineEdit("llava")
        self.model_edit.setToolTip("Name of the vision model hosted in Ollama, e.g. 'llava', 'llava:13b', 'qwen2.5vl:7b'")
        # Only prompt on user
        self.only_on_prompt_cb = QCheckBox("Only prompt on user message")
        self.only_on_prompt_cb.setChecked(True)
        self.only_on_prompt_cb.stateChanged.connect(self.on_only_on_prompt_changed)
        # Auto analysis enable
        self.auto_enable_cb = QCheckBox("Automatic frame analysis")
        self.auto_enable_cb.setChecked(False)
        self.auto_enable_cb.stateChanged.connect(self.on_auto_toggle)
        # Interval spin
        self.auto_interval_ms = QSpinBox()
        self.auto_interval_ms.setRange(100, 10000)
        self.auto_interval_ms.setValue(1500)
        self.auto_interval_ms.setSuffix(" ms")
        self.auto_interval_ms.valueChanged.connect(self.on_auto_interval_change)
        # UART controls
        self.uart_enable_cb = QCheckBox("Enable UART output")
        self.uart_enable_cb.setChecked(False)
        # Port selection
        self.port_box = QComboBox()
        self.refresh_ports()
        # Baud selection
        self.baud_box = QComboBox()
        self.baud_box.addItems(["115200", "57600", "38400", "19200", "9600"])
        # Connect/disconnect button
        self.connect_btn = QPushButton("Connect")
        self.connect_btn.setCheckable(True)
        self.connect_btn.toggled.connect(self.on_connect_serial)
        # Inversion toggles
        self.invert_x_cb = QCheckBox("Invert X")
        self.invert_y_cb = QCheckBox("Invert Y")

        # Layout for settings
        ctrl_layout = QGridLayout()
        row = 0
        ctrl_layout.addWidget(QLabel("Model:"), row, 0)
        ctrl_layout.addWidget(self.model_edit, row, 1, 1, 2)
        row += 1
        ctrl_layout.addWidget(self.only_on_prompt_cb, row, 0, 1, 3)
        row += 1
        ctrl_layout.addWidget(self.auto_enable_cb, row, 0)
        ctrl_layout.addWidget(QLabel("Interval:"), row, 1)
        ctrl_layout.addWidget(self.auto_interval_ms, row, 2)
        row += 1
        ctrl_layout.addWidget(QLabel("Port:"), row, 0)
        ctrl_layout.addWidget(self.port_box, row, 1)
        ctrl_layout.addWidget(self.connect_btn, row, 2)
        row += 1
        ctrl_layout.addWidget(QLabel("Baud:"), row, 0)
        ctrl_layout.addWidget(self.baud_box, row, 1)
        ctrl_layout.addWidget(self.uart_enable_cb, row, 2)
        row += 1
        ctrl_layout.addWidget(self.invert_x_cb, row, 0)
        ctrl_layout.addWidget(self.invert_y_cb, row, 1)
        row += 1
        ctrl_group = QGroupBox("Settings")
        ctrl_group.setLayout(ctrl_layout)

        # Assemble layout
        left_col = QVBoxLayout()
        left_col.addWidget(self.video_label, 1)
        left_col.addWidget(self.coords_label)

        right_col = QVBoxLayout()
        right_col.addWidget(chat_group, 3)
        right_col.addWidget(ctrl_group, 1)

        main_layout = QHBoxLayout()
        main_layout.addLayout(left_col, 3)
        main_layout.addLayout(right_col, 2)

        central = QWidget()
        central.setLayout(main_layout)
        self.setCentralWidget(central)

        # Apply simple dark theme to unify look
        self.setStyleSheet(
            """
            QMainWindow {
                background: #0b0f14;
            }
            QGroupBox {
                color: #9dc1ff;
                font-weight: 600;
                border: 1px solid #263241;
                border-radius: 8px;
                margin-top: 10px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                top: -7px;
                background: #0b0f14;
                padding: 0 6px;
            }
            QLabel {
                color: #cdd6f4;
            }
            QLineEdit, QTextEdit, QComboBox, QSpinBox {
                background: #0f141b;
                color: #d1e0ff;
                border: 1px solid #273244;
                border-radius: 6px;
                padding: 6px;
            }
            QPushButton {
                background: #1a2330;
                color: #d7e3ff;
                border: 1px solid #33465e;
                border-radius: 8px;
                padding: 8px 12px;
            }
            QPushButton:hover {
                border-color: #5aa2ff;
            }
            QPushButton:checked {
                background: #213147;
                color: #ffffff;
            }
            QCheckBox {
                color: #c7d3e7;
            }
            """
        )

        # Initialize subsystems
        # Video worker (face detection)
        self.video = VideoWorker(camera_index=0)
        self.video.frame_ready.connect(self.on_frame_ready)
        self.video.face_center_available.connect(self.on_face_center)
        self.video.start()

        # LLM client; supply callback functions
        self.ollama = OllamaClient(
            model=self.model_edit.text().strip(),
            on_stream_delta=self.on_llm_stream,
            on_response_done=self.on_llm_done,
            on_error=self.on_llm_error,
        )

        # Auto analysis timer
        self.auto_timer = QTimer(self)
        self.auto_timer.setInterval(self.auto_interval_ms.value())
        self.auto_timer.timeout.connect(self.on_auto_tick)

        # Serial manager
        self.serial = SerialManager()

    # ---------- Slots and callbacks ----------
    @Slot()
    def on_only_on_prompt_changed(self) -> None:
        self.only_on_user_prompt = self.only_on_prompt_cb.isChecked()

    @Slot()
    def on_auto_toggle(self) -> None:
        if self.auto_enable_cb.isChecked():
            self.auto_timer.start()
        else:
            self.auto_timer.stop()

    @Slot(int)
    def on_auto_interval_change(self, val_ms: int) -> None:
        self.auto_timer.setInterval(val_ms)

    @Slot()
    def on_auto_tick(self) -> None:
        # Auto analysis: send frame with generic prompt
        if self.only_on_user_prompt:
            return
        if self.last_frame_qimage is None:
            return
        if self.ollama.is_busy:
            return
        # Compose generic auto message
        self.append_chat("system", f"[auto] Analyzing frame...")
        self.ollama.set_model(self.model_edit.text().strip())
        self.ollama.send_frame_with_prompt(self.last_frame_qimage, self.auto_prompt)

    @Slot()
    def on_send_prompt(self) -> None:
        text = self.chat_input.text().strip()
        if not text:
            return
        if self.last_frame_qimage is None:
            QMessageBox.warning(self, "No frame", "No webcam frame yet. Please wait a moment.")
            return
        if self.ollama.is_busy:
            QMessageBox.information(self, "Model busy", "The model is still processing the previous request. Please wait.")
            return
        self.append_chat("user", text)
        # Update model if changed
        self.ollama.set_model(self.model_edit.text().strip())
        # Send message with frame
        self.ollama.send_user_message_with_frame(text, self.last_frame_qimage)
        self.chat_input.clear()

    def append_chat(self, role: str, text: str) -> None:
        """Append a message to the chat log with formatting based on role."""
        if role == "user":
            self.chat_log.append(f"<b><span style='color:#98c1ff'>You:</span></b> {text}")
        elif role == "assistant":
            self.chat_log.append(f"<b><span style='color:#a6e3a1'>Assistant:</span></b> {text}")
        elif role == "system":
            self.chat_log.append(f"<i><span style='color:#7f8ea3'>{text}</span></i>")
        else:
            self.chat_log.append(text)
        # Scroll to bottom
        self.chat_log.verticalScrollBar().setValue(self.chat_log.verticalScrollBar().maximum())

    @Slot(QImage, dict)
    def on_frame_ready(self, qimg: QImage, meta: dict) -> None:
        self.last_frame_qimage = qimg
        # Show the frame scaled to the label size
        pix = QPixmap.fromImage(qimg)
        self.video_label.setPixmap(pix.scaled(
            self.video_label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation
        ))

    @Slot(tuple, tuple)
    def on_face_center(self, center_norm_xy: Tuple[float, float], bbox_rel_xywh: Tuple[float, float, float, float]) -> None:
        # Save normalized coordinates for potential use
        cx_norm, cy_norm = center_norm_xy
        self.last_face_center_norm = center_norm_xy
        # Map normalized coordinates to servo degrees with optional inversion
        inv_x = self.invert_x_cb.isChecked()
        inv_y = self.invert_y_cb.isChecked()
        x_deg = int(map_range_clamped(cx_norm, 0.0, 1.0, 180 if inv_x else 0, 0 if inv_x else 180))
        y_deg = int(map_range_clamped(cy_norm, 0.0, 1.0, 180 if inv_y else 0, 0 if inv_y else 180))
        self.coords_label.setText(f"X: {x_deg}, Y: {y_deg} (deg)")
        # Send to UART if enabled
        if self.uart_enable_cb.isChecked() and self.serial.is_open:
            self.serial.send_set_angles(x_deg, y_deg)

    @Slot(bool)
    def on_connect_serial(self, checked: bool) -> None:
        if checked:
            port = self.port_box.currentText().strip()
            try:
                baud = int(self.baud_box.currentText())
            except ValueError:
                baud = 115200
            try:
                self.serial.open(port, baud)
                self.connect_btn.setText("Disconnect")
                self.uart_enable_cb.setChecked(True)
            except Exception as exc:
                self.connect_btn.setChecked(False)
                QMessageBox.critical(self, "Serial Error", str(exc))
        else:
            self.serial.close()
            self.connect_btn.setText("Connect")
            self.uart_enable_cb.setChecked(False)

    def refresh_ports(self) -> None:
        """Refresh the list of available serial ports."""
        try:
            from serial.tools import list_ports
            ports = [p.device for p in list_ports.comports()]
        except Exception:
            ports = []
        # Provide some sensible defaults if no ports discovered
        if not ports:
            ports = ["COM3", "COM4", "/dev/ttyUSB0", "/dev/ttyACM0"]
        self.port_box.clear()
        self.port_box.addItems(ports)

    # LLM callbacks
    def on_llm_stream(self, delta_text: str) -> None:
        """Handle streaming deltas from the LLM. Append to chat log."""
        if not delta_text:
            return
        # Insert incremental text to the last assistant message
        cursor = self.chat_log.textCursor()
        cursor.movePosition(cursor.End)
        cursor.insertText(delta_text)
        self.chat_log.setTextCursor(cursor)

    def on_llm_done(self) -> None:
        """Mark the end of the LLM response."""
        # Insert a newline for separation
        self.chat_log.append("")

    def on_llm_error(self, msg: str) -> None:
        self.append_chat("system", f"[error] {msg}")

    def closeEvent(self, event):
        # Clean up resources when closing the window
        try:
            self.video.stop()
        except Exception:
            pass
        try:
            self.serial.close()
        except Exception:
            pass
        try:
            self.ollama.close()
        except Exception:
            pass
        return super().closeEvent(event)


def main():
    """Entry point for running the application."""
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()